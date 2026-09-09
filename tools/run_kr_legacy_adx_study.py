"""Collect/freeze public KR daily bars or run registered legacy-only diagnostics."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core.kr_legacy_adx_research import (
    CUTOFF, TRIGGERS, build_study, session_date, validate_source,
)
from prism_core.trend_quality_research import digest, timestamp
from tools.collect_trend_replay_data import _fetch_child


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_new(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def bounded_fetch(request, timeout=40):
    """Reuse the sanitized child transport, without claiming US market identity."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_fetch_child, args=(request, send), daemon=True)
    started = now()
    process.start()
    send.close()
    try:
        result = receive.recv() if receive.poll(timeout) else {"status": "timeout", "raw_rows": []}
    except EOFError:
        result = {"status": "worker_error", "raw_rows": []}
    finally:
        receive.close()
        process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
    result.setdefault("retrieval_started_at", started)
    result.setdefault("retrieved_at", now())
    return result


def requests_for(source):
    groups = {}
    for row in source["records"]:
        if row.get("trigger_type") in TRIGGERS and re.fullmatch(r"[0-9]{6}", row.get("ticker") or "") and row.get("recorded_entry_at"):
            groups.setdefault(row["ticker"], []).append(row)
    requests = []
    for code, rows in sorted(groups.items()):
        start = min(timestamp(r["recorded_entry_at"]) for r in rows) - timedelta(days=210)
        for suffix in (".KS", ".KQ"):
            requests.append({"market": "KR", "base_code": code, "ticker": code + suffix, "interval": "1d",
                             "start": start.date().isoformat() + "T00:00:00+09:00", "end": CUTOFF})
    return requests


def make_schedule(start, end=CUTOFF):
    import pandas_market_calendars as calendars
    calendar = calendars.get_calendar("XKRX")
    schedule = calendar.schedule(start_date=timestamp(start).date(), end_date=timestamp(end).date())
    return {day.strftime("%Y-%m-%d"): {"open": row["market_open"].isoformat(),
                                     "close": row["market_close"].isoformat()}
            for day, row in schedule.iterrows()}, calendars.__version__


def normalize(request, response, schedule):
    bars, excluded = [], Counter()
    seen = set()
    for raw in response["raw_rows"]:
        try:
            day = session_date(raw["provider_timestamp"])
        except (KeyError, ValueError, TypeError):
            excluded["INVALID_PROVIDER_TIMESTAMP"] += 1
            continue
        if day not in schedule:
            excluded["NON_SESSION_DATE"] += 1
            continue
        if timestamp(schedule[day]["close"]) > timestamp(CUTOFF):
            excluded["INCOMPLETE_AT_CUTOFF"] += 1
            continue
        if day in seen:
            excluded["DUPLICATE_SESSION_DATE"] += 1
            # Preserve duplicate as an invalid mapping, never silently deduplicate.
            continue
        seen.add(day)
        values = [raw.get(k) for k in ("open", "high", "low", "close", "volume")]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
            excluded["MISSING_OHLCV"] += 1
            continue
        opened, high, low, close, volume = values
        if not 0 < low <= min(opened, close) <= max(opened, close) <= high or volume < 0:
            excluded["INVALID_OHLCV"] += 1
            continue
        bars.append({**raw, "session_date": day, "bar_close_at": schedule[day]["close"]})
    bars.sort(key=lambda b: b["session_date"])
    return {"request": request, **response, "bars": bars, "calendar": "XKRX",
            "source_sha256": digest(response), "exclusions": dict(excluded)}


def resolve_mapping(code, datasets):
    choices = [d for d in datasets if d["request"]["ticker"] in (code + ".KS", code + ".KQ")
               and d["bars"] and not d["exclusions"].get("DUPLICATE_SESSION_DATE")]
    if len(choices) != 1:
        return {"status": "AMBIGUOUS_SUFFIX" if len(choices) > 1 else "PRICE_DATA_MISSING",
                "provider_symbols_tried": [code + ".KS", code + ".KQ"]}
    return {"status": "OK", "provider_symbol": choices[0]["request"]["ticker"],
            "mapping_basis": "ONE_OF_TWO_SAME_BASE_YAHOO_SUFFIX_HISTORIES_NOT_ISIN_VERIFICATION",
            "dataset": choices[0]}


