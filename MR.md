# Code Review: `panera_tesla.py`

## Status: fixes applied and verified live (2026-09-07)

Findings #1, #2, #3, #5, #6 below were fixed; #4 (title/PowerKW false
negatives) and the 0.5 mi match radius were reviewed and left as-is by
request. All changes were run end-to-end against the real OpenChargeMap and
Overpass APIs, not just unit-level.

- **#1 (no API key):** fixed — requests now send `X-API-Key`
  (`OCM_API_KEY`, overridable via the `OCM_API_KEY` env var).
- **#2 (unfiltered fetch + truncation risk):** fixed — `fetch_superchargers`
  now filters server-side with `operatorid=23,3534` (OCM referencedata:
  Tesla-only + Tesla-including-non-Tesla) and `levelid=3` (DC fast, >40kW),
  looked up from `/v3/referencedata/` rather than guessed. A truncation
  warning is logged if the response hits `maxresults`.
- **#3 (title-substring false positives):** fixed — the "tesla"/"supercharger"
  substring checks on operator/title text are gone; "is this Tesla" is now
  answered by the exact `operatorid` match above. The `PowerKW`-based
  `MIN_KW` check is kept as a secondary sanity filter, without the title
  bypass that made it able to admit non-Tesla POIs.
- **#5 (unguarded lat/lon):** fixed — `AddressInfo.Latitude/Longitude` are
  read with `.get()` and the POI is skipped (not crashed on) if either is
  missing.
- **#6 (no retry / no caching):** fixed — a shared `_request_with_retry`
  helper now backs both the Overpass and OCM calls, and `fetch_paneras()`
  caches its result to `paneras_cache.json` for 24h.

**Two additional bugs surfaced only by live testing** (not visible from
reading the code):

1. **`boundingbox` format was silently broken.** The original
   `"minLat,minLon,maxLat,maxLon"` flat form — which multiple docs describe
   as valid — makes the live OCM API return HTTP 200 with an empty list, no
   error. It needs the parenthesized `"(lat,lon),(lat,lon)"` form. This means
   the script most likely never returned any superchargers at all before
   today, independent of every other issue in this review. Fixed by
   wrapping each corner in parens.
2. **Overpass server-side query timeout is invisible to HTTP-level retry
   logic.** A timed-out query comes back as HTTP 200 with a top-level
   `"remark"` field instead of `"elements"` — confirmed live when a
   whole-state query for Rhode Island timed out and came back with zero
   Paneras, despite a real one existing at 43 Providence Place, Providence,
   RI (the user's own example). `_request_with_retry` only catches network/
   HTTP-status errors, so this failure mode was silently swallowed as
   "0 results for this state." Fixed with a new `_overpass_query()` wrapper
   that checks for `"remark"` and retries.

**Verified end-to-end run** (after both API-format fixes): 117 Paneras found
across the six states (including 43 Providence Place, Providence, RI — it's
just not within 0.5 mi of a Supercharger, so it doesn't appear in the
matches), 210 real Tesla Superchargers found via the corrected server-side
filter, 39 Panera/Supercharger pairs within 0.5 mi written to
`panera_superchargers.csv`.

---

Reviewed: `panera_tesla.py` (162 lines) — the only file in the repo.
Scope: correctness, data-quality/robustness, and API-usage issues in the
Panera-list → Tesla-Supercharger-match pipeline. Two external claims below
(OCM boundingbox param order, OCM API key requirement) were checked against
OpenChargeMap's public docs/OpenAPI spec rather than assumed.

## Summary

The script is small and mostly well thought out (the Overpass retry/backoff
loop, the `IsOperational is False` check, the dedup-by-coordinate logic are
all deliberate and correct). The real risk is concentrated in
`fetch_superchargers()`: it fetches an **unfiltered, capped** set of charging
POIs and then relies on **fragile string matching** to identify Tesla
Superchargers, which can both silently drop real matches and silently admit
fake ones. There's also one unguarded field access that can crash a run after
several minutes of successful fetching.

---

## Findings (roughly most → least impactful)

### 1. Requests to OpenChargeMap don't send an API key (lines 89–98)

```python
params = {
    "output": "json",
    "countrycode": "US",
    "boundingbox": f"{lat1},{lon1},{lat2},{lon2}",
    "maxresults": 2000,
    # If OCM starts requiring a key, register free at openchargemap.org
    # and add "key": "...",
}
r = requests.get(OCM, params=params, headers=UA, timeout=120)
```

The comment treats an API key as a *future* possibility. Per OCM's current
docs/OpenAPI spec, the `key` param is nominally optional at the schema level,
but OCM's own guidance says a key **must** be supplied (via `key=` or
`X-API-Key`) for any regular/automated use, and anonymous traffic is the
first thing rate-limited or blocked. Since this script does exactly the kind
of "hit us for thousands of POIs" call that policy targets, it's likely to
degrade (partial results) or fail outright (401/403) for unlucky runs.

