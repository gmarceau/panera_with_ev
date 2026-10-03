#!/usr/bin/env python3
"""
Finds Panera Bread locations in New England and the mid-Atlantic within
walking distance of a fast EV charger. The charger networks and the other
main search parameters live in config.yml, validated with pydantic on
load. Outputs a YAML file of matches (see PaneraChargerApp --help).
"""

import json
import os
import re
import time
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import requests
import yaml
from plumbum import cli, local
from plumbum.cli.terminal import Progress
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UA = {"User-Agent": "panera-supercharger-finder (personal project)"}

# Free key from openchargemap.org — required for regular/automated use.
# Override with the OCM_API_KEY env var instead of editing this if you'd
# rather not keep a key in source.
OCM_API_KEY = os.environ.get("OCM_API_KEY", "a4aa4b45-a754-4380-a058-900687637713")

CONFIG_FILE = Path(__file__).with_name("config.yml")


class Network(BaseModel):
    """One charger network: its OpenChargeMap operator IDs."""
    model_config = ConfigDict(extra="forbid")

    operator_ids: tuple[int, ...] = Field(min_length=1)


class SearchConfig(BaseModel):
    """The main search parameters, as loaded from config.yml.

    Every field is validated so a typo'd or out-of-range config.yml fails
    immediately with a field-by-field error instead of silently matching
    nothing. extra="forbid" catches misspelled keys that would otherwise
    be silently ignored in favor of a default.
    """
    model_config = ConfigDict(extra="forbid")

    overpass_url: str
    ocm_url: str
    target_states: dict[str, str] = Field(min_length=1)   # ISO 3166-2 code -> state name
    search_bbox: tuple[float, float, float, float]        # min_lat, min_lon, max_lat, max_lon
    networks: dict[str, Network] = Field(min_length=1)
    default_networks: list[str] = Field(min_length=1)
    dc_fast_level_id: int = Field(ge=1)                   # ChargerTypes level; 3 = DC fast
    min_kw: float = Field(ge=0)
    walk_miles: float = Field(gt=0)
    max_results: int = Field(gt=0)
    cache_file: str
    cache_max_age_seconds: int = Field(gt=0)
    output_file: str

    @field_validator("overpass_url", "ocm_url")
    @classmethod
    def _require_http_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("target_states")
    @classmethod
    def _require_iso_state_codes(cls, v):
        for code, name in v.items():
            if not re.fullmatch(r"[A-Z]{2}-[A-Z0-9]{1,3}", code):
                raise ValueError(f"{code!r} is not an ISO 3166-2 code like 'US-CT'")
            if not name.strip():
                raise ValueError(f"state name for {code!r} is empty")
        return v

    @model_validator(mode="after")
    def _cross_check(self):
        min_lat, min_lon, max_lat, max_lon = self.search_bbox
        if not -90 <= min_lat < max_lat <= 90:
            raise ValueError(f"bad search_bbox {self.search_bbox}: "
                             "need -90 <= min_lat < max_lat <= 90")
        if not -180 <= min_lon < max_lon <= 180:
            raise ValueError(f"bad search_bbox {self.search_bbox}: "
                             "need -180 <= min_lon < max_lon <= 180")
        unknown = [n for n in self.default_networks if n not in self.networks]
        if unknown:
            raise ValueError(f"default_networks {unknown} are not defined in networks")
        return self


