"""Exact sector/industry matching; narrow industry leadership never widens."""

SECTORS = frozenset({"Technology", "Healthcare", "Financial Services", "Consumer Cyclical",
                     "Consumer Defensive", "Energy", "Industrials", "Basic Materials",
                     "Real Estate", "Utilities", "Communication Services"})
INDUSTRY_SECTORS = {"Semiconductors": "Technology", "Semiconductor Equipment & Materials": "Technology",
                    "Software - Infrastructure": "Technology", "Software - Application": "Technology"}


class SectorMap(dict):
    """Backward-compatible ticker->sector mapping carrying already fetched industries."""

    def __init__(self):
        super().__init__()
        self.industries = {}


def leader_matches(leader, sector, industry=""):
    """An industry restriction wins even when an explicit parent sector is present."""
    name = str(leader.get("sector", "")).strip()
    narrow = str(leader.get("industry", "")).strip()
    if narrow:
        return bool(industry and narrow == industry and (not name or name == sector))
    if name in SECTORS:
        return name == sector
    # Legacy industry labels wrongly placed under sector remain narrow.
    return bool(name in INDUSTRY_SECTORS and name == industry and INDUSTRY_SECTORS[name] == sector)


def matched_leader(leading, sector_map, ticker):
    industry = getattr(sector_map, "industries", {}).get(ticker, "")
    return next((item for item in leading if leader_matches(item, sector_map.get(ticker, ""), industry)), None)
