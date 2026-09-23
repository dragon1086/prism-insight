"""Bounded, descriptive official macro evidence; never a trading regime signal."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from lxml import etree, html

CPI_URL = "https://www.bls.gov/news.release/cpi.nr0.htm"
BEA_INDEX = "https://www.bea.gov/news/current-releases"
TREASURY_URL = "https://home.treasury.gov/resource-center-data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
HOSTS = {"www.bls.gov", "api.bls.gov", "www.bea.gov", "home.treasury.gov", "fred.stlouisfed.org"}
BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0?startyear={start}&endyear={end}"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2,DGS10"
DATE_RE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
MONTH_RE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}"
MAX_BYTES = 3_000_000
TTL = 3600


def _allowed(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in HOSTS
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except (TypeError, ValueError):
        return False


class _Fetcher:
    def __init__(self):
        self.calls = 0

    def __call__(self, url):
        for _ in range(3):
            if not _allowed(url) or self.calls >= 9:
                raise ValueError("official source request boundary")
            self.calls += 1
            with requests.get(url, timeout=(3, 8), allow_redirects=False, stream=True) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                chunks, size, started = [], 0, time.monotonic()
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES or time.monotonic() - started > 10:
                        raise ValueError("official source response boundary")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise ValueError("redirect limit")


def _text(raw):
    tree = html.fromstring(raw)
    for node in tree.xpath("//script|//style|//nav|//footer"):
        node.drop_tree()
    return tree, " ".join(tree.text_content().split())


def _date(text):
    found = re.search(DATE_RE, text, re.IGNORECASE)
    return datetime.strptime(found.group(), "%B %d, %Y").replace(tzinfo=timezone.utc).date() if found else None


def _release(raw, kind, url, as_of):
    tree, text = _text(raw)
    if kind == "cpi":
        stamp = re.search(r"embargoed until(.{0,180})", text, re.IGNORECASE)
        period = re.search(r"CONSUMER PRICE INDEX\s*[-–]\s*(" + MONTH_RE + ")", text, re.IGNORECASE)
        start = text.find("The Consumer Price Index for All Urban Consumers")
    else:
        stamps = tree.xpath('//*[contains(@class,"field--name-field-release-date")]')
        stamp = stamps[0].text_content() if stamps else ""
        period = re.search(r"Personal Income and Outlays,\s*(" + MONTH_RE + ")", text, re.IGNORECASE)
        start = text.find("Personal income increased")
        if start < 0:
            start = text.find("Personal income decreased")
    stamp_text = stamp.group(1) if hasattr(stamp, "group") else stamp or ""
    publication = _date(stamp_text)
    now_et = datetime.now(ZoneInfo("America/New_York"))
    release_at = (datetime(publication.year, publication.month, publication.day, 8, 30,
                           tzinfo=ZoneInfo("America/New_York"))
                  if publication and re.search(r"8:30\s*a\.m\.\s*(?:\(ET\)|EDT|EST)", stamp_text) else None)
    same_day_released = (publication == as_of == now_et.date()
                         and release_at is not None and now_et >= release_at)
    if not publication or publication > as_of or (publication == as_of and not same_day_released) or not period or start < 0:
        return {"status": "not_found", "url": url, "reason": "unverified_or_not_prior_release"}
    observed_month = datetime.strptime(period.group(1), "%B %Y").replace(tzinfo=timezone.utc).date()
    if observed_month >= min(as_of.replace(day=1), publication.replace(day=1)) or (as_of - publication).days > 100:
        return {"status": "not_found", "url": url, "reason": "future_period_or_stale_release"}
    stop = text.find("Table A.", start) if kind == "cpi" else text.find("Personal Income and Related Measures", start)
    excerpt = text[start:stop if stop > start else start + 3000][:3000]
    if "percent" not in excerpt or (kind == "pce" and "PCE price index" not in excerpt):
        return {"status": "not_found", "url": url, "reason": "release_layout_unverified"}
    result = {"status": "released", "url": url, "published_date": publication.isoformat(),
              "observed_period": period.group(1), "excerpt": excerpt}
    if release_at:
        result["release_at"] = release_at.isoformat()
        result["release_at_basis"] = "source_explicit_08:30_America/New_York"
    upcoming = re.search(r"Next release:(.{0,220})", text, re.IGNORECASE) if kind == "pce" else re.search(
        r"Consumer Price Index for (.{0,220}?)scheduled to be released on (.{0,100})", text, re.IGNORECASE)
    if upcoming:
        snippet = upcoming.group(0)
        release_day = _date(snippet)
        if release_day and release_day > as_of:
            result["next_release"] = {"status": "not_yet_released", "date": release_day.isoformat(),
                                      "calendar_excerpt": snippet, "url": url}
    return result


def _cpi_api(raw, url, as_of):
    # Current revisions are not a historical vintage. Never backfill past as-of reports.
    if as_of != datetime.now(ZoneInfo("America/New_York")).date():
        raise ValueError("API has no publication vintage")
    payload = json.loads(raw)
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS API unavailable")
    series = payload["Results"]["series"][0]
    if series["seriesID"] != "CUUR0000SA0":
        raise ValueError("unexpected CPI series")
    points = {}
    for row in series["data"]:
        if not re.fullmatch(r"M(?:0[1-9]|1[0-2])", row.get("period", "")):
            continue
        try:
            observed = date(int(row["year"]), int(row["period"][1:]), 1)
            value = float(row["value"])
        except (ValueError, KeyError, TypeError):
            continue
        if observed < as_of.replace(day=1) and math.isfinite(value) and value > 0:
            points[observed] = value
    if not points:
        raise ValueError("no CPI observation")
    observed = max(points)
    if (as_of - observed).days > 100:
        raise ValueError("stale CPI observation")
    previous = points.get(observed.replace(year=observed.year - 1))
    result = {"status": "released", "url": url, "observed_period": observed.strftime("%B %Y"),
              "observed_date": observed.isoformat(), "publication_date_status": "unknown",
              "availability_basis": "official API observation available at captured_at; current revision, not historical vintage",
              "series_id": "CUUR0000SA0", "index_value": points[observed],
              "definition": "CPI-U all items US city average; not seasonally adjusted; index 1982-84=100"}
    if previous:
        result["derived_yoy_percent"] = round((points[observed] / previous - 1) * 100, 4)
        result["yoy_comparison_index"] = previous
        result["yoy_comparison_period"] = observed.replace(year=observed.year - 1).isoformat()
    return result


def _treasury(raw, url, as_of):
    root = etree.fromstring(raw, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    rows = {}
    for props in root.xpath('//*[local-name()="properties"]'):
        fields = {etree.QName(child).localname: child.text for child in props}
        try:
            observed = date.fromisoformat(fields["NEW_DATE"][:10])
            y2, y10 = float(fields["BC_2YEAR"]), float(fields["BC_10YEAR"])
        except (KeyError, TypeError, ValueError):
            continue
        if observed < as_of and (as_of - observed).days <= 10 and all(math.isfinite(v) and 0 <= v <= 50 for v in (y2, y10)):
            if observed in rows and rows[observed] != (y2, y10):
                return {"status": "not_found", "url": url, "reason": "conflicting_same_date_pair"}
            rows[observed] = (y2, y10)
    if not rows:
        return {"status": "not_found", "url": url, "reason": "no_recent_same_date_pair"}
    observed = max(rows)
    y2, y10 = rows[observed]
    return {"status": "released", "url": url, "observed_date": observed.isoformat(),
            "definition": "Daily Treasury Par Yield Curve Rates; percent; 10Y minus 2Y in percentage points",
            "yield_2y_percent": y2, "yield_10y_percent": y10, "spread_10y_minus_2y_pp": round(y10 - y2, 6)}


def _valid_cached(key, item, as_of, now):
    if not isinstance(item, dict) or item.get("status") != "released" or not _allowed(item.get("url", "")):
        return False
    try:
        captured = datetime.fromisoformat(item["captured_at"])
        item_age = (now - captured).total_seconds()
        stamp = date.fromisoformat(item.get("published_date", item.get("observed_date", "")))
    except (ValueError, TypeError, KeyError):
        return False
    if not 0 <= item_age < TTL or stamp > as_of:
        return False
    if key == "treasury":
        y2, y10, spread = (item.get(name) for name in ("yield_2y_percent", "yield_10y_percent", "spread_10y_minus_2y_pp"))
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (y2, y10, spread)):
            return False
        return stamp < as_of and 0 <= y2 <= 50 and 0 <= y10 <= 50 and abs(spread - (y10 - y2)) < 1e-5 and (as_of - stamp).days <= 10
    if key not in {"cpi", "pce"} or not isinstance(item.get("observed_period"), str):
        return False
    try:
        observed = datetime.strptime(item["observed_period"], "%B %Y").replace(tzinfo=timezone.utc).date()
    except ValueError:
        return False
    if observed >= as_of.replace(day=1):
        return False
    if item.get("series_id") == "CUUR0000SA0":
        value = item.get("index_value")
        return (as_of == now.astimezone(ZoneInfo("America/New_York")).date()
                and isinstance(value, (int, float)) and not isinstance(value, bool)
                and observed == stamp.replace(day=1)
                and math.isfinite(value) and value > 0 and (as_of - stamp).days <= 100)
    if observed >= stamp.replace(day=1):
        return False
    zone = ZoneInfo("America/New_York")
    release_at = item.get("release_at")
    if release_at:
        try:
            release_at = datetime.fromisoformat(release_at)
        except (ValueError, TypeError):
            return False
        expected = datetime(stamp.year, stamp.month, stamp.day, 8, 30, tzinfo=zone)
        if (release_at != expected or item.get("release_at_basis") != "source_explicit_08:30_America/New_York"
                or captured < release_at or now < release_at):
            return False
    elif stamp >= captured.astimezone(zone).date():
        return False
    if stamp == as_of and (release_at is None or as_of != now.astimezone(zone).date()):
        return False
    return isinstance(item.get("excerpt"), str) and bool(item["excerpt"]) and (as_of - stamp).days <= 100


def _fred_yields(raw, as_of, url=FRED_URL):
    if as_of != datetime.now(ZoneInfo("America/New_York")).date():
        raise ValueError("FRED current revisions are not historical vintages")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != ["observation_date", "DGS2", "DGS10"]:
        raise ValueError("unexpected FRED columns")
    rows = {}
    for row in reader:
        try:
            day = date.fromisoformat(row["observation_date"])
            y2, y10 = float(row["DGS2"]), float(row["DGS10"])
        except (TypeError, ValueError):
            continue
        if day < as_of and (as_of - day).days <= 10 and all(math.isfinite(v) and 0 <= v <= 50 for v in (y2, y10)):
            if day in rows and rows[day] != (y2, y10):
                raise ValueError("conflicting same-date FRED pair")
            rows[day] = (y2, y10)
    if not rows:
        raise ValueError("no recent same-date FRED pair")
    day = max(rows)
    y2, y10 = rows[day]
    return {"status": "released", "url": url,
            "series_urls": ["https://fred.stlouisfed.org/series/DGS2", "https://fred.stlouisfed.org/series/DGS10"],
            "observed_date": day.isoformat(), "publication_date_status": "unknown",
            "availability_basis": "FRED current revision available at captured_at, not historical vintage",
            "definition": "Federal Reserve H.15 via FRED; Treasury constant maturity market yields, investment basis, percent, not seasonally adjusted; 10Y minus 2Y in percentage points. Not the fetched Treasury par-curve source.",
            "yield_2y_percent": y2, "yield_10y_percent": y10, "spread_10y_minus_2y_pp": round(y10 - y2, 6)}


def collect_us_official_macro_sources(as_of=None, cache_dir=None):
    """Collect once per US-date/hour; failures are never persisted. No credentials."""
    as_of = date.fromisoformat(f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}" if len(as_of) == 8 else as_of) if isinstance(as_of, str) else as_of
    as_of = as_of or datetime.now(ZoneInfo("America/New_York")).date()
    now = datetime.now(timezone.utc)
    if as_of > now.astimezone(ZoneInfo("America/New_York")).date():
        return {"as_of": as_of.isoformat(), "captured_at": now.isoformat(), "sources": {}, "status": "invalid_future_as_of"}
    cached, path = {}, None
    if cache_dir is not None:
        path = Path(cache_dir) / f"official_macro_v1_{as_of.isoformat()}.json"
        try:
            packet = json.loads(path.read_text()) if path.stat().st_size <= MAX_BYTES else {}
            age = (now - datetime.fromisoformat(packet["captured_at"])).total_seconds()
            if packet["as_of"] == as_of.isoformat() and 0 <= age < TTL:
                for key, item in packet["sources"].items():
                    if _valid_cached(key, item, as_of, now):
                        cached[key] = item
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    fetch = _Fetcher()
    sources = dict(cached)
    for kind in ("cpi", "pce", "treasury"):
        if kind in sources:
            continue
        url = CPI_URL if kind == "cpi" else BEA_INDEX if kind == "pce" else TREASURY_URL.format(year=as_of.year)
        try:
            if kind == "pce":
                tree, _ = _text(fetch(url))
                links = [urljoin(url, a.get("href", "")) for a in tree.xpath("//a")
                         if re.fullmatch(r"Personal Income and Outlays,\s*" + MONTH_RE, a.text_content().strip(), re.IGNORECASE)]
                if not links:
                    raise ValueError("release link not found")
                url = links[0]
            raw = fetch(url)
            sources[kind] = _treasury(raw, url, as_of) if kind == "treasury" else _release(raw, kind, url, as_of)
            sources[kind]["captured_at"] = datetime.now(timezone.utc).isoformat()
        except (requests.RequestException, ValueError, etree.Error):
            sources[kind] = {"status": "not_found", "url": url, "reason": "official_fetch_or_parse_unavailable"}
        if kind == "cpi" and sources[kind]["status"] != "released" and as_of == now.astimezone(ZoneInfo("America/New_York")).date():
            try:
                api_url = BLS_API.format(start=as_of.year - 1, end=as_of.year)
                sources[kind] = _cpi_api(fetch(api_url), api_url, as_of)
                sources[kind]["captured_at"] = datetime.now(timezone.utc).isoformat()
            except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
                pass
        if kind == "treasury" and sources[kind]["status"] != "released" and as_of == now.astimezone(ZoneInfo("America/New_York")).date():
            try:
                fred_url = f"{FRED_URL}&cosd={(as_of - timedelta(days=10)).isoformat()}&coed={as_of.isoformat()}"
                sources[kind] = _fred_yields(fetch(fred_url), as_of, fred_url)
                sources[kind]["captured_at"] = datetime.now(timezone.utc).isoformat()
            except (requests.RequestException, ValueError, KeyError, TypeError):
                pass
    result = {"as_of": as_of.isoformat(), "captured_at": datetime.now(timezone.utc).isoformat(), "sources": sources}
    if path:
        successful = {k: v for k, v in sources.items() if v["status"] == "released"}
        if successful:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({**result, "sources": successful}, ensure_ascii=False))
            except OSError:
                pass
    return result


def render_us_official_macro_sources(packet, language="ko"):
    """Source text is evidence, not instructions, forecasts, or a regime label."""
    lines = ["## Official US macro source evidence", f"As of US date: {packet.get('as_of')}",
             ("Retain publication/observation period, seasonal adjustment and MoM/YoY definitions. "
              "not_found means collection unavailable, NOT unreleased. Do not infer a trading regime.")]
    for key, item in packet.get("sources", {}).items():
        if item.get("status") != "released":
            label = "공식 자료 수집·해독 미완료 (미발표를 뜻하지 않음)" if language == "ko" else "Official retrieval/decoding unavailable (not evidence of non-release)"
            lines.append(f"### {key.upper()}\n{label}\nSource: {item.get('url', '')}")
        else:
            lines.append(f"### {key.upper()}\n{json.dumps(item, ensure_ascii=False)}")
    return "\n".join(lines)
