"""KIS stock-master industry codes joined to KIS's official index master.

Layout reference: open-trading-api/stocks_info/업종코드정보.h (1/4/40 bytes).
These are KIS classifications, not an inferred company competitive ranking.
"""
from io import BytesIO
from threading import Lock
import zipfile

import requests

from cores.market_data.source import Unavailable, Unsupported
from prism_core.sector_names import normalize_kr_sector

_URL = "https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip"
_CACHE = {}
_LOCK = Lock()


def parse_sector_master(content: bytes) -> dict[tuple[str, str], str]:
    result = {}
    for row in content.splitlines():
        if len(row) < 45:
            continue
        division = row[:1].decode("ascii")
        code = row[1:5].decode("ascii")
        if division not in {"0", "1"} or not code.isdigit():
            continue
        name = row[5:45].decode("cp949").strip()
        if name:
            result[(division, code)] = name
    if not result:
        raise Unavailable("KIS index master contains no domestic sectors")
    return result


def get_sector_records(reference_date: str, market: str = "ALL") -> dict[str, dict]:
    from cores.kis_market_snapshot import fetch_kis_master_data
    if market not in {"ALL", "KOSPI", "KOSDAQ"}:
        raise Unsupported("Unsupported KIS sector market")
    master = fetch_kis_master_data()
    if reference_date != master.observed_date:
        raise Unsupported("Historical sector membership is not supplied by today's KIS master")
    with _LOCK:
        names = _CACHE.get(reference_date)
        if names is None:
            try:
                response = requests.get(_URL, timeout=20)
                response.raise_for_status()
                with zipfile.ZipFile(BytesIO(response.content)) as archive:
                    names = parse_sector_master(archive.read("idxcode.mst"))
            except Exception as exc:
                raise Unavailable("KIS index master unavailable") from exc
            _CACHE.clear()
            _CACHE[reference_date] = names
    result = {}
    for ticker, code in master.industry_codes.items():
        stock_market = master.markets.get(ticker)
        if market != "ALL" and stock_market != market:
            continue
        division = {"KOSPI": "0", "KOSDAQ": "1"}.get(stock_market)
        name = names.get((division, code))
        if name:
            result[ticker] = {
                "raw_sector": name, "sector": normalize_kr_sector(name),
                "industry_code": code, "source": "kis_master",
                "observed_date": master.observed_date,
                "coarse": name in {"금융", "제조", "출판·매체복제"},
            }
    return result


def get_sector_info(reference_date: str, market: str = "ALL") -> dict[str, str]:
    return {ticker: row["sector"] for ticker, row in get_sector_records(reference_date, market).items()}
