"""Offline replay of frozen identity-v2 data with an approved two-day overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core import kr_legacy_adx_calendar_v3 as core
from tools import run_kr_legacy_adx_identity_v2 as v2


def korean_report(result):
    report = v2.korean_report(result)
    report = report.replace("# KR legacy ADX identity v2 · round 2", "# KR legacy ADX calendar v3 · quality iteration 3")
    report = report.replace("- 새 108개 요청에서 OHLC와 같은 history 응답의 symbol/type/exchange/timezone/firstTradeDate를 결합했습니다.",
                            "- v2에서 새로 수집한 108개 동일 응답의 OHLC와 symbol/type/exchange/timezone/firstTradeDate를 그대로 재사용했습니다. v3는 시세를 다시 조회하지 않았습니다.")
    report = report.replace("v1/v2 H1~H4 family8에 Bonferroni 99.375%", "v1/v2/v3 H1~H4 family12에 Bonferroni 99.5833%")
    details = ["", "## 연구 달력 교정과 제외된 승자",
               "- v2의 44행 결과를 이미 본 뒤, 사전 등록한 데이터 품질 교정을 수행했습니다. 독립 holdout이나 규칙 최적화를 주장하지 않습니다.",
               "- 운영 달력·cron·가격·임곗값은 그대로입니다. 2026-06-03과 2026-07-17만 연구용 예상 거래일에서 제외했습니다.",
               "- 2026-03-27은 평일 거래일로 남겨 부적합 OHLC를 그대로 제외했습니다. 다른 누락일을 휴장일로 간주하거나 원본 진입일을 다음 거래일로 옮기지 않았습니다.",
               f"- 원래 달력 버전: {result['calendar_overlay']['source_calendar_version']}. 사실 투영 해시: {core.FACTS_SHA256}.",
               "- 휴장 사실은 [삼성증권 6월 3일 주식시장 휴장 공지](https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=23996)와 [7월 17일 주식시장 휴장 공지](https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=24145)의 본문으로 확인했습니다. 두 공지는 KRX 시장업무규정 제5조를 인용합니다."]
    entry_dates = sorted(r["entry_session_date"] for r in result["rows"] if r["entry_session_date"])
    exit_dates = sorted(core.v2.v1.session_date(r["recorded_exit_at"]) for r in result["rows"] if r["recorded_exit_at"])
    if entry_dates and exit_dates:
        details.append(f"- 원본 기록 기간(한국 날짜): 진입 {entry_dates[0]}~{entry_dates[-1]}, 청산 {exit_dates[0]}~{exit_dates[-1]}입니다.")
    for book, summary in result["books"].items():
        w = summary["excluded_winner_coverage"]
        details += [f"- Legacy book {book[:12]}: 원본 대상 승자 {w['original_scoped_winner_count']}행 중 적격 {w['eligible_winner_count']}행, 제외 {w['excluded_winner_count']}행입니다. 제외 사유: {json.dumps(w['excluded_reason_counts'], ensure_ascii=False)}.",
                    "- 제외된 승자의 지표나 미진입 대응 성과를 채우지 않았습니다. 이 결측과 청산 기록만 남은 표본의 선택 편향을 고려해야 합니다."]
        if w["excluded_records"]:
            largest = max(w["excluded_records"], key=lambda row: row["recorded_return_pct"])
            details.append(f"- 제외 승자 중 가장 큰 원본 기록 수익률은 {largest['recorded_return_pct']:.3f}% ({largest['ticker']})입니다. 적격 표본의 승자 보존율에는 이 행이 들어가지 않습니다. 브로커 체결 손익으로 확인한 수치는 아닙니다.")
        trial_metrics = [summary["partitions"]["all_eligible"][trial] for trial in ("H1", "H2", "H3", "H4")]
        if all(m["paired_no_entry_zero_delta_pp"] is not None and m["paired_no_entry_zero_delta_pp"] < 0 for m in trial_metrics):
            details.append("- 전체 적격 기록에서 H1~H4의 대응 차이 점추정은 모두 음수였습니다. 이 관측만으로 모든 진입에 ADX/ER 하한을 추가할 근거는 확인되지 않았습니다.")
        if all(m["bonferroni_date_cluster_interval_pp"] is not None and m["bonferroni_date_cluster_interval_pp"][0] <= 0 <= m["bonferroni_date_cluster_interval_pp"][1] for m in trial_metrics):
            details.append("- 네 가설의 보수적 탐색 구간이 모두 0을 포함합니다. 개선이나 악화가 통계적으로 확정됐다는 뜻으로 해석하지 않습니다.")
    return report + "\n".join(details) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "collection", "registration", "facts", "derived-collection", "output", "markdown"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if any(path.exists() for path in (args.output, args.markdown, args.derived_collection)):
        raise ValueError("output_exists")
    source = json.loads(args.source.read_text())
    collection = json.loads(args.collection.read_text())
    facts = json.loads(args.facts.read_text())
    registered = args.registration.read_bytes()
    if core.FACTS_SHA256.encode() not in registered or collection["artifact_sha256"].encode() not in registered:
        raise ValueError("registration_inputs_mismatch")
    derived = core.calendar_overlay(collection, facts, hashlib.sha256(registered).hexdigest())
    result = core.build_study(source, derived)
    v2.base.write_new(args.derived_collection, derived)
    v2.base.write_new(args.output, result)
    with args.markdown.open("x", encoding="utf-8") as handle:
        handle.write(korean_report(result))
    print(json.dumps({"status": "studied", "artifact_sha256": result["artifact_sha256"],
                      "derived_collection_sha256": derived["artifact_sha256"], "coverage": result["coverage"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
