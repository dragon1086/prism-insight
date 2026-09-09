"""Registered retrospective diagnostics. No trading imports, orders or promotion."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import collect_trend_replay_data as data

FROZEN_SUPPLEMENT_FILE_SHA256 = "f708e92e1b5238d7a0e11d0c53f1413657c80862254b0926bdd633fed5c4dae0"
FROZEN_SUPPLEMENT_CANONICAL_SHA256 = "dfe104ead17031cd8041208fe77ffbfe5e8377788beffaf46b315125ab01eca3"


def read_frozen_price_packet(path):
    """Require the exact supplementary artifact registered by the price addendum."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_SUPPLEMENT_FILE_SHA256:
        raise ValueError("registered_price_packet_file_changed")
    packet = json.loads(raw)
    if data.sha(packet) != FROZEN_SUPPLEMENT_CANONICAL_SHA256:
        raise ValueError("registered_price_packet_content_changed")
    return packet


def joined_price_evidence(original, supplementary):
    candidates = {r["decision_ref"]: r for r in supplementary["analysis_rows"]}
    result = {}
    if len(candidates) != len(supplementary["analysis_rows"]):
        raise ValueError("duplicate_supplementary_ref")
    for row in original["analysis_rows"]:
        other = candidates.get(row["decision_ref"])
        if other is None or any(row[field] != other[field] for field in ("ticker", "decided_at")):
            raise ValueError("supplementary_context_mismatch")
        if row.get("outcomes", {}).get("strategy_entry_at") != other.get("outcomes", {}).get("strategy_entry_at"):
            raise ValueError("supplementary_entry_mismatch")
        if row.get("entry", {}).get("position_ref") != other.get("entry", {}).get("position_ref"):
            raise ValueError("supplementary_position_mismatch")
        evidence = other.get("entry", {}).get("price_evidence", {})
        if evidence.get("status") == "OK" and evidence.get("schema_version") == 1 and row.get("entry", {}).get("observed"):
            if evidence.get("basis") != "ORIGINAL_STRATEGY_EVENT_NOT_BROKER_FILL":
                raise ValueError("invalid_price_basis")
            allowed = {"schema_version", "status", "basis", "source_event_ref", "reference_price",
                       "stop_loss_at_entry", "stop_history_reconstructed"}
            result[row["decision_ref"]] = {key: evidence[key] for key in allowed if key in evidence}
    return result


def merged_case_bars(source, added, trade, interval):
    """Re-normalize preserved raw rows using case entry, not actual exit boundary.

    A bar straddling the actual exit still belongs to a hypothetical continued
    position. Never fabricate a missing bar or silently accept conflicting copies.
    """
    merged = {}
    for dataset in source["datasets"] + added["datasets"]:
        request = dataset["request"]
        if request.get("decision_ref") != trade["decision_ref"] or request["interval"] != interval:
            continue
        replay = {key: dataset[key] for key in ("status", "raw_rows", "exchange", "retrieved_at")}
        case_request = {**request, "start": trade["strategy_entry_at"], "end": data.CUTOFF}
        normalized = data.normalize(case_request, replay)
        for bar in normalized["bars"]:
            key = bar["bar_close_at"]
            if key in merged and merged[key] != bar:
                raise ValueError("conflicting_provider_bar")
            merged[key] = bar
    return [merged[key] for key in sorted(merged)]


def studies(source, added, packets, supplement, registration, price_report):
    from prism_core.historical_trend_replay import build_historical_study
    from prism_core.noise_stop_research import run_noise_case
    # Also guard direct in-memory callers; identity joins alone do not freeze prices.
    if data.sha(supplement) != FROZEN_SUPPLEMENT_CANONICAL_SHA256:
        raise ValueError("registered_price_packet_content_changed")
    data.verify(source)
    data.verify(added)
    if added["initial_artifact_sha256"] != source["artifact_sha256"]:
        raise ValueError("extension_lineage_mismatch")
    registration_hash = hashlib.sha256(registration.read_bytes()).hexdigest()
    if added["registration_sha256"] != registration_hash:
        raise ValueError("registration_changed_after_extension")
    if hashlib.sha256(price_report.read_bytes()).hexdigest() != "08fd58eb041bba2468bb4e5254bbb1ac5aa17fa2ca1cce54e36502845c13bd0c":
        raise ValueError("reference_exit_report_mismatch")
    prices = joined_price_evidence(next(p for p in packets if p["market"] == "US"), supplement)
    result = {"kind": "ACTUAL_RECONSTRUCTED_DIAGNOSTIC", "created_at": data.iso(datetime.now(timezone.utc)),
              "data_cutoff": data.CUTOFF, "registration_sha256": registration_hash,
              "source_artifact_sha256": source["artifact_sha256"], "extension_artifact_sha256": added["artifact_sha256"],
              "supplementary_packet_id": supplement["packet_id"],
              "supplementary_packet_content_sha256": data.sha(supplement),
              "price_report_sha256": hashlib.sha256(price_report.read_bytes()).hexdigest(),
              "price_addendum_sha256": hashlib.sha256((registration.parent /
                  "reconstructed-trend-noise-price-addendum-20260910.md").read_bytes()).hexdigest(),
              "adx": build_historical_study(packets, source), "noise_cases": [],
              "automatic_shadow_forbidden": True, "automatic_live_forbidden": True,
              "verdict": "CONTINUE_CAPTURE"}
    for trade in source["closed_trades"]:
        evidence = prices.get(trade["decision_ref"])
        if evidence is None or not evidence.get("stop_loss_at_entry"):
            result["noise_cases"].append({"ticker": trade["ticker"], "status": "ORIGINAL_STOP_MISSING"})
            continue
        reference = evidence["reference_price"]
        case = run_noise_case(merged_case_bars(source, added, trade, "1m"),
            merged_case_bars(source, added, trade, "5m"), entry_at=trade["strategy_entry_at"],
            cutoff=data.CUTOFF, reference_entry=reference, normal_threshold=evidence["stop_loss_at_entry"] * .995,
            catastrophic_threshold=reference * .93, actual_exit_at=trade["strategy_closed_at"],
            actual_exit_price=1731.29 if trade["ticker"] == "SNDK" else None)
        result["noise_cases"].append({"ticker": trade["ticker"], "decision_ref": trade["decision_ref"],
            "price_evidence": evidence, "scope": "SNDK_DISCOVERY" if trade["ticker"] == "SNDK" else "SEEN_STATIC_INITIAL_STOP_APPROXIMATION",
            "dynamic_stop_history_available": False, **case})
    result["artifact_sha256"] = data.sha(result)
    return result


