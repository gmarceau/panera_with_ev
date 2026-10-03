# Panera Bread and Fast Charger Finder

This program finds Panera Bread stores in New England and the
mid-Atlantic (CT, ME, MA, NH, RI, VT, PA, NJ, NY, MD). It checks
each store's distance to the nearest fast EV charger — Tesla
Superchargers and EVgo stations by default. It keeps stores that are
close enough to walk to a charger.

## What You Need

- Python 3.10 or later
- [`uv`](https://docs.astral.sh/uv/) (recommended), or `pip`
- [Task](https://taskfile.dev) (recommended — `brew install go-task`),
  used to run the commands below
- An API key from openchargemap.org (free)

## Setup

1. Install the dependencies. With `uv`:

   ```
   uv sync
   ```

Without `uv`:

```
pip install requests pyyaml plumbum pydantic
```

2. Get a free API key from openchargemap.org. Set it as an
   environment variable:

   ```
   export OCM_API_KEY=your-key-here
   ```

   If you skip this step, the program uses a built-in key instead.

## How to Run It

The preferred way to drive the pipeline is with
[Task](https://taskfile.dev) (see `taskfile.yml`):

```
task run      # full pipeline: refresh caches, write panera_chargers.yml
task test     # run the test suite
task explore  # open the marimo map of the results
task clean    # delete the cache, the output, and Task's state
```

`task run` first refreshes the Panera location cache
(`paneras_cache.json`) if `config.yml` or the code changed, then
fetches live charger data from OpenChargeMap and rewrites
`panera_chargers.yml`. Pass program options after `--`, e.g.:

```
task run -- --networks evgo --walk-miles 0.25
```

Without Task, run the program directly:

```
uv run panera_tesla.py
```

(or `python panera_tesla.py` if you installed the dependencies with
`pip` instead of `uv`.)

The program does four things:

1. It downloads the list of Panera Bread stores in the ten target
   states. It gets this list from OpenStreetMap.
2. It downloads the list of fast chargers in the same states — Tesla
   Superchargers and EVgo stations by default. It gets this list
   from OpenChargeMap.
3. It compares the two lists. It keeps each Panera store that sits
   close enough to walk to a charger.
4. It writes the matches to a YAML file, `panera_chargers.yml` by
   default.

The program also prints each match to the screen as it works, with
a progress bar while it downloads Panera data (the slow part).

### Command-line options

```
--walk-miles VALUE    How close counts as "walkable." Default: 0.5
--networks VALUE      Comma-separated charger networks. Default: every
                      network in config.yml (tesla, evgo, rivian, mercedes,
                      applegreen, shell, totalenergies)
--output PATH         Where to write the YAML file. Default: panera_chargers.yml
--refresh             Ignore the Panera cache and re-fetch from OpenStreetMap
```

Example: only EVgo, within a quarter mile:

```
uv run panera_tesla.py --networks evgo --walk-miles 0.25
```

Run `uv run panera_tesla.py --help` to see this list from the
program itself.

## Configuration (`config.yml`)

The main search parameters live in `config.yml` next to the script:
the target states, the search bounding box, the walking distance,
the minimum charger power, the cache and output files, and the
charger networks with their OpenChargeMap operator IDs. The file is
validated with [pydantic](https://docs.pydantic.dev/) when the
program loads it — a missing parameter, an inverted bounding box, or
a typo'd key fails immediately with a field-by-field error instead
of silently matching nothing.

Available charger networks (each maps to verified OpenChargeMap
operator IDs, listed with their meaning in `config.yml`):

| Network | Notes |
|---|---|
| `tesla` | Superchargers, both Tesla-only and NACS-opened (default) |
| `evgo` | includes the old "NRG EVgo" brand (default) |
| `rivian` | Rivian Adventure Network (DC fast; Waypoints L2 excluded) |
| `mercedes` | Mercedes-Benz High-Power Charging (US) |
| `applegreen` | Applegreen Fast Charge (US travel plazas) |
| `shell` | Shell Recharge Solutions (US) |
| `totalenergies` | European operator; no US stations in OpenChargeMap, so it returns nothing under the US filter |

Example: every network at once:

```
uv run panera_tesla.py --networks tesla,evgo,rivian,mercedes,applegreen,shell,totalenergies
```

## About the First Run

The first run takes a few minutes. OpenStreetMap answers queries
slowly. The program saves the Panera list to `paneras_cache.json`.
It reuses this file for 24 hours. Pass `--refresh`, or delete this
file, to force a fresh download.

If a network request fails, the program tries again. It makes up to
three attempts before it gives up.

## Output File

The YAML file holds one entry per match. Each entry shows:

- The Panera store's name and address
- The nearby charger's name, address, and network
- The distance between them, in miles

## Exploring the Results on a Map

`explore_map.py` is a [marimo](https://marimo.io) notebook that
shows the matches on an OpenStreetMap-based map, with a slider to
narrow the distance shown. It reads the YAML file above, so run
`panera_tesla.py` at least once first.

```
uv run marimo edit explore_map.py    # interactive, editable
uv run marimo run explore_map.py     # read-only app view
```

## Limits to Know

- Distance is a straight line, not a walking route. A store across a
  highway from a charger may still count as "within walking
  distance," even if you cannot walk there directly.
- Store and charger data come from public, crowd-sourced maps. Some
  entries may be missing or out of date.
