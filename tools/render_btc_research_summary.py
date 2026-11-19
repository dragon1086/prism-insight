"""Read-only Korean summary of existing BTC economic replay artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys


HASH_FIELDS = ("contract_hash", "tape_hash", "exchange_hash", "final_state_hash",
               "economic_clock_trace_hash", "runtime_event_hash", "result_hash")
MAX_FILE_BYTES = 16_000_000
MAX_TAPE_BYTES = 96_000_000


def require(condition, code):
    if not condition:
        raise ValueError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def read_json(path):
    require(path.stat().st_size <= MAX_FILE_BYTES, "summary_json_size")
    return json.loads(path.read_text(encoding="utf-8"))


def count(value):
    require(value is None or type(value) is int and value >= 0, "summary_invalid_count")
    return value


def number(value):
    try:
        valid = value is None or type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    require(valid, "summary_invalid_number")
    return value


def tape_facts(path, contract_hash=None):
    """Count recorded operations, never rerun a model or a trading decision."""
    result = dict(reviews=0, failed_reviews=0, replies=0, generation_errors=0,
                  missing_responses=0, stages=Counter(), responses={})
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        total = 0
        header = True
        while True:
            line = stream.readline(MAX_FILE_BYTES + 1)
            if not line:
                break
            total += len(line)
            require(len(line) <= MAX_FILE_BYTES and total <= MAX_TAPE_BYTES,
                    "summary_tape_size")
            row = json.loads(line)
            require(isinstance(row, dict), "summary_tape_record_type")
            if header:
                require(row.get("kind") == "economic_header"
                        and row.get("schema") == 4
                        and row.get("contract_hash") == digest(row["contract"]),
                        "summary_tape_header")
                if contract_hash is not None:
                    require(row["contract_hash"] == contract_hash, "summary_tape_wrong_report")
                header = False
                continue
            proof = row.pop("sha256", None)
            require(row.get("kind") == "economic_tick" and proof == digest(row), "summary_tape_row_hash")
            decision = row.get("decision")
            if not decision:
                continue
            envelope = decision["envelope"]
            require(envelope.get("sha256") == digest(
                {k: v for k, v in envelope.items() if k != "sha256"}),
                "summary_review_hash")
            status = envelope["outcome"]["status"]
            require(status in {"COMPLETE", "FAILED"}, "summary_review_status")
            result["reviews"] += 1
            result["failed_reviews"] += status == "FAILED"
            operations = [op for op in envelope["operations"] if op["kind"] == "generate"]
            result["stages"][len(operations)] += 1
            for op in operations:
                if isinstance(op.get("text"), str):
                    require("error" not in op, "summary_ambiguous_response")
                    key = digest(op["request"])
                    require(key not in result["responses"], "summary_duplicate_request")
                    result["responses"][key] = digest(dict(text=op["text"], usage=op.get("usage")))
                    result["replies"] += 1
                elif "error" in op:
                    result["generation_errors"] += 1
                else:
                    result["missing_responses"] += 1
        require(not header, "summary_empty_tape")
    return result


def study_facts(directory, recorded):
    """Use the existing read-only ledger verifier; never construct CallGuard."""
    from tools.btc_research_call_guard import CallGuard, CallGuardError

    result = dict(http=None, unknown_http=None, cached_matches=None, prior_missing_responses=None)
    registry_path = directory / "registry.json"
    if registry_path.exists() and (directory / "http-guard").exists():
        registry = read_json(registry_path)
        try:
            state = CallGuard.inspect_existing(directory / "http-guard",
                hashlib.sha256(registry_path.read_bytes()).hexdigest(), registry["max_http"])
        except CallGuardError:
            raise ValueError("summary_call_ledger_invalid") from None
        result.update(http=count(state["actual_http_calls"]),
                      unknown_http=count(state["unknown_http_attempts"]))
        prior = count(registry.get("prior_sealed_actual_http"))
        if prior is not None:
            result["registered_prior_http"] = prior
            result["cumulative_http"] = (prior + result["http"]
                if not state["inflight"] and result["unknown_http"] == 0 else None)
    certificate_path, used_path = directory / "cache-certificate.json", directory / "cache-used.json"
    if certificate_path.exists():
        certificate = read_json(certificate_path)
        if result.get("registered_prior_http") is not None and "prior_actual_http" in certificate:
            require(count(certificate["prior_actual_http"]) == result["registered_prior_http"],
                    "summary_prior_http_mismatch")
        result["prior_missing_responses"] = count(certificate.get("missing_response_attempts"))
        if recorded is not None and used_path.exists():
            source = Path(certificate["tape_path"])
            if not source.is_absolute():
                source = directory / source
            require(source.stat().st_size <= MAX_TAPE_BYTES, "summary_cache_size")
            require(hashlib.sha256(source.read_bytes()).hexdigest() == certificate["tape_sha256"],
                    "summary_cache_hash")
            cached = tape_facts(source)["responses"]
            require(len(cached) == count(certificate["reused_response_count"]),
                    "summary_cache_count")
            used = read_json(used_path)
            require(isinstance(used, list) and all(isinstance(k, str) for k in used)
                    and len(used) == len(set(used)) and set(used) <= cached.keys(),
                    "summary_cache_used")
            matched = recorded["responses"].keys() & set(used)
            require(all(recorded["responses"][key] == cached[key] for key in matched),
                    "summary_cached_response_changed")
            result["cached_matches"] = len(matched)
    return result


def comparison(report, frozen):
    if frozen is None:
        return dict(status="MISSING", differences=[], missing=list(HASH_FIELDS))
    metadata = frozen.get("execution_metadata", {})
    require(metadata.get("mode") == "frozen" and type(metadata.get("actual_model_calls")) is int
            and metadata["actual_model_calls"] == 0,
            "summary_not_frozen_report")
    missing = [key for key in HASH_FIELDS if report.get(key) is None or frozen.get(key) is None]
    for key in HASH_FIELDS:
        for source in (report, frozen):
            value = source.get(key)
            require(value is None or isinstance(value, str) and len(value) == 64
                    and all(c in "0123456789abcdef" for c in value), "summary_invalid_hash")
    different = [key for key in HASH_FIELDS if key not in missing and report[key] != frozen[key]]
    for key in ("economic", "sampled_closed_net_usdt", "closed_trade_detail",
                "sampled_daily_nav", "execution_inventory"):
        if key == "economic" or key in report or key in frozen:
            if report.get(key) is None or frozen.get(key) is None:
                missing.append(key)
            elif digest(report[key]) != digest(frozen[key]):
                different.append(key)
    return dict(status="FAILED" if different else "MISSING" if missing else "MATCH_REPORTED_HASHES",
                differences=different, missing=missing)


def summarize(report, *, tape=None, study=None, frozen=None, exchange=None):
    require(isinstance(report, dict) and report.get("simulated_only") is True,
            "summary_simulated_report_required")
    economic, metadata = report.get("economic", {}), report.get("execution_metadata", {})
    require(isinstance(economic, dict) and isinstance(metadata, dict), "summary_report_shape")
    require(report.get("mode") in {None, "SYNTHETIC_FIXTURE", "HISTORICAL_LLM_RESEARCH"}
            and metadata.get("mode") in {None, "fresh", "frozen", "fixture"}
            and report.get("execution_status") in {None, "COMPLETE", "STOPPED"}, "summary_report_mode")
    contract = report.get("contract", {})
    start, end = count(contract.get("start_ms")), count(contract.get("end_ms"))
    require(start is None or end is None or start < end, "summary_period")
    closed, wins, losses = [count(economic.get(k)) for k in ("completed_scenarios", "wins", "losses")]
    if None not in (closed, wins, losses):
        require(wins + losses <= closed, "summary_trade_counts")
    means = {}
    details = report.get("closed_trade_detail")
    if details is not None:
        require(isinstance(details, list) and closed == len(details), "summary_trade_detail_count")
        nets = [number(row.get("net_usdt")) for row in details]
        if None not in nets:
            require((wins is None or wins == sum(v > 0 for v in nets))
                    and (losses is None or losses == sum(v < 0 for v in nets)),
                    "summary_trade_detail_outcomes")
        for name, predicate in (("mean_winner", lambda v: v > 0), ("mean_loser", lambda v: v < 0)):
            values = [number(row.get("net_return_on_initial_notional")) for row in details
                      if row.get("net_usdt") is not None and predicate(number(row["net_usdt"]))]
            means[name] = (number(sum(values) / len(values))
                           if values and None not in values and None not in nets else None)
    inventory = report.get("execution_inventory")
    if exchange is not None:
        require(isinstance(exchange, dict) and exchange.get("simulation") is True
                and digest(exchange) == report.get("exchange_hash"), "summary_exchange_hash")
        if inventory is None:
            inventory = exchange.get("executions")
    fills = None
    if inventory is not None:
        require(isinstance(inventory, list), "summary_inventory_type")
        executions = {}
        for row in inventory:
            if row.get("execType") != "Trade":
                continue
            key = row.get("execId")
            require(isinstance(key, str) and key and (key not in executions or executions[key] == row),
                    "summary_fill_identity")
            executions[key] = row
        fills = len(executions)
    recorded = tape_facts(tape, report.get("contract_hash")) if tape is not None else None
    declared = report.get("coverage", {}).get("model_review_records")
    if recorded is not None and declared is not None:
        require(count(declared) == recorded["reviews"], "summary_review_count_mismatch")
    calls = study_facts(study, recorded) if study is not None else {}
    unclosed = economic.get("unclosed_scenario")
    require(unclosed is None or type(unclosed) is bool, "summary_invalid_unclosed_status")
    return dict(mode=report.get("mode"), execution_mode=metadata.get("mode"), start_ms=start, end_ms=end,
        execution_status=report.get("execution_status"),
        planned_slots=count(report.get("coverage", {}).get("planned_slots")),
        reviews=recorded["reviews"] if recorded is not None else None,
        replies=recorded["replies"] if recorded is not None else None,
        failed_reviews=recorded["failed_reviews"] if recorded is not None else None,
        generation_errors=recorded["generation_errors"] if recorded is not None else None,
        missing_responses=recorded["missing_responses"] if recorded is not None else None,
        stages=dict(recorded["stages"]) if recorded is not None else None,
        report_http=count(metadata.get("actual_model_calls")), study=calls,
        closed=closed, wins=wins, losses=losses, fills=fills,
        open_quantity=number(economic.get("open_quantity")),
        unclosed_scenario=unclosed,
        pending_intents=count(economic.get("pending_intents")),
        unknown_children=count(economic.get("unknown_exchange_children")),
        net_usdt=number(economic.get("net_change")), fees_usdt=number(economic.get("fees")),
        funding_usdt=number(economic.get("funding_net")),
        max_drawdown_pct=number(economic.get("max_drawdown_pct")),
        mean_winner=means.get("mean_winner"), mean_loser=means.get("mean_loser"),
        comparison=comparison(report, frozen))


def render(facts):
    def value(v):
        return "미확인" if v is None else str(v)
    def decimal(v, suffix=""):
        return "미확인" if v is None else f"{v:.6f}{suffix}"
    def mean(key, n):
        return "해당 없음" if n == 0 else decimal(
            None if facts[key] is None else facts[key] * 100, "%")
    calls, check = facts["study"], facts["comparison"]
    modes = {None: "미확인", "SYNTHETIC_FIXTURE": "합성 기능 검사",
             "HISTORICAL_LLM_RESEARCH": "과거 AI 연구", "fresh": "실행(재사용 포함 가능)",
             "frozen": "저장 응답 재생", "fixture": "합성 검사"}
    period = "미확인"
    if facts["start_ms"] is not None and facts["end_ms"] is not None:
        kst = timezone(timedelta(hours=9))
        period = " ~ ".join(datetime.fromtimestamp(facts[k] / 1000, kst).strftime("%Y-%m-%d %H:%M")
                            for k in ("start_ms", "end_ms")) + " KST (종료 미포함)"
    payoff = (number(facts["mean_winner"] / abs(facts["mean_loser"]))
              if facts["mean_winner"] is not None and facts["mean_loser"] else None)
    win_rate = (100 * facts["wins"] / facts["closed"]
                if facts["wins"] is not None and facts["closed"] else None)
    breakeven = (facts["closed"] - facts["wins"] - facts["losses"]
                 if None not in (facts["closed"], facts["wins"], facts["losses"]) else None)
    rows = [
        ("대상 기간", period),
        ("연구 종류 / 실행 방식", f"{modes[facts['mode']]} / {modes[facts['execution_mode']]}"),
        ("원본 실행 상태", value(facts["execution_status"])),
        ("계획 시점 / 테이프 판단 기록", f"{value(facts['planned_slots'])} / {value(facts['reviews'])}"),
        ("완료 응답 / 생성단계 오류 / 응답 누락", " / ".join(value(facts[k]) for k in
            ("replies", "generation_errors", "missing_responses"))),
        ("실패한 판단 기록", value(facts["failed_reviews"])),
        ("보고서에 기록된 해당 실행 HTTP", value(facts["report_http"])),
        ("연구 장부 전체 확정 HTTP / 미확인 시도", f"{value(calls.get('http'))} / {value(calls.get('unknown_http'))}"),
        ("이전 등록 HTTP / 등록분 포함 누적 HTTP", f"{value(calls.get('registered_prior_http'))} / {value(calls.get('cumulative_http'))}"),
        ("기존 응답 일치(재사용 기록 대조)", value(calls.get("cached_matches"))),
        ("이전 원응답 미보존", value(calls.get("prior_missing_responses"))),
        ("재생 체결 기록 / 종료 거래", f"{value(facts['fills'])} / {value(facts['closed'])}"),
        ("승 / 패 / 손익 0 / 승률", f"{value(facts['wins'])} / {value(facts['losses'])} / "
            f"{value(breakeven)} / {'해당 없음' if facts['closed'] == 0 else decimal(win_rate, '%')}"),
        ("평균 이익 / 평균 손실", f"{mean('mean_winner', facts['wins'])} / {mean('mean_loser', facts['losses'])}"),
        ("손익비", decimal(payoff)),
        ("비용 후 순손익 / 수수료 / 펀딩(USDT)", " / ".join(decimal(facts[k]) for k in
            ("net_usdt", "fees_usdt", "funding_usdt"))),
        ("최대낙폭", decimal(facts["max_drawdown_pct"], "%")),
        ("미청산 수량 / 미완료 주문 계획 / 미확인 거래소 주문", " / ".join(value(facts[k]) for k in
            ("open_quantity", "pending_intents", "unknown_children"))),
        ("미종료 시나리오", "미확인" if facts["unclosed_scenario"] is None else
            "있음" if facts["unclosed_scenario"] else "없음"),
        ("제공된 재생 보고서 대조", check["status"]),
        ("불일치 항목", ", ".join(check["differences"]) or "없음"),
        ("미확인 대조 항목", ", ".join(check["missing"]) or "없음"),
    ]
    if facts["stages"] is not None:
        rows.insert(4, ("판단별 생성단계 수", ", ".join(
            f"{n}단계 {total}회" for n, total in sorted(facts["stages"].items()))))
    warnings = []
    if facts["execution_status"] == "STOPPED":
        warnings.append("⚠️ 원 실행이 중단됐습니다. 아래는 기록된 일부 결과입니다.")
    if check["status"] == "FAILED":
        warnings.append("⚠️ 제공된 재생 보고서와 불일치합니다. 검증 완료 성과로 사용하지 않습니다.")
    elif check["status"] == "MISSING":
        warnings.append("재생 보고서 대조 근거는 미확인 또는 불완전합니다.")
    return ("# BTC 연구 결과 요약\n\n기존 모의연구 자료의 읽기 전용 표시입니다. "
            "새 AI 호출·주문·재생 검증은 실행하지 않았습니다. 실계좌 수익이나 수익성 승인표가 아닙니다.\n\n" +
            ("\n".join(warnings) + "\n\n" if warnings else "") +
            "| 항목 | 기록 |\n|---|---|\n" +
            "\n".join(f"| {name} | {text} |" for name, text in rows) +
            "\n\n연구 장부 수치는 연구 전체 범위입니다. 저장 응답 수를 신규 호출 수로 세지 않습니다. "
            "MATCH_REPORTED_HASHES는 제공된 보고서의 일치이며, 독립 재생을 새로 실행했다는 뜻이 아닙니다.\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="directory containing report.json")
    parser.add_argument("--tape", type=Path, help="existing economic JSONL tape, optionally gzip")
    parser.add_argument("--study", type=Path, help="existing registry/http-guard/cache sidecars")
    parser.add_argument("--frozen-report", type=Path, help="existing frozen report.json; no replay is run")
    args = parser.parse_args(argv)
    try:
        exchange = args.run / "exchange.json"
        facts = summarize(read_json(args.run / "report.json"), tape=args.tape, study=args.study,
                          frozen=read_json(args.frozen_report) if args.frozen_report else None,
                          exchange=read_json(exchange) if exchange.exists() else None)
        print(render(facts), end="")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        code = str(error)
        safe = code if (code.startswith("summary_") and len(code) <= 80
                        and all(c in "abcdefghijklmnopqrstuvwxyz_" for c in code)) else type(error).__name__
        print(f"BTC 연구 요약 실패: {safe}. 원본 자료는 변경하지 않았습니다.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