def korean_report(result):
    adx = result["adx"]
    def value(number):
        return "알 수 없음" if number is None else f"{number:.4f}"
    lines = ["# 실제 데이터 기반 ADX·손절 진단", "",
             "이 결과는 과거 데이터를 다시 수집한 탐색 진단입니다. 독립 검증이나 수익 개선 증명이 아니며 운영 규칙은 바꾸지 않았습니다.",
             "", "## 데이터와 한계", f"- 데이터 마감: {result['data_cutoff']}",
             "- 최초 수집과 청산 후 추가 수집은 서로 다른 수집 회차입니다. 각 원본과 수집 시각·해시를 보존했습니다.",
             "- 원장 진입의 가격 근거는 별도 정본 Packet의 정확한 연결로 확인했습니다. 브로커 체결가는 아닙니다.",
             "- SNDK는 발견 사례이며 SPGI도 이미 본 사례입니다. 독립 holdout이 없습니다.",
             "", "## 고정 ADX 가설", f"- 전체 후보 {len(adx['rows'])}건, 대상 날짜 {adx['date_count']}개입니다.",
             f"- 70/30 날짜 분할 후 30일 embargo를 적용한 학습 표본: {adx['train_rows_after_30_day_embargo']}건. 판정: NO_VALID_HOLDOUT.",
             "- 일봉은 판단 전에 마감된 마지막 60개만 사용했습니다. 현재 관측값을 과거 observed_at으로 위장하지 않았습니다.",
             f"- 제외 사유별 건수: {json.dumps(adx['exclusion_counts'], ensure_ascii=False, sort_keys=True)}. 한 후보에 사유가 겹칠 수 있습니다."]
    for name, metrics in adx["paired_closed_diagnostics"].items():
        lines.append(f"- {name}: 연결 청산 {metrics['paired_closed_strategy_count']}건, 유지 {metrics['kept_count']}건, 제외 {metrics['dropped_count']}건, 대응 차이 {value(metrics['paired_equal_opportunity_delta_pp'])}%p.")
    lines += ["- 위 차이는 미진입을 0으로 둔 가격수익률 비교입니다. 포트폴리오 수익률이나 인과 효과가 아닙니다.",
              "- 전방 1·3·5일 값은 판단 직전 마감가와 이후 거래일 마감가의 비교입니다. 정확한 판단 시점 가격 수익률이 아닙니다.",
              "- 관측된 진입만으로 단위 슬롯 점유를 재생했습니다. 누락된 대체 후보나 실제 배분 비중·자금 재활용은 추정하지 않았습니다.",
              "- winner 보존율과 날짜별 bootstrap 구간은 JSON의 표본 기준을 함께 확인해야 합니다. null은 0이나 검증 성공이 아닙니다.",
              "", "## 손절 방식 비교"]
    for case in result["noise_cases"]:
        lines.append(f"### {case['ticker']} ({case.get('scope', case['status'])})")
        reference = case.get("actual_reference_exit", {})
        lines.append(f"- 실제 청산 참조: {reference.get('at')}, 가격 {reference.get('price')}. 아래 분봉 근사 집행과 구분합니다.")
        for name, arm in case.get("arms", {}).items():
            returns = arm["return_pct_by_cost_bps_each_side"]
            lines.append(f"- {name}: 판단 {arm['decision_at']}, 집행 {arm['execution_at']}, 가격 {arm['exit_price']}, 평가 {arm['valuation']}. 편도 비용 0/10/30bps 수익률은 {returns['0']:.4f}% / {returns['10']:.4f}% / {returns['30']:.4f}%입니다.")
            lines.append(f"  - 보유 구간 MFE {value(arm['mfe_pct'])}%, MAE {value(arm['mae_pct'])}%. 조회 시각의 분봉 부재 {arm['missed_scheduled_observations']}회이며 휴장·시간외 자료 부재도 포함할 수 있습니다.")
        path = case.get("post_actual_exit_path", {})
        lines.append(f"- 실제 청산 후 확인된 분봉 {path.get('available_minute_bars', 0)}개, 손절 기준을 회복한 첫 마감 시각: {path.get('first_close_above_normal_at')}.")
        lines.append(f"- 실제 청산가 대비 최대 마감 회복 {value(path.get('maximum_close_recovery_from_actual_exit_pct'))}%, 추가 저가 하락 {value(path.get('worst_low_from_actual_exit_pct'))}%. 모집단 꼬리위험 추정치는 아닙니다.")
    lines += ["", "## 해석", "- 분봉 마감가로 정해진 cron 조회를 근사했습니다. 실제 틱·조회 시각·즉시 체결을 재현한 결과가 아닙니다.",
              "- 연속 확인에는 모든 중간 분봉이 있어야 합니다. 결측은 연속 하락의 증거가 아닙니다. 절대 손절은 세 방식에서 같은 빠른 조회 시각에 적용했습니다.",
              "- 집행은 판단보다 엄격히 뒤인 다음 확인 가능 분봉 시가입니다. 표본 1~2건으로 지연 손실의 모집단 꼬리위험을 추정할 수 없습니다.",
              "- 미청산 평가에는 공통 마감가와 가상 왕복 비용을 적용했습니다. 실제 실현수익이나 발생한 비용을 뜻하지 않습니다.",
              "- SPGI는 초기 손절가 고정 근사이며 손절가 변경 이력이 없습니다. 종료 시각이 자료 마감과 같아 청산 후 회복을 평가할 수 없습니다.",
              "", "판정: **CONTINUE_CAPTURE**. 운영 필터·손절·SHADOW/LIVE를 승격하지 않습니다.",
              "", f"결과 해시: `{result['artifact_sha256']}`"]
    return "\n".join(lines) + "\n"


