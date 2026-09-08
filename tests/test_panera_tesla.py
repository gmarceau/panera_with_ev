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
