"""Real provider recovery without network, broker, or production cache writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from threading import Barrier
from types import SimpleNamespace

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from prism_core.us_daily_cache import DailyBarCache


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv('US_DAILY_CACHE_ENABLED', 'true')
    monkeypatch.setenv('PRISM_US_DAILY_CACHE_DIR', str(tmp_path))
    old = sys.path[:]
    spec = importlib.util.spec_from_file_location('cache_provider', Path(__file__).resolve().parents[1]/'prism-us/cores/us_surge_detector.py')
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    monkeypatch.setattr(p.yf, 'download', lambda *a, **kw: pytest.fail('Unexpected bulk request'))
    monkeypatch.setattr(p.yf, 'Ticker', lambda *a, **kw: pytest.fail('Unexpected single request'))
    yield p
    sys.path[:] = old


def history():
    return pd.DataFrame({'Open': [10.,11.,12.], 'High':[13.,14.,15.],
                         'Low':[9.,10.,11.], 'Close':[12.,13.,14.],
                         'Volume':[1e6,2e6,3e6]},
                        index=pd.to_datetime(['2026-09-21','2026-09-22','2026-09-23']))


def test_cache_recovers_only_missing_exact_previous_without_requests(provider, tmp_path):
    observed = datetime(2026,9,23,22,tzinfo=timezone.utc)
    cache = DailyBarCache(tmp_path, now=datetime(2026,9,22,22,tzinfo=timezone.utc))
    cache.save_history('AAA', history())
    damaged = history(); damaged.loc['2026-09-22',:] = float('nan')
    result = provider._recover_daily_snapshot(damaged,'20260922',['AAA'],observed)
    assert result.loc['AAA','Close'] == 13
    assert result.attrs['snapshot_coverage']['sources'] == {'AAA':'cache'}
    assert result.attrs['snapshot_coverage']['retry_attempts'] == 0
    assert result.attrs['snapshot_coverage']['provider_reason_counts'] == {'invalid_ohlcv':1}


def test_new_valid_provider_wins_over_cache(provider, tmp_path):
    cache=DailyBarCache(tmp_path,now=datetime(2026,9,22,22,tzinfo=timezone.utc))
    cache.save_history('AAA',history())
    raw=history();raw.loc['2026-09-22','Close']=13.5
    result=provider._recover_daily_snapshot(raw,'20260922',['AAA'],datetime(2026,9,23,22,tzinfo=timezone.utc))
    assert result.loc['AAA','Close']==13.5
    assert result.attrs['snapshot_coverage']['sources']=={'AAA':'provider'}


@pytest.mark.parametrize('changed_basis',[False,True])
def test_retry_must_match_original_adjustment_anchors(provider, monkeypatch, changed_basis):
    raw=history();raw.loc['2026-09-22',:]=float('nan')
    retry=history()
    if changed_basis: retry.loc['2026-09-21', ['Open','High','Low','Close']] *= .5
    calls=[]
    def fetch(**kwargs):
        calls.append(kwargs)
        return retry
    monkeypatch.setattr(provider.yf,'Ticker',lambda _:SimpleNamespace(history=fetch))
    result=provider._recover_daily_snapshot(raw,'20260922',['AAA'],datetime(2026,9,23,22,tzinfo=timezone.utc))
    assert len(calls)==1
    assert calls[0]['repair'] is False and calls[0]['auto_adjust'] is True
    assert result.empty is changed_basis


def test_rate_limit_stops_remaining_single_requests(provider, monkeypatch):
    raw=history();raw.loc['2026-09-22',:]=float('nan')
    raw=pd.concat({'AAA':raw,'BBB':raw},axis=1)
    calls=[]
    def fetch(**kwargs):
        calls.append(kwargs)
        raise YFRateLimitError()
    monkeypatch.setattr(provider.yf,'Ticker',lambda _:SimpleNamespace(history=fetch))
    result=provider._recover_daily_snapshot(raw,'20260922',['AAA','BBB'],datetime(2026,9,23,22,tzinfo=timezone.utc))
    assert result.empty and len(calls)==1
    assert result.attrs['snapshot_coverage']['retry_stop_reason']=='rate_limit'


def test_bulk_rate_limit_diagnostic_prevents_additional_retry(provider, monkeypatch):
    raw=history();raw.loc['2026-09-22',:]=float('nan')
    raw.attrs['recovery_retry_suppressed'] = True
    result=provider._recover_daily_snapshot(raw,'20260922',['AAA'],datetime(2026,9,23,22,tzinfo=timezone.utc))
    assert result.empty
    assert result.attrs['snapshot_coverage']['retry_attempts']==0


def test_real_bulk_api_swallowed_rate_limit_is_detected(provider, monkeypatch):
    import yfinance.multi as multi
    # Restore the actual public bulk implementation, replace only its network
    # ticker boundary. No private error dictionary is assumed by the adapter.
    monkeypatch.setattr(provider.yf, 'download', multi.download)
    def fail(**kwargs):
        raise YFRateLimitError()
    monkeypatch.setattr(multi, 'Ticker', lambda _: SimpleNamespace(history=fail))
    data = provider._download_daily(['AAA'], start='2026-09-21', end='2026-09-24',
                                    threads=False, progress=False)
    assert data.empty and data.attrs['recovery_retry_suppressed'] is True


def test_global_logging_disable_suppresses_unobservable_retries(provider,monkeypatch):
    import logging
    monkeypatch.setattr(provider.yf,'download',lambda *a,**kw:history())
    previous=logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        result=provider._download_daily(['AAA'])
    finally:
        logging.disable(previous)
    assert result.attrs['recovery_retry_suppressed'] is True


def test_yfinance_pass_through_formatter_does_not_disable_retry(provider,monkeypatch):
    import logging
    from yfinance.utils import YFLogFormatter
    yf_logger=logging.getLogger('yfinance')
    formatter=YFLogFormatter()
    monkeypatch.setattr(provider.yf,'download',lambda *a,**kw:history())
    yf_logger.addFilter(formatter)
    try:
        result=provider._download_daily(['AAA'])
    finally:
        yf_logger.removeFilter(formatter)
    assert result.attrs['recovery_retry_suppressed'] is False


def test_same_key_concurrent_pairs_do_not_exchange_histories(provider, monkeypatch):
    monkeypatch.setenv('US_DAILY_CACHE_ENABLED','false')
    monkeypatch.setattr(provider,'get_last_trading_day',lambda _:pd.Timestamp('2026-09-22').date())
    barrier=Barrier(2)
    from threading import local
    values=local()
    def download(*args,**kwargs):
        return history()*values.factor
    monkeypatch.setattr(provider.yf,'download',download)
    def run(factor):
        values.factor=factor
        current=provider.get_snapshot('20260923',['AAA'])
        barrier.wait(timeout=5)
        previous,_=provider.get_previous_snapshot('20260923',['AAA'])
        return current.loc['AAA','Close'],previous.loc['AAA','Close']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run,[1,2]))==[(14.,13.),(28.,26.)]


@pytest.mark.parametrize('changed_basis',[False,True])
def test_expired_memory_pair_checks_new_response_basis(provider, monkeypatch, changed_basis):
    monkeypatch.setenv('US_DAILY_CACHE_ENABLED','false')
    monkeypatch.setattr(provider,'get_last_trading_day',lambda _:pd.Timestamp('2026-09-22').date())
    monkeypatch.setattr(provider.time,'monotonic',lambda:100.)
    fresh=history()*(2 if changed_basis else 1)
    responses=iter([history(),fresh])
    monkeypatch.setattr(provider.yf,'download',lambda *a,**kw:next(responses))
    provider.get_snapshot('20260923',['AAA'])
    monkeypatch.setenv('US_DAILY_CACHE_ENABLED','true')
    monkeypatch.setattr(provider.time,'monotonic',lambda:200.)
    monkeypatch.setattr(provider.yf,'Ticker',lambda _:SimpleNamespace(history=lambda **kw:pd.DataFrame()))
    previous,_=provider.get_previous_snapshot('20260923',['AAA'])
    assert previous.empty is changed_basis


def test_retry_count_is_bounded(provider,monkeypatch):
    damaged=history();damaged.loc['2026-09-22',:]=float('nan')
    tickers=[f'A{i}' for i in range(12)]
    raw=pd.concat({t:damaged for t in tickers},axis=1)
    calls=[]
    def fetch(**kwargs):
        calls.append(kwargs);return pd.DataFrame()
    monkeypatch.setattr(provider.yf,'Ticker',lambda _:SimpleNamespace(history=fetch))
    result=provider._recover_daily_snapshot(raw,'20260922',tickers,datetime(2026,9,23,22,tzinfo=timezone.utc))
    assert result.empty and len(calls)==10
    assert result.attrs['snapshot_coverage']['retry_stop_reason']=='retry_count_budget'


def test_late_retry_is_not_accepted(provider,monkeypatch):
    raw=history();raw.loc['2026-09-22',:]=float('nan')
    clock=[0.]
    monkeypatch.setattr(provider.time,'monotonic',lambda:clock[0])
    def fetch(**kwargs):
        clock[0]=16.;return history()
    monkeypatch.setattr(provider.yf,'Ticker',lambda _:SimpleNamespace(history=fetch))
    result=provider._recover_daily_snapshot(raw,'20260922',['AAA'],datetime(2026,9,23,22,tzinfo=timezone.utc))
    assert result.empty
    assert result.attrs['snapshot_coverage']['retry_stop_reason']=='retry_time_budget'


def test_cache_io_failure_keeps_valid_provider_result(provider,monkeypatch):
    def fail(*a,**kw):raise OSError('fixture')
    monkeypatch.setattr(DailyBarCache,'save_history',fail)
    result=provider._recover_daily_snapshot(history(),'20260922',['AAA'],datetime(2026,9,23,22,tzinfo=timezone.utc))
    assert result.loc['AAA','Close']==13.
    assert result.attrs['snapshot_coverage']['sources']=={'AAA':'provider'}


def test_marked_repaired_target_is_not_accepted(provider):
    raw=history();raw['Repaired?']=False;raw.loc['2026-09-22','Repaired?']=True
    result=provider._snapshot_from_history(raw,'20260922',['AAA'])
    assert result.empty
    assert result.attrs['snapshot_coverage']['reason_counts']=={'repaired_ohlcv_rejected':1}
