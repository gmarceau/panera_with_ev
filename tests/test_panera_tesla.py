import pytest
import yaml
from pydantic import ValidationError

import panera_tesla as pt


def test_haversine_miles_same_point_is_zero():
    assert pt.haversine_miles(42.0, -71.0, 42.0, -71.0) == 0.0


def test_haversine_miles_one_degree_latitude_is_about_69_miles():
    d = pt.haversine_miles(42.0, -71.0, 43.0, -71.0)
    assert 68.5 < d < 69.5


def test_in_target_states_accepts_abbrev_name_and_case():
    assert pt.in_target_states("CT")
    assert pt.in_target_states("ct")
    assert pt.in_target_states("Connecticut")
    assert pt.in_target_states("CONNECTICUT")
    assert pt.in_target_states(" vt ")
    assert not pt.in_target_states(None)
    assert not pt.in_target_states("")


def test_in_target_states_includes_mid_atlantic_expansion():
    for abbrev in ["PA", "NJ", "NY", "MD"]:
        assert pt.in_target_states(abbrev)
    for name in ["Pennsylvania", "New Jersey", "New York", "Maryland"]:
        assert pt.in_target_states(name)
    assert not pt.in_target_states("Ohio")


def test_operator_ids_for_single_network():
    assert pt.operator_ids_for(["tesla"]) == "23,3534"


def test_operator_ids_for_multiple_networks():
    ids = pt.operator_ids_for(["tesla", "evgo"])
    # every individual ID should appear, comma-joined, none lost
    assert set(ids.split(",")) == {"23", "3534", "15", "3252"}


def test_operator_ids_for_unknown_network_raises():
    with pytest.raises(ValueError):
        pt.operator_ids_for(["chargepoint"])


def test_config_defines_all_networks():
    # Operator IDs verified against
    # https://api.openchargemap.io/v3/referencedata/ — don't guess new ones.
    expected = {
        "tesla": [23, 3534],
        "evgo": [15, 3252],
        "rivian": [3607],
        "mercedes": [3827],
        "applegreen": [3516],
        "shell": [59],
        "totalenergies": [3447, 3571, 25],
    }
    assert set(pt.CONFIG.networks) == set(expected)
    for name, ids in expected.items():
        assert list(pt.CONFIG.networks[name].operator_ids) == ids


def test_operator_ids_for_new_networks():
    ids = pt.operator_ids_for(
        ["rivian", "mercedes", "applegreen", "shell", "totalenergies"])
    assert ids == "3607,3827,3516,59,3447,3571,25"


def _write_config(tmp_path, mutate=None):
    """The real config.yml (mutated if requested) written to tmp_path."""
    data = pt.CONFIG.model_dump(mode="json")
    if mutate:
        mutate(data)
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_load_config_accepts_the_real_config():
    assert pt.load_config(pt.CONFIG_FILE) == pt.CONFIG


def test_load_config_rejects_unknown_default_network(tmp_path):
    p = _write_config(tmp_path, lambda d: d.update(default_networks=["chargepoint"]))
    with pytest.raises(ValidationError):
        pt.load_config(p)


def test_load_config_rejects_inverted_bbox(tmp_path):
    p = _write_config(tmp_path, lambda d: d.update(search_bbox=[47.5, -66.9, 37.8, -80.75]))
    with pytest.raises(ValidationError):
        pt.load_config(p)


def test_load_config_rejects_empty_operator_ids(tmp_path):
    p = _write_config(tmp_path, lambda d: d["networks"].update(shell={"operator_ids": []}))
    with pytest.raises(ValidationError):
        pt.load_config(p)


def test_load_config_rejects_unknown_key(tmp_path):
    p = _write_config(tmp_path, lambda d: d.update(walk_mile=0.5))  # typo'd key
    with pytest.raises(ValidationError):
        pt.load_config(p)


def test_load_config_rejects_missing_required_field(tmp_path):
    p = _write_config(tmp_path, lambda d: d.pop("min_kw"))
    with pytest.raises(ValidationError):
        pt.load_config(p)


def test_load_config_rejects_bad_url(tmp_path):
    p = _write_config(tmp_path, lambda d: d.update(ocm_url="ftp://chargers.example"))
    with pytest.raises(ValidationError):
        pt.load_config(p)


def test_load_config_rejects_bad_state_code(tmp_path):
    p = _write_config(tmp_path, lambda d: d["target_states"].update({"CT": "Connecticut"}))
    with pytest.raises(ValidationError):
        pt.load_config(p)


def test_load_config_rejects_nonpositive_walk_miles(tmp_path):
    p = _write_config(tmp_path, lambda d: d.update(walk_miles=0))
    with pytest.raises(ValidationError):
        pt.load_config(p)


