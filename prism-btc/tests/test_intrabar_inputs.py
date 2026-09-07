"""Offline source integrity and native closed-feature parity checks."""
import hashlib
import sqlite3

import pandas as pd
import pytest

from analysis import intrabar_inputs as inputs
from analysis.replay_data import LoadedBars, LoadedFunding, TIMEFRAME_MS
from backtest import engine
from core.entries import EntryInputs
from core.market_regimes import volatility_sequence
from engine.indicators import add_indicators, atr


def mark_db(tmp_path, rows=None):
    path = tmp_path / "marks.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE mark_klines(timeframe,open_time,open,high,low,close,confirmed)")
        conn.executemany("INSERT INTO mark_klines VALUES(?,?,?,?,?,?,?)", rows if rows is not None else
                         [("5m", stamp, 100, 102, 98, 101, 1) for stamp in (0, 300_000, 600_000)])
    return path


def test_mark_complete_canonical_hash_readonly_and_no_volume(tmp_path):
    path = mark_db(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = inputs.load_mark_bars(path, 0, 900_000)
    assert len(result.rows) == result.manifest["expected_count"] == 3
    assert result.manifest["source_kind"] == "MARK_PRICE_OHLC"
    assert "volume" not in result.rows[0]
    assert result.manifest == inputs.load_mark_bars(path, 0, 900_000).manifest
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("field,value", [(1, 1), (1, .1), (2, "100"), (2, None),
    (2, float("inf")), (2, float("nan")), (2, 0), (2, 105), (3, 97), (4, 105),
    (5, 97), (6, 0), (6, 1.0)])
def test_bad_mark_rejected(tmp_path, field, value):
    row = ["5m", 0, 100, 102, 98, 101, 1]
    row[field] = value
    with pytest.raises(ValueError):
        inputs.load_mark_bars(mark_db(tmp_path, [row]), 0, 300_000)


@pytest.mark.parametrize("rows", [[], [("5m", 0, 100, 102, 98, 101, 1)] * 2])
def test_mark_missing_duplicate_rejected(tmp_path, rows):
    with pytest.raises(ValueError):
        inputs.load_mark_bars(mark_db(tmp_path, rows), 0, 300_000)


@pytest.mark.parametrize("start,end", [(True, 300_000), (0, False), (0, 300_001)])
def test_mark_invalid_bounds_do_not_create_database(tmp_path, start, end):
    with pytest.raises(ValueError):
        inputs.load_mark_bars(tmp_path / "absent.db", start, end)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "confirmed", "open_time"])
def test_boolean_values_rejected_before_normalization(monkeypatch, field):
    # SQLite stores Python bool as integer; exercise the reader boundary itself.
    row = dict(timeframe="5m", open_time=0, open=100., high=102., low=98., close=101., confirmed=1)
    row[field] = True
    monkeypatch.setattr(inputs, "_read", lambda *args: [row])
    with pytest.raises(ValueError):
        inputs.load_mark_bars("unused", 0, 300_000)


def test_mark_hash_tracks_content_not_filename_or_mtime(tmp_path):
    path = mark_db(tmp_path)
    before = inputs.load_mark_bars(path, 0, 900_000).manifest["content_sha256"]
    path.touch()
    assert inputs.load_mark_bars(path, 0, 900_000).manifest["content_sha256"] == before
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE mark_klines SET close=100 WHERE open_time=0")
    assert inputs.load_mark_bars(path, 0, 900_000).manifest["content_sha256"] != before


