# Panera Bread and Tesla Supercharger Finder

This program finds Panera Bread stores in New England. It checks
each store's distance to the nearest Tesla Supercharger. It keeps
stores that are within half a mile of a Supercharger.

## What You Need

- Python 3.8 or later
- The `requests` library
- An API key from openchargemap.org (free)

## Setup

1. Install the `requests` library. Run this command:

   ```
   pip install requests
   ```

2. Get a free API key from openchargemap.org. Set it as an
   environment variable:

   ```
   export OCM_API_KEY=your-key-here
   ```

   If you skip this step, the program uses a built-in key instead.

## How to Run It

Run this command:

```
python panera_tesla.py
```

The program does four things:

1. It downloads the list of Panera Bread stores in six New England
   states. It gets this list from OpenStreetMap.
2. It downloads the list of Tesla Superchargers in the same states.
   It gets this list from OpenChargeMap.
3. It compares the two lists. It keeps each Panera store that sits
   within half a mile of a Supercharger.
4. It writes the matches to a file named `panera_superchargers.csv`.

The program also prints each match to the screen as it works.

## About the First Run

The first run takes a few minutes. OpenStreetMap answers queries
slowly. The program saves the Panera list to `paneras_cache.json`.
It reuses this file for 24 hours. To force a fresh download, delete
this file.

If a network request fails, the program tries again. It makes up to
three attempts before it gives up.

## Output File

`panera_superchargers.csv` holds one row per match. Each row shows:

- The Panera store's name and address
- The nearby Supercharger's name and address
- The distance between them, in miles

## Limits to Know

- Distance is a straight line, not a walking route. A store across a
  highway from a Supercharger may still count as "within half a
  mile," even if you cannot walk there directly.
- Store and charger data come from public, crowd-sourced maps. Some
  entries may be missing or out of date.
