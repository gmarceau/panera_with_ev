#!/usr/bin/env python3
"""
Finds Panera Bread locations in New England within walking distance
of a Tesla supercharger. Outputs panera_superchargers.csv.

pip install requests
"""

import csv
import json
import os
import time
from math import asin, cos, radians, sin, sqrt

import requests
import yaml
from plumbum import cli, local

OVERPASS = "https://overpass-api.de/api/interpreter"
OCM = "https://api.openchargemap.io/v3/poi/"
UA = {"User-Agent": "panera-supercharger-finder (personal project)"}

# Free key from openchargemap.org — required for regular/automated use.
# Override with the OCM_API_KEY env var instead of editing this if you'd
# rather not keep a key in source.
OCM_API_KEY = os.environ.get("OCM_API_KEY", "a4aa4b45-a754-4380-a058-900687637713")

NE_STATES = ["US-CT", "US-ME", "US-MA", "US-NH", "US-RI", "US-VT"]  # OSM area codes
NE_ABBREVS = {"ct", "ma", "me", "nh", "ri", "vt"}
NE_NAMES = {"connecticut", "massachusetts", "maine", "new hampshire",
            "rhode island", "vermont"}
NE_BBOX = (40.95, -73.75, 47.50, -66.90)   # min_lat, min_lon, max_lat, max_lon

# OCM referencedata IDs (https://api.openchargemap.io/v3/referencedata/),
# looked up rather than guessed. Filtering on these server-side replaces
# fragile "tesla"/"supercharger" substring matching on operator/title text.
# Tesla: 23 = "Tesla (Tesla-only charging)", 3534 = "Tesla (including
# non-tesla)" (NACS-opened Superchargers). EVgo: 15 = "eVgo Network",
# 3252 = "NRG EVgo" (EVgo's prior brand name).
NETWORK_OPERATOR_IDS = {
    "tesla": "23,3534",
    "evgo": "15,3252",
}
# ChargerTypes level 3 = "High (Over 40kW)", i.e. DC fast charging — this
# excludes Level 1/2 destination/wall chargers without relying on each
# connection's (often missing) PowerKW value.
DC_FAST_LEVEL_ID = "3"

WALK_MILES = 0.5    # "same mall/plaza" cutoff
MIN_KW = 50         # below this it's a Tesla *destination* charger, not a supercharger

PANERA_CACHE_FILE = "paneras_cache.json"
PANERA_CACHE_MAX_AGE = 24 * 3600  # seconds; delete the file to force a refresh sooner


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(min(1.0, sqrt(a)))


def in_new_england(state: str) -> bool:
    s = (state or "").strip().lower()
    return s in NE_ABBREVS or s in NE_NAMES


def cache_is_fresh(path, max_age):
    """True if path exists and was modified within max_age seconds."""
    p = local.path(path)
    if not p.exists():
        return False
    return (time.time() - p.stat().st_mtime) < max_age


def operator_ids_for(networks):
    """Comma-joined OCM operator IDs for the given network names.

    e.g. operator_ids_for(["tesla", "evgo"]) -> "23,3534,15,3252"
    """
    try:
        return ",".join(NETWORK_OPERATOR_IDS[n] for n in networks)
    except KeyError as exc:
        raise ValueError(
            f"unknown charger network {exc.args[0]!r}; "
            f"known networks: {sorted(NETWORK_OPERATOR_IDS)}"
        ) from exc


def _request_with_retry(method, url, *, attempts=3, backoff=15, **kwargs):
    """Both Overpass and OpenChargeMap rate-limit; retry either the same way."""
    last_exc = None
    for attempt in range(attempts):
        try:
            r = requests.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(backoff)
    raise last_exc


def _overpass_query(q, attempts=3, backoff=15):
    """POST an Overpass QL query, retrying on Overpass's own server-side
    query timeout as well as on network errors.

    A timed-out query comes back as HTTP 200 with a top-level "remark" field
    instead of a real "elements" list — no exception, no non-2xx status — so
    _request_with_retry's HTTP-level retry never notices and the caller would
    silently treat it as "zero results for this state". Confirmed live: a
    whole-state query for Rhode Island timed out this way and came back
    empty despite a real Panera Bread existing at Providence Place Mall.
    """
    for attempt in range(attempts):
        r = _request_with_retry("POST", OVERPASS, data={"data": q},
                                 headers=UA, timeout=180)
        data = r.json()
        if "remark" not in data:
            return data
        if attempt == attempts - 1:
            raise RuntimeError(f"Overpass query failed: {data['remark']}")
        time.sleep(backoff)


def fetch_paneras():
    """Panera locations in the six New England states, from OpenStreetMap.

    Cached to PANERA_CACHE_FILE for PANERA_CACHE_MAX_AGE seconds so a later
    failure in the pipeline (e.g. the OpenChargeMap call) doesn't force
    re-running all six slow, rate-limited Overpass queries. Delete the cache
    file to force an immediate refresh.
    """
    cache = local.path(PANERA_CACHE_FILE)
    if cache_is_fresh(cache, PANERA_CACHE_MAX_AGE):
        age_h = (time.time() - cache.stat().st_mtime) / 3600
        print(f"  (using {PANERA_CACHE_FILE}, {age_h:.1f}h old)")
        return json.loads(cache.read())

    query_tpl = """
    [out:json][timeout:120];
    area["ISO3166-2"="{code}"][admin_level=4]->.a;
    (
      nwr["brand"="Panera Bread"](area.a);
      nwr["name"~"Panera Bread",i](area.a);
    );
    out center;
    """
    paneras, seen = [], set()
    for code in NE_STATES:
        q = query_tpl.format(code=code)
        data = _overpass_query(q)
        for el in data.get("elements", []):
            pt = el.get("center") or el              # nodes carry lat/lon directly
            if "lat" not in pt:
                continue
            key = (round(pt["lat"], 5), round(pt["lon"], 5))
            if key in seen:
                continue
            seen.add(key)
            t = el.get("tags", {})
            addr = ", ".join(x for x in [
                " ".join(filter(None, [t.get("addr:housenumber"),
                                       t.get("addr:street")])),
                t.get("addr:city"), t.get("addr:state")] if x)
            paneras.append({"name": t.get("name", "Panera Bread"),
                            "address": addr or "(no address in OSM)",
                            "lat": pt["lat"], "lon": pt["lon"]})
        time.sleep(10)                                # be polite to the free API

    cache.write(json.dumps(paneras))
    return paneras


