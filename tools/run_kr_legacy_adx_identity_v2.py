"""Fresh same-response EQUITY identity correction; never re-label v1 OHLC."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core import kr_legacy_adx_identity_v2 as core
from tools import run_kr_legacy_adx_study as base
from tools.collect_trend_replay_data import FIELDS


def _fetch_child(request, connection):
    import contextlib
    import io
    import logging
    import tempfile
    logging.disable(logging.CRITICAL)
    started = base.now()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), tempfile.TemporaryDirectory() as cache:
            import yfinance as yf
            yf.set_tz_cache_location(cache)
            ticker = yf.Ticker(request["ticker"])
            frame = ticker.history(start=base.timestamp(request["start"]), end=base.timestamp(request["end"]),
                                   interval="1d", auto_adjust=False, back_adjust=False, repair=False,
                                   actions=True, prepost=True, keepna=True, rounding=False,
                                   timeout=10, raise_errors=True)
            # Private cached metadata came from THIS history response. Do not
            # call get_history_metadata(), info, fast_info, or another request.
            metadata = getattr(ticker._price_history, "_history_metadata", {}) or {}
            identity = {key: (metadata.get(key) if isinstance(metadata.get(key), (str, int, float)) else None)
                        for key in core.IDENTITY_FIELDS}
            rows = []
            for when, row in frame.iterrows():
                if when.tzinfo is None:
                    raise ValueError("provider_timezone_missing")
                values = {key: (float(row[column]) if column in row and math.isfinite(float(row[column])) else None)
                          for column, key in FIELDS.items()}
                rows.append({"provider_timestamp": when.isoformat(), **values})
            result = {"status": "received" if rows else "empty", "raw_rows": rows,
                      "identity_metadata": identity, "yfinance_version": yf.__version__,
                      "identity_source": "same_history_response_cached_metadata",
                      "raw_basis": "yfinance_history_dataframe_not_wire_chart_json"}
    except Exception:
        result = {"status": "provider_error", "raw_rows": [], "identity_metadata": {}}
    result.update(retrieval_started_at=started, retrieved_at=base.now())
    connection.send(result)
    connection.close()


def bounded_fetch(request, timeout=40):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_fetch_child, args=(request, send), daemon=True)
    started = base.now()
    process.start()
    send.close()
    try:
        result = receive.recv() if receive.poll(timeout) else {"status": "timeout", "raw_rows": [], "identity_metadata": {}}
    except EOFError:
        result = {"status": "worker_error", "raw_rows": [], "identity_metadata": {}}
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
    result.setdefault("retrieved_at", base.now())
    return result


def collect(source, registration, raw_dir, fetcher=bounded_fetch):
    core.v1.validate_source(source)
    registered = registration.read_bytes()
    if source["artifact_sha256"].encode() not in registered or source["logical_source_sha256"].encode() not in registered:
        raise ValueError("registration_source_mismatch")
    requests = base.requests_for(source)
    if not requests or len(requests) > 500:
        raise ValueError("request_count_invalid")
    schedule, calendar_version = base.make_schedule(min(r["start"] for r in requests))
    registration_sha = hashlib.sha256(registered).hexdigest()
    raw_dir.mkdir(exist_ok=False)
    base.write_new(raw_dir / "collection-manifest.json", {
        "kind": "KR_LEGACY_ADX_IDENTITY_V2_REGISTRATION", "collection_round": 2,
        "registered_before_fetch_at": base.now(), "registration_sha256": registration_sha,
        "source_artifact_sha256": source["artifact_sha256"], "requests": requests})
    def one(request):
        response = fetcher(request)
        raw = {"request": request, "response": response}
        raw["artifact_sha256"] = base.digest(raw)
        base.write_new(raw_dir / (request["ticker"] + ".json"), raw)
        return base.normalize(request, response, schedule)
    with ThreadPoolExecutor(max_workers=2) as pool:
        datasets = list(pool.map(one, requests))
    result = {"kind": "KR_LEGACY_RECONSTRUCTED_PUBLIC_DAILY_IDENTITY_V2", "collection_round": 2,
              "created_at": base.now(), "data_cutoff": base.CUTOFF,
              "source_artifact_sha256": source["artifact_sha256"], "registration_sha256": registration_sha,
              "quarantined_v1_collection_sha256": "e5840a94341a6943246ea3028b990e2562a23e75d53a9a305a8a6d6194601a8b",
              "schedule": schedule, "calendar_version": calendar_version, "datasets": datasets,
              "mapping": {code: core.resolve_mapping(code, datasets) for code in sorted({r["base_code"] for r in requests})},
              "price_basis": "YAHOO_AS_DELIVERED_AUTO_ADJUST_FALSE_BACK_ADJUST_FALSE_REPAIR_FALSE",
              "identity_basis": "SAME_HISTORY_RESPONSE_METADATA_NOT_HISTORICAL_ISIN_CERTIFICATION",
              "automatic_shadow_forbidden": True, "automatic_live_forbidden": True}
    result["artifact_sha256"] = base.digest(result)
    return result


def korean_report(result):
    report = base.korean_report(result)
    report = report.replace("재구성 자료로 제한된 대응 진단을 실행했지만, 종목 식별과 표본 부족으로 KR 규칙의 성과를 검증하지 못했습니다.",
                            "동일 응답의 주식 identity를 확인한 공개 시세로 재진단했습니다. 과거 legacy 기록의 탐색 비교이며 운영 규칙의 성과 검증은 아닙니다.")
    report = report.replace("코드별 suffix 식별 상태", "코드별 동일 응답 EQUITY 식별 상태")
    report = report.replace(".KS/.KQ 둘 다 유효하면 제외하고", ".KS/.KQ 둘 다 유효 EQUITY이면 제외하고")
    report = report.replace("H1~H4 family에 Bonferroni 98.75%", "v1/v2 H1~H4 family8에 Bonferroni 99.375%")
    prefix = ("# KR legacy ADX identity v2 · round 2\n\n"
              "- v1은 canonical symbol/type metadata가 없어 PROVIDER_IDENTITY_UNVERIFIED로 격리했습니다. 당시 2행 결과를 이미 보았으며 독립 holdout으로 주장하지 않습니다.\n"
              "- 새 108개 요청에서 OHLC와 같은 history 응답의 symbol/type/exchange/timezone/firstTradeDate를 결합했습니다. EQUITY만 허용하고 MUTUALFUND·미상·불일치는 제외했습니다.\n"
              "- firstTradeDate가 진입일 정규장 시작 이후이거나 없으면 제외합니다. 현재 provider identity이지 ISIN·과거 issuer 연속성 인증이 아닙니다.\n"
              "- H1~H4, 마감, 60봉 anchor, 원본 날짜 분할과 비용 규칙은 바꾸지 않았습니다. v1 원본 파일을 덮어쓰거나 새 metadata를 옛 OHLC에 붙이지 않았습니다.\n\n")
    prefix += "- 아래 승자 보존 값은 0~1 비율이며, 대응 차이의 단위는 %p입니다. 날짜별 검증 표본과 결측을 함께 해석해야 합니다.\n\n"
    return prefix + report


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
            raise ValueError("study_inputs_required")
        collection = json.loads(args.collection.read_text())
        if collection["registration_sha256"] != hashlib.sha256(args.registration.read_bytes()).hexdigest():
            raise ValueError("registration_changed")
        result = core.build_study(source, collection)
        with args.markdown.open("x", encoding="utf-8") as handle:
            handle.write(korean_report(result))
    base.write_new(args.output, result)
    print(json.dumps({"status": "collected" if args.collect else "studied", "artifact_sha256": result["artifact_sha256"],
                      "datasets": len(result.get("datasets", [])), "coverage": result.get("coverage")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
