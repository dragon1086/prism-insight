"""Explicit PUBLIC-only data acquisition for isolated scenario research bundles."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener,ProxyHandler,HTTPRedirectHandler

import pandas as pd

HOST = "https://api.bybit.com"
INTERVALS = {"30m":("30",1_800_000),"1h":("60",3_600_000),
             "4h":("240",14_400_000),"12h":("720",43_200_000),"1d":("D",86_400_000)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError("public_data_redirect_forbidden")


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _public_get(path,params):
    if path not in {"/v5/market/kline","/v5/market/mark-price-kline","/v5/market/funding/history"}:
        raise ValueError("public_endpoint_only")
    request=HOST+path+"?"+urlencode(dict(category="linear",symbol="BTCUSDT",**params))
    opener=build_opener(ProxyHandler({}),_NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(request,timeout=20) as response:
                data=json.loads(response.read(4_000_000))
            if data.get("retCode")!=0:
                raise ValueError("public_response_failed")
            time.sleep(.15)
            return data["result"]["list"]
        except Exception:
            if attempt==2:
                raise RuntimeError("public_market_download_failed") from None
            time.sleep(attempt+1)


def candles(interval,start,end,*,mark=False,get=_public_get):
    """Half-open UTC interval, strict pages and duplicates; no live DB writes."""
    rows={};cursor=end-1
    for _ in range(100):
        page=get("/v5/market/mark-price-kline" if mark else "/v5/market/kline",
                 {"interval":interval,"start":start,"end":cursor,"limit":1000})
        if not page:
            break
        times=[]
        for row in page:
            at=int(row[0]);times.append(at)
            if not start<=at<end or at>cursor:
                raise ValueError("candle_outside_request")
            item={"open_time":at,**dict(zip(("open","high","low","close"),map(float,row[1:5]))),
                  "volume":0. if mark else float(row[5])}
            if at in rows and rows[at]!=item:
                raise ValueError("conflicting_public_candle")
            rows[at]=item
        next_cursor=min(times)-1
        if next_cursor>=cursor:
            raise ValueError("candle_pagination_not_progressing")
        cursor=next_cursor
        if cursor<start:
            break
    else:
        raise ValueError("download_page_limit")
    return [rows[t] for t in sorted(rows)]


def create_bundle(start_ms,end_ms,*,get=_public_get):
    if start_ms%300000 or end_ms%300000 or not 0<end_ms-start_ms<=86400000:
        raise ValueError("aligned_window_up_to_one_day_required")
    source_from=(start_ms//86400000-1)*86400000
    source=candles("1",source_from,end_ms, get=get)
    mark=candles("1",source_from,end_ms,mark=True,get=get)
    warmup={tf:candles(interval,(start_ms//duration-60)*duration,end_ms//duration*duration,get=get)
            for tf,(interval,duration) in INTERVALS.items()}
    # Include real schedule anchors on both sides; future rates never enter LLM input.
    fund_start=source_from-86400000;fund_end=end_ms+12*3600000
    raw=get("/v5/market/funding/history",{"startTime":fund_start,"endTime":fund_end,"limit":200})
    if not raw or len(raw)>=200:
        raise ValueError("funding_schedule_unavailable_or_truncated")
    funding=[]
    for row in raw:
        if row.get("symbol")!="BTCUSDT":
            raise ValueError("wrong_funding_symbol")
        funding.append({"timestamp":int(row["fundingRateTimestamp"]),"rate":float(row["fundingRate"])})
    funding.sort(key=lambda r:r["timestamp"])
    if any(b["timestamp"]-a["timestamp"]!=8*3600000 for a,b in zip(funding,funding[1:])):
        raise ValueError("non_regular_funding_requires_explicit_contract")
    if not (funding[0]["timestamp"]<=start_ms and funding[-1]["timestamp"]>end_ms):
        raise ValueError("funding_schedule_does_not_bracket_run")
    from backtest.scenario_data import HistoricalScenarioData, _records_frame
    market=HistoricalScenarioData(_records_frame(source),source_interval_ms=60000,
        warmup={tf:_records_frame(rows) for tf,rows in warmup.items()},mark=_records_frame(mark),funding=funding)
    for at in (start_ms,end_ms):
        if not market.snapshot(at)["valid"]:
            raise ValueError("snapshot_warmup_incomplete")
    content=dict(schema_version=1,symbol="BTCUSDT",source_interval_ms=60000,
                 start_ms=start_ms,end_ms=end_ms,source=source,mark=mark,warmup=warmup,funding=funding,
                 funding_interval_ms=8*3600000)
    return {**content,"data_hash":digest(content),"coverage":market.manifest(),
            "provenance":{"source":HOST,"fetched_at":time.time(),"private_account_data":False}}


def load_bundle(path):
    value=json.loads(Path(path).read_text())
    content={k:v for k,v in value.items() if k not in {"data_hash","coverage","provenance"}}
    if digest(content)!=value.get("data_hash") or value.get("schema_version")!=1:
        raise ValueError("dataset_hash_or_schema_mismatch")
    start,end=value.get("start_ms"),value.get("end_ms")
    if (type(start) is not int or type(end) is not int or start%300000 or end%300000
            or not 0<end-start<=86400000 or value.get("symbol")!="BTCUSDT"
            or value.get("source_interval_ms")!=60000 or value.get("funding_interval_ms")!=28800000):
        raise ValueError("invalid_bundle_contract")
    funding=value.get("funding")
    if not isinstance(funding,list) or len(funding)<2:
        raise ValueError("funding_schedule_required")
    for row in funding:
        if (type(row.get("timestamp")) is not int or row["timestamp"]%28800000
                or type(row.get("rate")) not in (int,float) or not -1<row["rate"]<1):
            raise ValueError("funding_event_invalid")
    if (funding[0]["timestamp"]>start or funding[-1]["timestamp"]<=end
            or any(b["timestamp"]-a["timestamp"]!=28800000 for a,b in zip(funding,funding[1:]))):
        raise ValueError("funding_schedule_incomplete")
    from backtest.scenario_data import HistoricalScenarioData, _records_frame
    market=HistoricalScenarioData(_records_frame(value["source"]),source_interval_ms=value["source_interval_ms"],
        warmup={tf:_records_frame(rows) for tf,rows in value["warmup"].items()},
        mark=_records_frame(value["mark"]),funding=value["funding"])
    if not market.manifest()["mark"]["complete"] or not all(market.snapshot(t)["valid"] for t in (start,end)):
        raise ValueError("required_market_coverage_missing")
    return value,market


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start",required=True);parser.add_argument("--end",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError("never_overwrite_research_input")
    start,end=pd.Timestamp(args.start),pd.Timestamp(args.end)
    if start.tz is None or end.tz is None:
        raise ValueError("timezone_required")
    bundle=create_bundle(start.value//1_000_000,end.value//1_000_000)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(bundle,stream,sort_keys=True,allow_nan=False)
    print(json.dumps({"data_hash":bundle["data_hash"],"rows":len(bundle["source"]),"private_requests":0}))


if __name__=="__main__":
    main()