def fetch_chargers(networks=("tesla", "evgo")):
    """Fast (DC) chargers for the given networks in New England, from OpenChargeMap."""
    lat1, lon1, lat2, lon2 = NE_BBOX
    params = {
        "output": "json",
        "countrycode": "US",
        # The live API silently returns zero results for the flat
        # "minLat,minLon,maxLat,maxLon" form some docs describe — verified
        # empirically it needs the parenthesized corner-pair form instead.
        "boundingbox": f"({lat1},{lon1}),({lat2},{lon2})",
        "operatorid": operator_ids_for(networks),  # server-side "is this one of our networks"
        "levelid": DC_FAST_LEVEL_ID,                # server-side "is this DC fast"
        "maxresults": 2000,
    }
    headers = {**UA, "X-API-Key": OCM_API_KEY}
    r = _request_with_retry("GET", OCM, params=params, headers=headers, timeout=120)
    data = r.json()
    if len(data) >= params["maxresults"]:
        print(f"  WARNING: OCM returned {len(data)} POIs, the requested max —"
              " results may be truncated, raise maxresults")

    found = []
    for poi in data:
        a = poi.get("AddressInfo") or {}
        if not in_new_england(a.get("StateOrProvince")):
            continue
        # Belt-and-suspenders on top of the server-side levelid filter: also
        # require a connection actually rated at fast-charging power, in
        # case a POI's LevelID is stale relative to its connections.
        conns = poi.get("Connections") or []
        if not any((c.get("PowerKW") or 0) >= MIN_KW for c in conns):
            continue
        if (poi.get("StatusType") or {}).get("IsOperational") is False:
            continue
        lat, lon = a.get("Latitude"), a.get("Longitude")
        if lat is None or lon is None:
            continue
        found.append({"title": a.get("Title") or "Fast charger",
                      "address": ", ".join(x for x in [a.get("AddressLine1"),
                                     a.get("Town"), a.get("StateOrProvince")] if x),
                      "network": (poi.get("OperatorInfo") or {}).get("Title"),
                      "lat": lat, "lon": lon})
    return found


def find_matches(paneras, chargers):
    matches = []
    for p in paneras:
        for c in chargers:
            d = haversine_miles(p["lat"], p["lon"], c["lat"], c["lon"])
            if d <= WALK_MILES:
                matches.append({
                    "panera_name": p["name"], "panera_address": p["address"],
                    "panera_lat": p["lat"], "panera_lon": p["lon"],
                    "charger_title": c["title"], "charger_address": c["address"],
                    "charger_lat": c["lat"], "charger_lon": c["lon"],
                    "network": c.get("network"),
                    "distance_mi": round(d, 2),
                })
    return sorted(matches, key=lambda m: m["distance_mi"])


def write_matches_yaml(matches, path):
    """Write matches as YAML to path (str or path-like)."""
    local.path(path).write(yaml.safe_dump(matches, sort_keys=False))


def run_pipeline(walk_miles, networks, output_path, refresh):
    """Fetch, match, and write output. Implemented in slice 8."""
    raise NotImplementedError


class PaneraChargerApp(cli.Application):
    """Find Panera Bread stores near fast EV chargers in New England."""

    walk_miles = cli.SwitchAttr(
        "--walk-miles", float, default=WALK_MILES,
        help="Match distance cutoff, in miles")
    networks = cli.SwitchAttr(
        "--networks", str, default="tesla,evgo",
        help="Comma-separated charger networks to include")
    output = cli.SwitchAttr(
        "--output", str, default="panera_chargers.yml",
        help="Output YAML file path")
    refresh = cli.Flag(
        "--refresh", default=False,
        help="Ignore the Panera cache and re-fetch from OpenStreetMap")

    def main(self):
        run_pipeline(
            walk_miles=self.walk_miles,
            networks=self.networks.split(","),
            output_path=self.output,
            refresh=self.refresh,
        )


def main():
    print("Fetching Panera locations (OpenStreetMap)...")
    paneras = fetch_paneras()
    print(f"  {len(paneras)} Paneras")

    print("Fetching fast chargers (OpenChargeMap)...")
    chargers = fetch_chargers()
    print(f"  {len(chargers)} chargers")

    matches = find_matches(paneras, chargers)
    print(f"\n{len(matches)} Panera/supercharger pairs within {WALK_MILES} mi:\n")
    for m in matches:
        print(f"  {m['distance_mi']:.2f} mi | {m['panera_name']}"
              f" ({m['panera_address']})  <->  {m['charger_title']}")

    if matches:
        with open("panera_superchargers.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(matches[0].keys()))
            w.writeheader()
            w.writerows(matches)
        print("\nWrote panera_superchargers.csv")


if __name__ == "__main__":
    main()