CUTOFF = int(pd.Timestamp("2023-01-02", tz="UTC").value // 1_000_000)


def frames():
    result = {}
    for tf in engine.ALL_TFS:
        duration = TIMEFRAME_MS[tf]
        values = [100. + i for i in range(85)]
        frame = pd.DataFrame({"open": values, "high": [v + 2 for v in values],
            "low": [v - 2 for v in values], "close": values, "volume": 1., "turnover": 100.})
        frame.index = pd.to_datetime([CUTOFF + (i - 80) * duration for i in range(85)], unit="ms", utc=True)
        result[tf] = add_indicators(frame)
    return result


def bundle(tf=None, volatility=()):
    return inputs.InputBundle((), (), None, frames() if tf is None else tf, {}, volatility)


def test_context_equals_native_snapshot_and_direct_entry_helpers():
    source = frames()
    context = bundle(source).context(CUTOFF)
    stamp = pd.Timestamp(CUTOFF, unit="ms", tz="UTC")
    assert context["snapshot"] == engine._build_snapshot_at(source, stamp)
    hour = engine._get_tf_slice(source, stamp, "1h")
    half = engine._get_tf_slice(source, stamp, "30m")
    for side, ref in (("long", hour.low.iloc[-10:].min()), ("short", hour.high.iloc[-10:].max())):
        assert context["entry_inputs"][side] == EntryInputs(float(half.close.iloc[-1]),
            float(atr(hour).iloc[-1]), float(ref), float(hour.close.rolling(35).mean().iloc[-1]))
    assert context["latest4h"]["close_ts"] == CUTOFF
    assert context["previous4h"]["close_ts"] == CUTOFF - TIMEFRAME_MS["4h"]
    assert context["trailing_ma12h"] == float(engine._get_tf_slice(source, stamp, "12h").close.rolling(10).mean().iloc[-1])


def test_five_minute_context_reuses_prior_boundary_not_current_candle():
    data = bundle()
    baseline = data.context(CUTOFF)
    for offset in (0, 300_000, 600_000, 1_500_000):
        assert data.context(CUTOFF + offset) is baseline
        assert baseline["feature_cutoff_ms"] <= CUTOFF + offset
    assert baseline["prior_close30m"] == 179.
    assert data.context(CUTOFF + 1_800_000)["prior_close30m"] == 180.


def test_future_htf_prices_preserve_snapshot_and_entry_prefix():
    source = frames()
    baseline = bundle(source).context(CUTOFF)
    changed = {}
    for tf, frame in source.items():
        raw = frame.drop(columns=["ma10", "ma35", "atr14"]).copy()
        raw.loc[raw.index >= pd.Timestamp(CUTOFF, unit="ms", tz="UTC"), ["open", "high", "low", "close"]] *= 5
        changed[tf] = add_indicators(raw)
    assert baseline == bundle(changed).context(CUTOFF)


def test_missing_native_history_no_synthetic_entry_or_range():
    short = {tf: frame.iloc[:1] for tf, frame in frames().items()}
    data = bundle(short)
    assert data.context(CUTOFF)["entry_inputs"] == {"long": None, "short": None}
    assert data.context(CUTOFF)["snapshot"] is None
    assert data.regime_at(CUTOFF)["label"] == "unknown"


def test_regime_does_not_use_current_five_minute_shock():
    frame = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100.},
        index=pd.to_datetime([CUTOFF + (i - 20) * 300_000 for i in range(25)], unit="ms", utc=True))
    frame.loc[frame.index[20], "high"] = 150.
    data = bundle(volatility=volatility_sequence(frame))
    assert data.regime_at(CUTOFF)["volatility"] == "normal"
    assert data.regime_at(CUTOFF + 300_000)["volatility"] == "shock"
    assert data.regime_at(CUTOFF + 300_000)["shock_event_ms"] == CUTOFF + 300_000


def test_prepare_contract_shifted_volume_and_manifest(monkeypatch):
    calls = []

    def fake_bars(path, tf, start, end):
        calls.append((path, tf, start, end))
        times = [inputs.START - 300_000, inputs.START, inputs.START + 300_000]
        return LoadedBars(tuple({"open_time": ts, "open": 100., "high": 101., "low": 99.,
            "close": 100., "volume": i * 10., "turnover": 0.} for i, ts in enumerate(times)), {"count": 3})

    monkeypatch.setattr(inputs, "load_bars", fake_bars)
    monkeypatch.setattr(inputs, "load_funding", lambda path, start, end, interval:
        LoadedFunding(({"funding_time": start, "rate": -.001},), {"interval_ms": interval}))
    actual = inputs.prepare_inputs("market", "trades")
    assert [row["prior_volume"] for row in actual.bars] == [0., 10.]
    assert [row["volume"] for row in actual.bars] == [10., 20.]
    assert actual.mark_bars is None
    assert actual.manifest["mark"] == {"status": "MARK_MISSING", "proxy": "LAST_PROXY"}
    assert actual.manifest["financial_bar_count"] == 2
    assert actual.manifest["warmup_bar_count"] == 1
    assert actual.manifest["funding"]["interval_ms"] == 28_800_000
    assert actual.funding == ({"ts": inputs.START, "rate": -.001},)
    assert ("trades", "5m", inputs.TRADE_WARMUP, inputs.END) in calls
    assert ("market", "1w", inputs.WARMUP, inputs.END - 259_200_000) in calls
    assert (inputs.END - inputs.START) // 300_000 == 420_768
    assert (inputs.START - inputs.TRADE_WARMUP) // 300_000 == 232
    monkeypatch.setattr(inputs, "load_mark_bars", lambda path: LoadedBars(
        ({"open_time": inputs.START, "open": 100., "high": 101., "low": 99., "close": 100.},),
        {"source_kind": "MARK_PRICE_OHLC"}))
    with_mark = inputs.prepare_inputs("market", "trades", "marks")
    assert with_mark.mark_bars == ({"ts": inputs.START, "open": 100., "high": 101., "low": 99., "close": 100.},)
    assert with_mark.manifest["mark"] == {"source_kind": "MARK_PRICE_OHLC"}