def collect(source, registration, raw_dir, fetcher=bounded_fetch):
    validate_source(source)
    requests = requests_for(source)
    schedule, version = make_schedule(min(r["start"] for r in requests))
    raw_dir.mkdir(exist_ok=False)
    registered_bytes = registration.read_bytes()
    registration_sha = hashlib.sha256(registered_bytes).hexdigest()
    manifest = {"kind": "KR_LEGACY_ADX_COLLECTION_REGISTRATION",
                "registered_before_fetch_at": now(), "source_artifact_sha256": source["artifact_sha256"],
                "registration_sha256": registration_sha, "requests": requests}
    write_new(raw_dir / "collection-manifest.json", manifest)
    # Bound both request count and live workers. No trading imports or calls.
    if len(requests) > 500:
        raise ValueError("request_count_exceeds_bound")
    def one(request):
        response = fetcher(request)
        raw = {"request": request, "response": response}
        raw["artifact_sha256"] = digest(raw)
        write_new(raw_dir / (request["ticker"] + ".json"), raw)
        return normalize(request, response, schedule)
    with ThreadPoolExecutor(max_workers=2) as pool:
        datasets = list(pool.map(one, requests))
    result = {"kind": "KR_LEGACY_RECONSTRUCTED_PUBLIC_DAILY_V1", "created_at": now(),
              "data_cutoff": CUTOFF, "source_artifact_sha256": source["artifact_sha256"],
              "registration_sha256": registration_sha, "schedule": schedule, "calendar_version": version,
              "datasets": datasets, "mapping": {},
              "price_basis": "YAHOO_AS_DELIVERED_AUTO_ADJUST_FALSE_BACK_ADJUST_FALSE_REPAIR_FALSE",
              "limitations": ["NOT_OBSERVED_AT_DECISION", "NOT_CANONICAL_STRATEGY", "NO_BROKER_FILL_FILTER",
                             "PROVIDER_OHLC_MAY_ALREADY_REFLECT_SPLITS", "CURRENT_PROVIDER_SURVIVOR_COVERAGE",
                             "FAILED_SUFFIX_NOT_PROOF_OF_DELISTING", "ORIGINAL_ISIN_LISTING_HISTORY_UNVERIFIED"],
              "automatic_shadow_forbidden": True, "automatic_live_forbidden": True}
    for code in sorted({r["base_code"] for r in requests}):
        result["mapping"][code] = resolve_mapping(code, datasets)
    result["artifact_sha256"] = digest(result)
    return result


