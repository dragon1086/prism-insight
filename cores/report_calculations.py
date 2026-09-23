"""Pure report arithmetic over captured market data; no scores or trade decisions."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date

import pandas as pd

from cores.market_data.remote_source import decode_result, encode_result
from prism_core.kr_flow_evidence import _quantity, compute_kr_flow_evidence

VERSION = 'report-calculations-v1'


def compute_prefetched_report_metrics(captured, *, ticker, reference_date, asof_utc):
    """Adapt the four existing provider responses without fetching or mutating them."""
    captures = []
    for key, capability, code in (
        ('stock_ohlcv', 'price_history', ticker),
        ('index_1001', 'index_history', '1001'),
        ('index_2001', 'index_history', '2001'),
        ('trading_volume', 'investor_flows', ticker),
    ):
        rows = captured.get(key)
        if not isinstance(rows, dict) or not rows or 'error' in rows:
            continue
        try:
            frame = pd.DataFrame.from_dict(
                {day: row for day, row in rows.items() if day != '__meta__'}, orient='index')
            if frame.empty:
                continue
            frame.index = pd.to_datetime(frame.index, errors='raise')
            metadata = rows.get('__meta__')
            if isinstance(metadata, dict):
                frame.attrs.update(metadata)
            captures.append({'capability': capability, 'ticker': code,
                             'params': {}, 'response': encode_result(frame)})
        except (ValueError, TypeError, OverflowError):
            # Invalid input remains unavailable; never manufacture replacement bars.
            continue
    reference = pd.Timestamp(reference_date).date().isoformat()
    return compute_report_metrics(captures, ticker=ticker, reference_date=reference,
                                  asof_utc=asof_utc)


def _frame(capture, cutoff):
    payload = capture['response']
    # Validate before pandas schema coercion: integer dtype can truncate fractions.
    for row in payload['table']['data']:
        for column, value in row.items():
            if column in {'Volume', '기관합계', '외국인합계', '개인'}:
                _quantity(value)
            if column in {'Open', 'High', 'Low', 'Close'} and (
                isinstance(value, bool) or value is None or not math.isfinite(float(value))
            ):
                raise ValueError('invalid_price')
    frame = decode_result(payload)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError('missing_frame')
    for i, row in enumerate(payload['table']['data']):
        for column in ('Open', 'High', 'Low', 'Close', 'Volume', '기관합계', '외국인합계', '개인'):
            if column in row and float(row[column]) != float(frame.iloc[i][column]):
                raise ValueError('lossy_numeric_decode')
    days = []
    for value in frame.index:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError('invalid_date')
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert('Asia/Seoul')
        if stamp.time().isoformat() != '00:00:00':
            raise ValueError('non_daily_timestamp')
        days.append(stamp.date().isoformat())
    if len(set(days)) != len(days) or max(days) > cutoff:
        raise ValueError('duplicate_or_future_date')
    frame.index = days
    return frame.sort_index()


def compute_report_metrics(captures, *, ticker, reference_date, asof_utc):
    """Compute dated observable facts, not economic interpretations.

    Price windows include the latest observed daily row, whose settlement finality
    is not proven by capture metadata. Flow windows always exclude the current KST
    date and use independent benchmark observations, not an official calendar.
    """
    asof = pd.Timestamp(asof_utc)
    if pd.isna(asof) or asof.tzinfo is None:
        raise ValueError('asof_utc requires an explicit timezone')
    asof = asof.tz_convert('UTC')
    reference = date.fromisoformat(reference_date).isoformat()
    cutoff = min(reference, asof.tz_convert('Asia/Seoul').date().isoformat())
    result = {'version': VERSION, 'ticker': ticker, 'reference_date': reference,
              'asof_utc': asof.isoformat(), 'facts': [], 'provenance': [],
              'limitations': []}
    result['limitations'] = ['일별 가격은 취득 시점의 관측값이며 최종 확정 여부는 확인하지 않았습니다.',
                             '관측 거래일 기준이며 공식 거래소 달력의 완전성을 검증하지 않았습니다.',
                             '기업행위 보정 여부는 각 캡처 요청 조건을 따릅니다. 수급은 미보정 원시 주식 수입니다.']
    selected = {}
    for cap, code in [('price_history', ticker), ('index_history', '1001'),
                      ('index_history', '2001'), ('investor_flows', ticker)]:
        matches = [c for c in captures if c.get('capability') == cap and c.get('ticker') == code]
        sid = f'{cap}:{code}'
        try:
            if len(matches) != 1:
                raise ValueError('missing_or_duplicate_capture')
            capture = matches[0]
            fingerprint = hashlib.sha256(json.dumps(capture, ensure_ascii=False,
                sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            result['provenance'].append({'id': sid, 'capability': cap, 'ticker': code,
                                        'params': capture.get('params', {}),
                                        'attrs': capture['response'].get('attrs', {}),
                                        'sha256': fingerprint})
            observed_at = capture['response'].get('attrs', {}).get('observed_at')
            if observed_at is not None:
                observed = pd.Timestamp(observed_at)
                if pd.isna(observed) or observed.tzinfo is None or observed > asof:
                    raise ValueError('invalid_or_future_capture_timestamp')
            selected[sid] = (_frame(capture, cutoff), None)
        except (ValueError, TypeError, KeyError, OverflowError):
            selected[sid] = (None, 'missing_or_invalid_capture')

    def fact(fid, label, value, unit, formula, source_ids, frame=None, n=None, reason=None):
        try:
            json.dumps(value, allow_nan=False)
        except (ValueError, OverflowError):
            value, reason = None, 'nonfinite_calculation'
        period = {'start': None, 'end': None, 'observations': 0}
        if frame is not None and len(frame):
            window = frame.tail(n) if n else frame
            period = {'start': str(window.index[0]), 'end': str(window.index[-1]),
                      'observations': len(window)}
        result['facts'].append({'id': fid, 'label': label, 'status': 'OK' if value is not None else 'MISSING',
                                'value': value, 'unit': unit, 'period': period,
                                'formula': formula, 'source_ids': source_ids,
                                'limitations': [reason] if reason else []})

    for prefix, sid, label, unit in [
        ('stock', f'price_history:{ticker}', '주가', '원'),
        ('index.1001', 'index_history:1001', '코스피', '포인트'),
        ('index.2001', 'index_history:2001', '코스닥', '포인트'),
    ]:
        frame, reason = selected[sid]
        if frame is not None:
            try:
                prices = frame[['Open', 'High', 'Low', 'Close']].astype(float)
                if (prices <= 0).any().any() or (prices.High < prices[['Open', 'Close', 'Low']].max(axis=1)).any() or (prices.Low > prices[['Open', 'Close', 'High']].min(axis=1)).any():
                    raise ValueError('invalid_ohlc')
                if 'Volume' not in frame or (frame.Volume < 0).any():
                    raise ValueError('invalid_volume')
            except (KeyError, ValueError, TypeError):
                frame, reason = None, 'missing_or_invalid_ohlcv'
        close = frame.Close.astype(float) if frame is not None else pd.Series(dtype=float)

        def add(suffix, title, value, value_unit, formula, n=None, *,
                prefix=prefix, label=label, sid=sid, frame=frame, reason=reason):
            fact(f'{prefix}.{suffix}', f'{label} {title}', value, value_unit,
                 formula, [sid], frame, n, reason or ('insufficient_observations' if value is None else None))

        latest = None
        if frame is not None:
            latest = {c: float(frame.iloc[-1][c]) for c in ('Open', 'High', 'Low', 'Close', 'Volume')}
            latest.update(date=str(frame.index[-1]), finality='unconfirmed')
        volume_unit = '주' if prefix == 'stock' else '원천 지수 거래량 단위(배율 미확인)'
        add('latest', '최근 관측 OHLCV', latest, f'가격:{unit}, 거래량:{volume_unit}', 'latest observed daily row', 1)
        for n in (5, 20, 60, 120, 200):
            add(f'sma.{n}', f'{n}관측일 종가 평균', float(close.tail(n).mean()) if len(close) >= n else None,
                unit, f'sum(last {n} closes)/{n}', n)
        for n in (1, 5, 20, 60, 120):
            add(f'return.{n}', f'{n}관측일 전 대비 등락률', float((close.iloc[-1] / close.iloc[-n-1] - 1) * 100) if len(close) > n else None,
                '%', f'(close[t]/close[t-{n}]-1)*100', n + 1)
        enough = len(close) >= 20
        ranges = {key: float(getattr(frame.tail(20)[column], op)()) for key, column, op in [
            ('ohlc_high', 'High', 'max'), ('ohlc_low', 'Low', 'min'),
            ('close_high', 'Close', 'max'), ('close_low', 'Close', 'min')]} if enough else None
        add('range.20', '20관측일 고가·저가 및 종가 범위', ranges, unit, 'max(High),min(Low),max(Close),min(Close)', 20)
        add('volume_mean.20', '20관측일 평균 거래량', float(frame.Volume.tail(20).mean()) if enough else None,
            '주' if prefix == 'stock' else '원천 지수 거래량 단위(배율 미확인)', 'sum(last 20 volumes)/20', 20)
        bands = None
        if enough:
            middle, sigma = float(close.tail(20).mean()), float(close.tail(20).std(ddof=0))
            bands = {'middle': middle, 'upper': middle + 2*sigma, 'lower': middle - 2*sigma,
                     'position_pct': (float(close.iloc[-1]) - middle + 2*sigma)/(4*sigma)*100 if sigma else None}
        add('bollinger.20', '볼린저 밴드', bands, f'밴드:{unit}, 위치:%', 'SMA20 ± 2*population_std(ddof=0); position=(close-lower)/(upper-lower)*100; flat position undefined', 20)
        rsi = None
        if len(close) >= 15:
            delta = close.diff().iloc[1:]
            gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
            up, down = float(gain.iloc[:14].mean()), float(loss.iloc[:14].mean())
            for g, loss_value in zip(gain.iloc[14:], loss.iloc[14:]):
                up, down = (13*up + g)/14, (13*down + loss_value)/14
            rsi = 50.0 if up == down == 0 else (100.0 if down == 0 else 100 - 100/(1 + up/down))
        add('rsi.14', 'RSI 14', rsi, '지수(0~100)', 'Wilder: seed=mean(first 14 changes), next=(13*previous+change)/14; flat=50', None)
        macd = None
        if len(close) >= 34:
            line = close.ewm(span=12, adjust=False, min_periods=12).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
            signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
            macd = {'line': float(line.iloc[-1]), 'signal': float(signal.iloc[-1]),
                    'histogram': float(line.iloc[-1] - signal.iloc[-1])}
        add('macd.12_26_9', 'MACD 12·26·9', macd, unit, 'EMA12-EMA26; signal=EMA9(line), adjust=False; seed=first observed; minimum 34 observations', None)

    flow, flow_reason = selected[f'investor_flows:{ticker}']
    price, _ = selected[f'price_history:{ticker}']
    benchmark, _ = selected['index_history:1001']
    def dated(frame):
        return {} if frame is None else frame.to_dict(orient='index')
    flow_rows = dated(flow)
    if flow is not None:
        flow_rows['__meta__'] = dict(flow.attrs)
    flow_evidence = compute_kr_flow_evidence(flow_rows, dated(price), dated(benchmark), asof_utc=asof.isoformat())
    for n in (5, 20, 30):
        window = flow_evidence['windows'][str(n)]
        value, reason = None, flow_reason or window['reason']
        if window['status'] == 'OK':
            try:
                days = [d for d in benchmark.index if window['start'] <= d <= window['end'] and date.fromisoformat(d).weekday() < 5]
                individual = sum(_quantity(flow.loc[d, '개인']) for d in days)
                net = window['net_shares']
                value = {'institution': net['institution'], 'foreign': net['foreign'],
                         'individual': individual, 'institution_foreign': net['combined'],
                         'all_three': net['combined'] + individual}
            except (ValueError, KeyError, TypeError):
                reason = 'invalid_or_missing_individual_quantity'
        fact(f'flow.{n}', f'{n}완료 관측일 투자자 순매수', value, '주(원화 금액 아님)',
             'sum(each actor net shares); institution_foreign excludes individual; all_three includes individual',
             [f'investor_flows:{ticker}', 'index_history:1001'], reason=reason)
        result['facts'][-1]['period'] = {'start': window['start'], 'end': window['end'], 'observations': window['observed_sessions']}
    return result


def render_report_metrics(result, *, scope='all'):
    """Readable public appendix; internal hashes/status codes are not report prose."""
    if scope not in {'all', 'stock', 'market'}:
        raise ValueError('invalid report metric scope')
    selected = [item for item in result['facts']
                if scope == 'all' or (item['id'].startswith('index.') == (scope == 'market'))]
    if not any(item['status'] == 'OK' for item in selected):
        return ''
    lines = ['## 시장 지표 기준값' if scope == 'market' else '## 주가·수급 지표 기준값',
             f"기준일: {result['reference_date']} / 조회 기준 시각(UTC): {result['asof_utc']}",
             '- 가격은 조회 시점에 제공된 일별 시세입니다. 최근 거래일의 수치는 최종 마감 자료와 차이가 있을 수 있습니다.',
             '- 이동평균과 등락률은 확보한 일별 관측값을 기준으로 계산했습니다.']
    labels = {'Open': '시가', 'High': '고가', 'Low': '저가', 'Close': '종가', 'Volume': '거래량',
              'ohlc_high': '장중 최고가', 'ohlc_low': '장중 최저가', 'close_high': '최고 종가', 'close_low': '최저 종가',
              'middle': '중심', 'upper': '상단', 'lower': '하단', 'position_pct': '밴드 내 위치(%)',
              'line': 'MACD', 'signal': '시그널', 'histogram': '차이', 'institution': '기관',
              'foreign': '외국인', 'individual': '개인', 'institution_foreign': '기관+외국인', 'all_three': '3주체 합계'}
    def number(value):
        return '산출 불가' if value is None else f'{value:,.2f}'
    for item in selected:
        if item['status'] != 'OK':
            continue
        value = item['value']
        rendered = ', '.join(f'{labels[key]} {number(v)}' for key, v in value.items() if key in labels) if isinstance(value, dict) else number(value)
        span = item['period']
        label = item['label'].replace('최근 관측 OHLCV', '최근 일별 시세')
        lines.append(f"- {label}: {rendered} [{item['unit']}; {span['start']}~{span['end']}, {span['observations']}관측일]")
    lines.append('- 필요한 관측 기간과 입력값이 충족된 지표만 제시했습니다.')
    if scope != 'market':
        lines.append('- 수급은 당일을 제외한 완료 관측일의 원시 주식 수입니다. 기관+외국인 합계와 개인을 포함한 3주체 합계는 다릅니다.')
    return '\n'.join(lines)
