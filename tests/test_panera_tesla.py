import pytest

import panera_tesla as pt


def test_haversine_miles_same_point_is_zero():
    assert pt.haversine_miles(42.0, -71.0, 42.0, -71.0) == 0.0


def test_haversine_miles_one_degree_latitude_is_about_69_miles():
    d = pt.haversine_miles(42.0, -71.0, 43.0, -71.0)
    assert 68.5 < d < 69.5


def test_in_new_england_accepts_abbrev_name_and_case():
    assert pt.in_new_england("CT")
    assert pt.in_new_england("ct")
    assert pt.in_new_england("Connecticut")
    assert pt.in_new_england("CONNECTICUT")
    assert pt.in_new_england(" vt ")
    assert not pt.in_new_england("New York")
    assert not pt.in_new_england(None)
    assert not pt.in_new_england("")


def test_operator_ids_for_single_network():
    assert pt.operator_ids_for(["tesla"]) == pt.NETWORK_OPERATOR_IDS["tesla"]


def test_operator_ids_for_multiple_networks():
    ids = pt.operator_ids_for(["tesla", "evgo"])
    assert pt.NETWORK_OPERATOR_IDS["tesla"] in ids
    assert pt.NETWORK_OPERATOR_IDS["evgo"] in ids
    # every individual ID should appear, comma-joined, no duplicates lost
    want = set((pt.NETWORK_OPERATOR_IDS["tesla"] + "," + pt.NETWORK_OPERATOR_IDS["evgo"]).split(","))
    assert set(ids.split(",")) == want


def test_operator_ids_for_unknown_network_raises():
    with pytest.raises(ValueError):
        pt.operator_ids_for(["chargepoint"])


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