def _poi(title, network_title, state, power_kw, operational=True):
    return {
        "AddressInfo": {
            "Title": title,
            "AddressLine1": "1 Main St",
            "Town": "Anytown",
            "StateOrProvince": state,
            "Latitude": 42.1,
            "Longitude": -71.1,
        },
        "OperatorInfo": {"Title": network_title},
        "Connections": [{"PowerKW": power_kw}],
        "StatusType": {"IsOperational": operational},
    }


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_chargers_filters_and_labels_network(monkeypatch):
    payload = [
        _poi("Boston Supercharger", "Tesla (Tesla-only charging)", "MA", 250),
        _poi("Hartford EVgo", "eVgo Network", "CT", 100, operational=None),
        _poi("Some Wall Connector", "Tesla (Tesla-only charging)", "MA", 11),  # too low power
    ]
    monkeypatch.setattr(pt, "_request_with_retry", lambda *a, **k: _FakeResponse(payload))

    chargers = pt.fetch_chargers(networks=("tesla", "evgo"))

    assert len(chargers) == 2
    titles = {c["title"] for c in chargers}
    assert titles == {"Boston Supercharger", "Hartford EVgo"}
    networks = {c["network"] for c in chargers}
    assert networks == {"Tesla (Tesla-only charging)", "eVgo Network"}
    boston = next(c for c in chargers if c["title"] == "Boston Supercharger")
    assert boston["lat"] == 42.1 and boston["lon"] == -71.1
    assert boston["address"] == "1 Main St, Anytown, MA"


def test_find_matches_includes_charger_coords_and_network():
    paneras = [{"name": "Panera Bread", "address": "1 Elm St", "lat": 42.0, "lon": -71.0}]
    chargers = [{"title": "Nearby Supercharger", "address": "2 Elm St",
                 "network": "Tesla (Tesla-only charging)", "lat": 42.001, "lon": -71.001}]

    matches = pt.find_matches(paneras, chargers)

    assert len(matches) == 1
    m = matches[0]
    assert m["charger_lat"] == 42.001
    assert m["charger_lon"] == -71.001
    assert m["network"] == "Tesla (Tesla-only charging)"


def test_write_matches_yaml_roundtrip(tmp_path):
    import yaml

    matches = [{"panera_name": "Panera Bread", "distance_mi": 0.12,
                "charger_lat": 42.1, "network": "eVgo Network"}]
    out = tmp_path / "matches.yml"

    pt.write_matches_yaml(matches, out)

    assert out.exists()
    loaded = yaml.safe_load(out.read_text())
    assert loaded == matches


def test_cache_is_fresh_true_within_max_age(tmp_path):
    f = tmp_path / "cache.json"
    f.write_text("{}")
    assert pt.cache_is_fresh(f, max_age=3600) is True


def test_cache_is_fresh_false_past_max_age(tmp_path):
    import os as _os
    import time as _time

    f = tmp_path / "cache.json"
    f.write_text("{}")
    old = _time.time() - 7200
    _os.utime(f, (old, old))
    assert pt.cache_is_fresh(f, max_age=3600) is False


def test_cache_is_fresh_false_when_missing(tmp_path):
    missing = tmp_path / "nope.json"
    assert pt.cache_is_fresh(missing, max_age=3600) is False


def test_cli_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(pt, "run_pipeline", lambda **kw: calls.append(kw))

    pt.PaneraChargerApp.run(["prog"], exit=False)

    assert len(calls) == 1
    assert calls[0]["walk_miles"] == pt.CONFIG.walk_miles
    assert calls[0]["networks"] == pt.CONFIG.default_networks
    assert calls[0]["output_path"] == "panera_chargers.yml"
    assert calls[0]["refresh"] is False


def test_cli_overrides(monkeypatch):
    calls = []
    monkeypatch.setattr(pt, "run_pipeline", lambda **kw: calls.append(kw))

    pt.PaneraChargerApp.run(
        ["prog", "--walk-miles", "0.3", "--networks", "evgo",
         "--output", "out.yml", "--refresh"],
        exit=False,
    )

    assert calls[0]["walk_miles"] == 0.3
    assert calls[0]["networks"] == ["evgo"]
    assert calls[0]["output_path"] == "out.yml"
    assert calls[0]["refresh"] is True


def test_run_pipeline_writes_yaml(tmp_path, monkeypatch, capsys):
    import yaml

    fake_paneras = [{"name": "Panera Bread", "address": "1 Elm St", "lat": 42.0, "lon": -71.0}]
    fake_chargers = [{"title": "Nearby Charger", "address": "2 Elm St",
                       "network": "eVgo Network", "lat": 42.001, "lon": -71.001}]
    monkeypatch.setattr(pt, "fetch_paneras", lambda refresh=False: fake_paneras)
    monkeypatch.setattr(pt, "fetch_chargers", lambda networks: fake_chargers)
    out = tmp_path / "out.yml"

    pt.run_pipeline(walk_miles=0.5, networks=["evgo"], output_path=str(out), refresh=False)

    loaded = yaml.safe_load(out.read_text())
    assert len(loaded) == 1
    assert loaded[0]["panera_name"] == "Panera Bread"
    assert loaded[0]["network"] == "eVgo Network"
    captured = capsys.readouterr()
    assert "1 Paneras" in captured.out
    assert "1 chargers" in captured.out
    assert "1 Panera/charger pairs" in captured.out