def extension(source, registration):
    data.verify(source)
    result = {"kind": "RECONSTRUCTED_REPLAY_POST_EXIT", "collection_round": 2,
              "created_at": data.iso(datetime.now(timezone.utc)), "data_cutoff": data.CUTOFF,
              "initial_artifact_sha256": source["artifact_sha256"],
              "registration_sha256": hashlib.sha256(registration.read_bytes()).hexdigest(),
              "datasets": [], "skipped": []}
    for trade in source["closed_trades"]:
        start = trade["strategy_closed_at"]
        if data.stamp(start) >= data.stamp(data.CUTOFF):
            result["skipped"].append({"decision_ref": trade["decision_ref"], "reason": "EXIT_AT_FIXED_CUTOFF"})
            continue
        for interval in ("1m", "5m"):
            request = {"market": trade["market"], "ticker": trade["ticker"], "interval": interval,
                       "start": start, "end": data.CUTOFF, "decision_ref": trade["decision_ref"]}
            result["datasets"].append(data.normalize(request, data.fetch(request)))
    result["artifact_sha256"] = data.sha(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect-extension", action="store_true")
    parser.add_argument("--extension", type=Path)
    parser.add_argument("--packet", type=Path, action="append")
    parser.add_argument("--price-packet", type=Path)
    parser.add_argument("--price-report", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("output_exists")
        source = json.loads(args.source.read_text())
        if args.collect_extension:
            result = extension(source, args.registration)
        else:
            if not args.extension or not args.packet or not args.price_packet or not args.price_report or not args.markdown or args.markdown.exists():
                raise ValueError("study_inputs_required")
            packets = [json.loads(path.read_text()) for path in args.packet]
            for path, packet in zip(args.packet, packets):
                expected = next(p for p in source["packets"] if p["packet_id"] == packet["packet_id"])
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected["packet_sha256"]:
                    raise ValueError("original_packet_changed")
            result = studies(source, json.loads(args.extension.read_text()), packets,
                             read_frozen_price_packet(args.price_packet), args.registration, args.price_report)
            with args.markdown.open("x") as handle:
                handle.write(korean_report(result))
        with args.output.open("x") as handle:
            json.dump(result, handle, sort_keys=True, indent=2, allow_nan=False)
        print(json.dumps({"status": "collected" if args.collect_extension else "studied",
                          "artifact_sha256": result["artifact_sha256"],
                          "dataset_count": len(result.get("datasets", [])), "collection_round": 2}))
    except (ValueError, OSError, KeyError, TypeError, StopIteration):
        print(json.dumps({"status": "failed", "category": "registered_diagnostic_failed"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
