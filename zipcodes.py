"""Towns and rough drive times from ZIP codes, worked out on this Mac: nothing is looked up online.

The ZIP code table (data/zipcodes.tsv.gz) comes from GeoNames (geonames.org), licensed CC BY 4.0. A drive time is a
rough estimate: the straight-line distance between the two ZIP codes' centers, stretched a quarter for roads, at about
30 mph for the first 20 miles and highway speed after that. Traffic isn't included.
"""
import gzip
import math
from pathlib import Path

TABLE = Path(__file__).parent / "data" / "zipcodes.tsv.gz"
HOME_ZIP, HOME_NAME = "10522", "Children's Village"  # Dobbs Ferry, NY: where drive times are measured from
_table = None


def table():
    """{ZIP: (town, state, latitude, longitude)}, in ZIP order, read the first time it's needed."""
    global _table
    if _table is None:
        _table = {}
        with gzip.open(TABLE, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("#"):
                    code, town, state, latitude, longitude = line.rstrip("\n").split("\t")
                    _table[code] = (town, state, float(latitude), float(longitude))
    return _table


def miles_between(a, b):
    """Straight-line miles between two (town, state, latitude, longitude) entries."""
    (lat1, lon1), (lat2, lon2) = [(math.radians(p[2]), math.radians(p[3])) for p in (a, b)]
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 3959 * math.asin(math.sqrt(h))


def drive_minutes(code, home=HOME_ZIP):
    """A rough drive time between two ZIP codes, in minutes rounded to 5, or None if either isn't known."""
    a, b = table().get(home), table().get(str(code or "")[:5])
    if not a or not b:
        return None
    road = miles_between(a, b) * 1.25
    return max(5, int(round((min(road, 20) * 2 + max(road - 20, 0) * 0.95) / 5)) * 5)


def place(code):
    """{"zip", "town", "state", "minutes"} for a ZIP code, or None if it isn't known."""
    code = str(code or "").strip()[:5]
    found = table().get(code)
    return {"zip": code, "town": found[0], "state": found[1], "minutes": drive_minutes(code)} if found else None


def search(text, limit=8):
    """ZIP codes starting with the digits typed, or in towns starting with the words typed ("Yonkers" or
    "Yonkers, NY"), nearest to Children's Village first."""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    if text.isdigit():
        codes = [c for c in table() if c.startswith(text)][:limit]
    else:
        town, _, state = (part.strip().lower() for part in text.partition(","))
        home = table()[HOME_ZIP]
        codes = sorted((c for c, v in table().items() if v[0].lower().startswith(town) and (not state or v[1].lower().startswith(state))),
                       key=lambda c: miles_between(home, table()[c]))[:limit]
    return [place(c) for c in codes]