**Fix:** register a free OCM key and send it as `X-API-Key` (don't put it in
`params`/the URL where it'll land in logs).

### 2. Charger fetch has no server-side filtering, so `maxresults=2000` can silently truncate (lines 88–119)

```python
params = {
    "output": "json",
    "countrycode": "US",
    "boundingbox": f"{lat1},{lon1},{lat2},{lon2}",
    "maxresults": 2000,
    ...
}
r = requests.get(OCM, params=params, ...)
...
for poi in r.json():
    ...
    if "tesla" not in operator + " " + title and "supercharger" not in title:
        continue
```

This queries **every charging-network POI** (ChargePoint, EVgo, Blink, Tesla,
municipal L2s, etc.) in the whole six-state bounding box, then filters for
Tesla *after* the server has already applied `maxresults`. New England has
well over 2000 public EVSE POIs total across all networks; if the true count
in the bbox exceeds the cap, whichever subset the server happens to return
may quietly omit real Superchargers — and nothing in the code detects or
reports that this happened (contrast with `fetch_paneras`, which at least
prints a count you could sanity-check).

OCM's API supports exactly the server-side filters needed to avoid this:
`operatorid` (Tesla's operator id) and/or `connectiontypeid` (Tesla/NACS
connector). Filtering server-side would both fix the truncation risk and cut
the response size drastically.

**Fix:** add `operatorid`/`connectiontypeid` params (or at minimum log
`len(r.json())` vs `maxresults` so truncation is visible), rather than
filtering 2000 mixed-network rows down to a handful of Tesla ones client-side.

### 3. Title-substring match can admit non-Tesla stations as "Superchargers" (lines 103–112)

```python
operator = ((poi.get("OperatorInfo") or {}).get("Title") or "").lower()
title = (a.get("Title") or "").lower()
if "tesla" not in operator + " " + title and "supercharger" not in title:
    continue
...
is_fast = any((c.get("PowerKW") or 0) >= MIN_KW for c in conns)
if not (is_fast or "supercharger" in title):  # drop destination chargers
    continue
```

Both filters treat the substring `"supercharger"` in the POI **title** as
sufficient on its own, independent of operator. OCM is community-submitted
data; a mislabeled or generically-named POI (e.g. a third-party network
station someone titled "... Supercharger Hub") would pass stage 1 (operator
check never runs because the `or` short-circuits) and pass stage 2 regardless
of its actual power rating, because `"supercharger" in title` is `True`. It
would then show up in the final CSV looking exactly like a real Tesla
Supercharger.

**Fix:** require the operator match (or a Tesla `operatorid`, see #2) as a
hard condition, and use `"supercharger" in title` only to *additionally
include* borderline power-rating cases, not to bypass the operator check
entirely.

### 4. Real Superchargers can be silently dropped when connector metadata is incomplete (lines 109–112)

The flip side of #3: a legitimate Tesla Supercharger whose OCM entry has
missing/zero `PowerKW` on its connections (common for crowd-sourced data) and
whose title doesn't literally contain the word "Supercharger" (e.g. named
after the plaza it's in) will fail both `is_fast` and the title check and get
dropped with no log line — you'd never know it was excluded. Given the
dataset is user-submitted, this isn't a hypothetical edge case.

**Suggestion:** at minimum, log/count how many `"tesla" in operator`-matched
POIs get dropped by the `is_fast`/title check in #3, so silent data loss is
visible rather than a filtering pass quietly dropping rows without a trace.

### 5. Unguarded `a["Latitude"]` / `a["Longitude"]` can crash a run late (line 118)

```python
found.append({"title": a.get("Title") or "Tesla Supercharger",
              "address": ...,
              "lat": a["Latitude"], "lon": a["Longitude"]})
```

Every other field on this dict is read with `.get(...)`; these two are
direct-indexed. If any POI that passes the Tesla/supercharger filters has an
`AddressInfo` block missing coordinates (OCM does not guarantee every field
is populated), this throws `KeyError` and kills the whole script — after the
several minutes it took to run all six Overpass queries with their polite
`sleep(10)`s. Nothing upstream is cached, so a crash here means starting over
from scratch (see #6).

**Fix:** use `.get("Latitude")`/`.get("Longitude")`, skip and (ideally) log
the POI if either is `None`.

### 6. No retry and no caching for the OpenChargeMap fetch (lines 97–98 vs. 56–65)

`fetch_paneras()` wraps each Overpass call in a 3-attempt retry with backoff
because "Overpass rate-limits." `fetch_superchargers()` makes a single,
unretried `requests.get`. Given #1 (no key → more likely to be
rate-limited/blocked) this asymmetry means the run is most likely to fail
exactly at the step with no retry, after already paying the cost of the slow
Overpass loop. There's also no on-disk caching of the Panera list, so any
downstream failure means re-running the entire multi-minute fetch again.

**Suggestion:** give the OCM call the same retry treatment, and/or cache
`fetch_paneras()`'s output to a local JSON file so reruns during development
(or after a transient OCM failure) don't re-hit Overpass.

### 7. Matching is straight-line distance, not walkability (lines 25, 122–134)

```python
WALK_MILES = 0.5    # "same mall/plaza" cutoff
...
d = haversine_miles(p["lat"], p["lon"], c["lat"], c["lon"])
if d <= WALK_MILES:
```

This is a reasonable, cheap heuristic, but it's worth stating explicitly as a
known limitation rather than an implicit assumption: a Panera and a
Supercharger 0.4 mi apart but separated by a highway/river (not actually the
same plaza) will match, while two that are genuinely adjacent but whose
geocoded points land just over 0.5 mi apart (e.g. opposite ends of a big
parking lot) will be missed. Not a correctness bug, but the constant name
"same mall/plaza" overstates what a crow-flies radius check can actually
guarantee.

### 8. Minor / low-severity items

- **Coordinate-rounding dedup** (`fetch_paneras`, lines 70–73): dedup key is
  `(round(lat, 5), round(lon, 5))`, i.e. ~1 m precision. Fine in practice;
  flagged only because two distinct storefronts sharing one building
  centroid (e.g. an OSM way and a duplicate node tagged at the same point)
  would be merged into one — the more likely failure mode is actually the
  opposite (missed dedup) if the same store is tagged as both a node with
  slightly different coordinates and a way, which this key would *not* catch.
- **Bounding box margins are tight against actual state extremes**
  (line 23): e.g. Connecticut's westernmost point (~-73.73°) is only ~0.02°
  (~1 mi) inside the box's `-73.75` edge, and the box's `-66.90` eastern edge
  is *west* of West Quoddy Head, ME (~-66.885°), the easternmost point in the
  contiguous US — so the extreme tip of Lubec, ME is technically outside the
  query box. Unlikely to affect any real Panera/Supercharger pair, but worth
  widening by a few tenths of a degree for safety margin.
- **Filter readability** (lines 105, 111): the double-negative
  `if not (is_fast or "supercharger" in title): continue` and the mixed
  `A and B: continue` / `not (A or B): continue` styles for what are
  logically the same kind of "keep if X or Y" check make the Tesla-detection
  logic harder to audit than it needs to be — worth normalizing to one style
  given how much correctness (#3/#4) rides on getting this filter right.

---

## Suggested priority

1. Add an OCM API key (#1) — otherwise the rest may be moot if requests start
   failing.
2. Switch to server-side `operatorid`/`connectiontypeid` filtering (#2) to
   fix both the truncation risk and the title-substring false-positive/
   negative issues (#3, #4) in one change.
3. Guard the `Latitude`/`Longitude` access (#5) — cheap fix, prevents a
   late-run crash.
4. Add retry to the OCM call and consider caching `fetch_paneras()` output
   (#6) to make reruns cheaper while iterating on #1–#3.
