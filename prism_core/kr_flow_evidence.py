"""Read-only Korean investor quantities, never US price-volume proxies."""

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

VERSION = "kr-flow-evidence-v1"
ACTORS = {"foreign": "외국인합계", "institution": "기관합계"}


def _dated(payload):
    if not isinstance(payload, dict) or "error" in payload:
        raise ValueError("missing_series")
    rows = {}
    for key, row in payload.items():
        if key == "__meta__":
            continue
        label = str(key)
        if len(label) == 8 and label.isdigit():
            label = f"{label[:4]}-{label[4:6]}-{label[6:]}"
        label = date.fromisoformat(label).isoformat()
        if label in rows or not isinstance(row, dict):
            raise ValueError("duplicate_or_invalid_row")
        rows[label] = row
    return rows


def _quantity(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid_quantity")
    try:
        value = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid_quantity") from exc
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError("invalid_quantity")
    return int(value)


def _streak(values):
    count = 0
    for value in reversed(values):
        if value <= 0:
            break
        count += 1
    return count


def _hash_quantity(value):
    """Fingerprint only normalized calculation inputs; invalid is explicit null."""
    try:
        return _quantity(value)
    except ValueError:
        return None


def compute_kr_flow_evidence(flow_data, price_data, session_data, *, asof_utc):
    """Use the last N completed observed benchmark sessions, not calendar days.

    An independent KIS benchmark is the session reference. This is not a claim
    that an exchange calendar or missing benchmark rows were independently audited.
    """
    result = {"version": VERSION, "market": "KR", "source": "KIS",
              "classification": "reported_investor_net_quantity",
              "unit": "shares_not_KRW", "corporate_action_adjustment": "not_applied_raw_shares",
              "session_basis": "KIS_benchmark_observed_sessions_not_official_calendar",
              "current_session_policy": "exclude_current_KST_date_without_finality_proof",
              "asof_utc": None, "excluded_estimate_dates": [], "input_hash": None,
              "windows": {}, "reason": None}
    try:
        asof = pd.Timestamp(asof_utc)
        if pd.isna(asof) or asof.tzinfo is None:
            raise ValueError("invalid_asof")
        asof = asof.tz_convert("UTC")
        result["asof_utc"] = asof.isoformat()
        flow, reference = map(_dated, (flow_data, session_data))
        try:
            prices = _dated(price_data)
        except (ValueError, TypeError, KeyError):
            # Price availability governs only volume normalization, not known net shares.
            prices = {}
        metadata = flow_data.get("__meta__", {})
        if not isinstance(metadata, dict):
            raise TypeError("invalid_metadata")
        unit = metadata.get("unit")
        if unit not in (None, "shares"):
            raise ValueError("unexpected_flow_unit")
        if metadata.get("data_status") == "intraday_estimate":
            raw_estimate = metadata.get("as_of")
            # Existing KIS producers label bucket times with literal KST.
            # Never rely on host-local timezone parsing of this abbreviation.
            if isinstance(raw_estimate, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} KST", raw_estimate):
                estimate = pd.Timestamp(raw_estimate[:-4]).tz_localize("Asia/Seoul")
            else:
                estimate = pd.Timestamp(raw_estimate)
            if pd.isna(estimate) or estimate.tzinfo is None:
                raise ValueError("unknown_estimate_date")
            result["excluded_estimate_dates"] = [estimate.tz_convert("Asia/Seoul").date().isoformat()]
        sessions = [day for day in sorted(reference)
                    if date.fromisoformat(day).weekday() < 5
                    and date.fromisoformat(day) < asof.tz_convert("Asia/Seoul").date()
                    and pd.Timestamp(day + " 15:40", tz="Asia/Seoul") <= asof
                    and day not in result["excluded_estimate_dates"]]
        relevant = sessions[-30:]
        fingerprint = {
            "sessions": relevant,
            "flows": {day: {actor: _hash_quantity(flow[day].get(column))
                            for actor, column in ACTORS.items()}
                      for day in relevant if day in flow},
            "volumes": {day: _hash_quantity(prices[day].get("Volume"))
                        for day in relevant if day in prices},
        }
        result["input_hash"] = hashlib.sha256(json.dumps(
            fingerprint, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, KeyError, OverflowError):
        result["reason"] = "invalid_or_missing_dated_input"
        flow, prices, sessions = {}, {}, []

    for n in (5, 20, 30):
        expected = sessions[-n:]
        window = {"status": "MISSING", "required_sessions": n,
                  "observed_sessions": sum(day in flow for day in expected),
                  "start": expected[0] if expected else None,
                  "end": expected[-1] if expected else None,
                  "net_shares": None, "positive_sessions": None,
                  "trailing_positive_sessions_within_window": None,
                  "combined_pct_of_traded_shares": None,
                  "volume_ratio_status": "MISSING", "reason": result["reason"]}
        result["windows"][str(n)] = window
        if window["reason"]:
            continue
        if len(expected) != n or window["observed_sessions"] != n:
            window["reason"] = "missing_reference_or_flow_sessions"
            continue
        try:
            values = {actor: [_quantity(flow[day].get(column)) for day in expected]
                      for actor, column in ACTORS.items()}
        except ValueError:
            window["reason"] = "invalid_or_missing_investor_quantity"
            continue
        values["combined"] = [a + b for a, b in zip(values["foreign"], values["institution"])]
        window.update(status="OK", net_shares={k: sum(v) for k, v in values.items()},
                      positive_sessions={k: sum(x > 0 for x in v) for k, v in values.items()},
                      trailing_positive_sessions_within_window={k: _streak(v) for k, v in values.items()})
        try:
            volumes = [_quantity(prices[day].get("Volume")) for day in expected]
            if any(v < 0 for v in volumes) or sum(volumes) <= 0:
                raise ValueError("invalid_volume")
            window["combined_pct_of_traded_shares"] = 100 * window["net_shares"]["combined"] / sum(volumes)
            window["volume_ratio_status"] = "OK"
        except (ValueError, KeyError):
            window["volume_ratio_status"] = "MISSING"  # Quantities remain known.
    return result


def render_kr_flow_evidence(evidence):
    lines = ["\n### KR_FLOW_EVIDENCE_V1 — 투자자 순매수 수량 요약",
             f"- 원천: KIS / 단위: 주(원화 금액 아님) / 기준: {evidence['asof_utc']}",
             "- 세션 기준: KIS 시장지수의 관측 거래일. 공식 달력 전수 검증과는 다릅니다.",
             "- 확정성 증명이 없는 현재 한국 날짜 및 장중 추정은 누적에서 제외합니다."]
    for n, window in evidence["windows"].items():
        span = f"{window['start']}~{window['end']}"
        coverage = f"관측 {window['observed_sessions']}/{window['required_sessions']}세션"
        if window["status"] != "OK":
            lines.append(f"- {n}세션: MISSING ({coverage}, {span}, 사유={window['reason']}).")
            continue
        net, positive, streak = (window[key] for key in (
            "net_shares", "positive_sessions", "trailing_positive_sessions_within_window"))
        lines.append(f"- {n}세션 ({span}, {coverage}): 외국인 {net['foreign']}주, "
                     f"기관 {net['institution']}주, 합계 {net['combined']}주.")
        lines.append(f"  순매수 관측일: 외국인 {positive['foreign']}, 기관 {positive['institution']}, "
                     f"합계 {positive['combined']}일. 창 내 말미 연속 순매수: "
                     f"외국인 {streak['foreign']}, 기관 {streak['institution']}, 합계 {streak['combined']}세션.")
        ratio = window["combined_pct_of_traded_shares"]
        ratio_text = f"{ratio:.8f}%" if ratio is not None else "MISSING"
        lines.append(f"  동일 구간 거래량 대비 합계 순매수 수량: {ratio_text}.")
    lines.extend([f"- 계산 입력 해시: {evidence['input_hash']}",
                  "- 기업행위를 보정하지 않은 원시 수량입니다. 누적 양수는 연속 순매수의 증명이 아닙니다.",
                  "- 겹치는 창을 독립 가점으로 세거나 결측을0으로 바꾸거나 새로운 매수 차단 조건으로 삼지 마십시오."])
    return "\n".join(lines) + "\n"


def kr_flow_interpretation_contract(language="ko"):
    if language == "en":
        return """
## KR investor-flow evidence contract
Use KR_FLOW_EVIDENCE_V1 (public heading: 투자자 순매수 수량 요약) as the numeric reference for 5/20/30 observed completed sessions.
Foreign, institution and combined figures are separate net SHARE quantities, not KRW or ownership levels.
Check window dates, coverage, source, as-of and raw-share adjustment limits. Intraday estimates are separate.
MISSING is unknown, never zero/buying/selling. A positive total does not prove consecutive positive sessions.
Keep the existing consecutive-3-session and cumulative-5-session criteria. Do not add independent credits
for the overlapping new 20/30-session context. Preserve existing criteria, scores,
stop/sizing rules; do not introduce a blanket rejection from a negative long window or infer causality.
"""
    return """
## 한국 수급 정량 근거 계약
KR_FLOW_EVIDENCE_V1(공개 보고서 제목: 투자자 순매수 수량 요약)의5/20/30 확정 관측 세션 계산값을 수급 숫자의 기준으로 사용하십시오.
외국인·기관·합계는 각각 순매수 수량(주)이며 원화 금액·보유 비율이 아닙니다. 기간·세션 수·출처·기준시각과
기업행위 미조정 원시 수량이라는 한계를 확인하고 장중 추정은 별도로 읽으십시오.
MISSING은 미확인이지0·순매수·순매도가 아닙니다. 합계 양수만으로 연속 순매수를 추정하지 마십시오.
기존3세션 연속·5세션 누적 조건은 유지하되 새20/30 창을 별도 독립 확인으로 더하지 마십시오. 기존 조건·점수·손절·비중을 유지하고
20/30일 순매도만으로 전역 매수 금지나 인과관계를 새로 만들지 마십시오.
"""