def korean_report(result):
    def fmt(value):
        return "MISSING" if value is None else f"{value:.3f}"
    lines = ["# KR 과거 청산 기록 ADX 진단", "",
             "**결론: 재구성 자료로 제한된 대응 진단을 실행했지만, 종목 식별과 표본 부족으로 KR 규칙의 성과를 검증하지 못했습니다.**",
             f"- 분석 적격은 {result['coverage']['feature_status'].get('OK', 0)}행입니다. 규칙 간 우열이나 승격 근거로 사용할 수 없습니다.",
             f"- 부족 사유: {', '.join(result['insufficiency_reasons'])}.", "",
             "## 자료와 해석 범위",
             f"- 원본 {result['coverage']['source_record_count']}행을 보존한 별도 KR 연구입니다. US 결과와 합산하지 않았습니다.",
             "- 가명 계정별 legacy book 기록이지 canonical 전략 원장이나 실제 체결 손익이 아닙니다.",
             "- 원래 판단 시각이 없어 진입 날짜 정규장 시작 전 마지막 60개 완료봉을 사용했습니다. 진입일 전체를 제외한 previous-session-entry-proxy입니다.",
             "- 원래 decision_at, regime, policy, 배분 비중과 후보 모집단은 복원하지 않았습니다. 포트폴리오/점유율/재투자 수익률은 계산하지 않았습니다.",
             "- yfinance 비총수익 OHLC를 조회 시점 기준으로 재구성했습니다. .KS/.KQ 둘 다 유효하면 제외하고, 실패를 상장폐지로 해석하지 않았습니다.",
             "- 특징 구간 및 보유 중 split과 기업행위 미상은 제외했습니다. 청산 뒤 split의 균일 재조정은 지표의 배율 불변성과 원본 수익률의 검증을 구분합니다.",
             f"- 자료 마감: {result['data_cutoff']}. 실제 조회 시각과 원본 응답/해시는 별도 frozen raw 및 JSON에 남겼습니다.",
             f"- 특징 상태: {json.dumps(result['coverage']['feature_status'], ensure_ascii=False)}.",
             f"- 코드별 suffix 식별 상태: {json.dumps(result['coverage']['symbol_mapping_status'], ensure_ascii=False)}.",
             f"- 원본 trigger 구성: {json.dumps(result['coverage']['original_trigger_counts'], ensure_ascii=False)}.",
             f"- 제외 사유: {json.dumps(result['coverage']['exclusion_reasons'], ensure_ascii=False)}.", "",
             "## 고정 규칙과 대응 진단",
             "- H1 ADX>=20, H2 ADX>=25와 +DI>-DI, H3 ADX 3봉 연속 상승과 +DI>-DI, H4 ER20>=0.30과 20봉 상승입니다.",
             "- 탈락 기록의 미진입 수익률을 0으로 두고 원본 기록과 비교했습니다. 인과효과나 자본가중 성과가 아닙니다.",
             "- 날짜 cluster bootstrap은 최소 30행·20날짜부터이며, H1~H4 family에 Bonferroni 98.75% 탐색 구간을 적용합니다. 미달은 MISSING입니다.",
             "- 전체/학습/검증/trigger별 반복 결과는 규칙 선택이나 별도 유의성 주장에 쓰지 않습니다."]
    for book, data in result["books"].items():
        lines += ["", f"### Legacy book {book[:12]}",
                  f"- 원본 {data['original_record_count']}행 중 분석 적격 {data['eligible_record_count']}행입니다.",
                  f"- 원본 대상 진입 날짜 70/30 경계: {data['test_boundary']}. 30일 embargo 시작: {data['embargo_start']}. embargo/청산 purge {data['embargo_or_closure_purged_count']}행.",
                  "- 과거 검증 구간일 뿐 독립 prospective holdout이 아닙니다."]
        for partition, trials in data["partitions"].items():
            lines += [f"#### {partition}"]
            for trial, m in trials.items():
                interval = m["bonferroni_date_cluster_interval_pp"]
                lines.append(f"- {trial}: {m['closed_legacy_record_count']}행/{m['entry_dates']}날짜, 유지 {m['kept_count']}·제외 {m['dropped_count']}. 대응 차이 {fmt(m['paired_no_entry_zero_delta_pp'])}%p, 구간 {interval if interval is not None else 'MISSING'}, 승자 보존 {fmt(m['winner_preservation_rate'])}, 최고 승자 제외 차이 {fmt(m['delta_without_highest_winner_pp'])}%p. {m['status']}.")
        for trigger, trials in data["trigger_diagnostics"].items():
            lines += [f"#### 원본 trigger: {trigger}"]
            for trial, m in trials.items():
                lines.append(f"- {trial}: {m['closed_legacy_record_count']}행, 유지 {m['kept_count']}·제외 {m['dropped_count']}, 대응 차이 {fmt(m['paired_no_entry_zero_delta_pp'])}%p, 승자 보존 {fmt(m['winner_preservation_rate'])}. {m['status']}.")
        lines += ["- trigger별 결과와 편도 추가 비용 10/30bps stress는 JSON에 포함했습니다. 원래 기록의 비용 포함 여부는 모르므로 실제 수수료 차감 손익이 아닙니다."]
    lines += ["", "## 한계와 다음 단계", "- 미청산 포지션과 미선택 후보가 없고, 상장폐지·시장 이전·ISIN 이력 완전성도 확인되지 않았습니다. 기록 종료 조건에 따른 선택 편향이 있습니다.",
              "- 원래 판단일 정보 및 계정 간 canonical 전략 연결이 없어 운영 규칙 개선이나 수익성을 입증하지 못합니다.",
              "- 판정: **CONTINUE_CAPTURE**. 고정 가설을 변경하거나 SHADOW/LIVE로 승격하지 않습니다.",
              f"- 원본 artifact: {result['source_artifact_sha256']}",
              f"- 시세 artifact: {result['collection_artifact_sha256']}",
              f"- 결과 artifact: {result['artifact_sha256']}"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output_exists")
    source = json.loads(args.source.read_text())
    if args.collect:
        if args.raw_dir is None:
            raise ValueError("raw_dir_required")
        result = collect(source, args.registration, args.raw_dir)
    else:
        if args.collection is None or args.markdown is None or args.markdown.exists():
            raise ValueError("study_outputs_required")
        collection = json.loads(args.collection.read_text())
        if collection["registration_sha256"] != hashlib.sha256(args.registration.read_bytes()).hexdigest():
            raise ValueError("registration_changed")
        result = build_study(source, collection)
        with args.markdown.open("x", encoding="utf-8") as handle:
            handle.write(korean_report(result))
    write_new(args.output, result)
    print(json.dumps({"status": "collected" if args.collect else "studied", "artifact_sha256": result["artifact_sha256"],
                      "datasets": len(result.get("datasets", [])), "coverage": result.get("coverage")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