def load_config(path=CONFIG_FILE):
    """Read config.yml and validate it with pydantic.

    Raises pydantic.ValidationError — one message per bad field — if the
    file is malformed, missing a required parameter, or contains a value
    that can't be right (e.g. an inverted bounding box or a default
    network with no operator IDs defined).
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    return SearchConfig.model_validate(data)


CONFIG = load_config()

# State matching for OpenChargeMap's StateOrProvince, which carries either
# the two-letter abbreviation or the full state name.
STATE_ABBREVS = {code.rsplit("-", 1)[-1].lower() for code in CONFIG.target_states}
STATE_NAMES = {name.strip().lower() for name in CONFIG.target_states.values()}


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(min(1.0, sqrt(a)))


def in_target_states(state: str) -> bool:
    s = (state or "").strip().lower()
    return s in STATE_ABBREVS or s in STATE_NAMES


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
        return ",".join(str(i) for n in networks for i in CONFIG.networks[n].operator_ids)
    except KeyError as exc:
        raise ValueError(
            f"unknown charger network {exc.args[0]!r}; "
            f"known networks: {sorted(CONFIG.networks)}"
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
        r = _request_with_retry("POST", CONFIG.overpass_url, data={"data": q},
                                 headers=UA, timeout=180)
        data = r.json()
        if "remark" not in data:
            return data
        if attempt == attempts - 1:
            raise RuntimeError(f"Overpass query failed: {data['remark']}")
        time.sleep(backoff)


def fetch_paneras(refresh=False):
    """Panera locations in the target states, from OpenStreetMap.

    Cached to the configured cache_file for cache_max_age_seconds so a
    later failure in the pipeline (e.g. the OpenChargeMap call) doesn't
    force re-running all the slow, rate-limited Overpass queries. Pass
    refresh=True (or delete the cache file) to force an immediate refresh.
    """
    cache = local.path(CONFIG.cache_file)
    if not refresh and cache_is_fresh(cache, CONFIG.cache_max_age_seconds):
        age_h = (time.time() - cache.stat().st_mtime) / 3600
        print(f"  (using {CONFIG.cache_file}, {age_h:.1f}h old)")
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
    states = list(CONFIG.target_states)
    for code in Progress(states, length=len(states)):
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


def fetch_chargers(networks=None):
    """Fast (DC) chargers for the given networks in the target states, from OpenChargeMap."""
    if networks is None:
        networks = CONFIG.default_networks
    lat1, lon1, lat2, lon2 = CONFIG.search_bbox
    params = {
        "output": "json",
        "countrycode": "US",
        # The live API silently returns zero results for the flat
        # "minLat,minLon,maxLat,maxLon" form some docs describe — verified
        # empirically it needs the parenthesized corner-pair form instead.
        "boundingbox": f"({lat1},{lon1}),({lat2},{lon2})",
        "operatorid": operator_ids_for(networks),    # server-side "is this one of our networks"
        "levelid": str(CONFIG.dc_fast_level_id),     # server-side "is this DC fast"
        "maxresults": CONFIG.max_results,
    }
    headers = {**UA, "X-API-Key": OCM_API_KEY}
    r = _request_with_retry("GET", CONFIG.ocm_url, params=params, headers=headers, timeout=120)
    data = r.json()
    if len(data) >= params["maxresults"]:
        print(f"  WARNING: OCM returned {len(data)} POIs, the requested max —"
              " results may be truncated, raise max_results in config.yml")

    found = []
    for poi in data:
        a = poi.get("AddressInfo") or {}
        if not in_target_states(a.get("StateOrProvince")):
            continue
        # Belt-and-suspenders on top of the server-side levelid filter: also
        # require a connection actually rated at fast-charging power, in
        # case a POI's LevelID is stale relative to its connections.
        conns = poi.get("Connections") or []
        if not any((c.get("PowerKW") or 0) >= CONFIG.min_kw for c in conns):
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


def find_matches(paneras, chargers, max_miles=CONFIG.walk_miles):
    matches = []
    for p in paneras:
        for c in chargers:
            d = haversine_miles(p["lat"], p["lon"], c["lat"], c["lon"])
            if d <= max_miles:
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
    print("Fetching Panera locations (OpenStreetMap)...")
    paneras = fetch_paneras(refresh=refresh)
    print(f"  {len(paneras)} Paneras")

    print("Fetching fast chargers (OpenChargeMap)...")
    chargers = fetch_chargers(networks)
    print(f"  {len(chargers)} chargers")

    matches = find_matches(paneras, chargers, max_miles=walk_miles)
    print(f"\n{len(matches)} Panera/charger pairs within {walk_miles} mi:\n")
    for m in matches:
        print(f"  {m['distance_mi']:.2f} mi | {m['panera_name']}"
              f" ({m['panera_address']})  <->  {m['charger_title']} [{m['network']}]")

    if matches:
        write_matches_yaml(matches, output_path)
        print(f"\nWrote {output_path}")


class PaneraChargerApp(cli.Application):
    """Find Panera Bread stores near fast EV chargers in New England and the mid-Atlantic."""

    walk_miles = cli.SwitchAttr(
        "--walk-miles", float, default=CONFIG.walk_miles,
        help="Match distance cutoff, in miles")
    networks = cli.SwitchAttr(
        "--networks", str, default=",".join(CONFIG.default_networks),
        help=f"Comma-separated charger networks to include "
             f"(known: {', '.join(sorted(CONFIG.networks))})")
    output = cli.SwitchAttr(
        "--output", str, default=CONFIG.output_file,
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


if __name__ == "__main__":
    PaneraChargerApp.run()
