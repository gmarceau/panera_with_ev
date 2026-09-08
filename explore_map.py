import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    import plotly.graph_objects as go
    import yaml

    return go, mo, yaml


@app.cell
def _(mo):
    mo.md("""
    # Panera Bread / Fast Charger Explorer

    Panera Bread stores across New England and the mid-Atlantic matched
    to nearby Tesla Supercharger
    and EVgo fast-charging stations. Distance is straight-line, not a
    walking route — a match doesn't guarantee an actual walkable path.
    """)
    return


@app.cell
def _(yaml):
    with open("panera_chargers.yml") as f:
        matches = yaml.safe_load(f) or []

    def clean_address(addr):
        return addr if addr and addr != "(no address in OSM)" else "no address on file"

    for _m in matches:
        _m["panera_address"] = clean_address(_m.get("panera_address"))
        _m["charger_address"] = clean_address(_m.get("charger_address"))

    max_distance = max((_m["distance_mi"] for _m in matches), default=0.5)
    return matches, max_distance


@app.cell
def _(max_distance, mo):
    distance_slider = mo.ui.slider(
        start=0.0,
        stop=max_distance,
        step=0.01,
        value=max_distance,
        label="Show matches within (miles)",
    )
    distance_slider
    return (distance_slider,)


@app.cell
def _(distance_slider, matches):
    filtered = [_m for _m in matches if _m["distance_mi"] <= distance_slider.value]
    return (filtered,)


@app.cell
def _(filtered, go):
    lines_lat, lines_lon = [], []
    for _m in filtered:
        lines_lat += [_m["panera_lat"], _m["charger_lat"], None]
        lines_lon += [_m["panera_lon"], _m["charger_lon"], None]

    # Three categories, each its own trace so the legend can toggle them
    # individually: Tesla-only (black), Tesla open to non-Tesla cars (red),
    # EVgo (blue).
    CATEGORIES = [
        ("Tesla (Tesla-only)", "#000000",
         lambda n: "tesla" in n and "non-tesla" not in n and "including" not in n),
        ("Tesla (open to non-Tesla)", "#d62728",
         lambda n: "tesla" in n and ("non-tesla" in n or "including" in n)),
        ("EVgo", "#1f77b4",
         lambda n: "evgo" in n or "nrg" in n),
    ]

    def categorize(network):
        n = (network or "").lower()
        for label, color, match in CATEGORIES:
            if match(n):
                return label, color
        return "Other", "#7f7f7f"

    fig = go.Figure()
    fig.add_trace(go.Scattermap(
        lat=lines_lat, lon=lines_lon, mode="lines",
        line=dict(width=1, color="#999999"),
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scattermap(
        lat=[_m["panera_lat"] for _m in filtered],
        lon=[_m["panera_lon"] for _m in filtered],
        mode="markers",
        marker=dict(size=10, color="#2ca02c"),
        text=[f"{_m['panera_name']}<br>{_m['panera_address']}" for _m in filtered],
        hoverinfo="text", name="Panera",
    ))
    labels_colors = [(label, color) for label, color, _ in CATEGORIES] + [("Other", "#7f7f7f")]
    for label, color in labels_colors:
        group = [_m for _m in filtered if categorize(_m["network"])[0] == label]
        if not group:
            continue
        fig.add_trace(go.Scattermap(
            lat=[_m["charger_lat"] for _m in group],
            lon=[_m["charger_lon"] for _m in group],
            mode="markers",
            marker=dict(size=10, color=color),
            text=[f"{_m['charger_title']}<br>{_m['charger_address']}<br>{_m['network']}"
                  for _m in group],
            hoverinfo="text", name=label,
        ))
    fig.update_layout(
        # "open-street-map" hits tile.openstreetmap.org directly, which
        # blocks requests with no Referer (the header a locally-served
        # marimo app doesn't send, and that browsers won't let us set) per
        # OSM's tile usage policy. carto-voyager is rendered from the same
        # OSM data but served from CARTO's CDN, built for this kind of
        # embedding — no token, no referer restriction.
        # fitbounds instead of a fixed center/zoom: the search region now
        # spans New England down to Maryland, so a hardcoded view tuned for
        # one corner of it would leave the rest off-screen.
        map=dict(style="carto-voyager", fitbounds="locations"),
        margin=dict(l=0, r=0, t=0, b=0),
        height=650,
    )
    fig
    return


@app.cell
def _(distance_slider, filtered, mo):
    mo.vstack([
        mo.md(f"**{len(filtered)} matches** within {distance_slider.value:.2f} mi"),
        mo.ui.table(filtered, selection=None),
    ])
    return


if __name__ == "__main__":
    app.run()
