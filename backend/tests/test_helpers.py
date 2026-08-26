"""Tests for the small pure helpers: team_ids, park_factors, guts, and the
mojibake/NaN handling in pybaseball_client._records/_fix_mojibake."""
import numpy as np
import pandas as pd
import pytest

from app import guts, park_factors, team_ids
from app.pybaseball_client import _fix_mojibake, _records


def test_team_ids_comma_joins_stints_in_order():
    df = pd.DataFrame({"mlbID": ["1", "2", "3"], "Name": ["Traded", "Stayed", "Unmatched"]})
    bwar = pd.DataFrame(
        {
            "mlb_ID": [1, 1, 2],
            "team_ID": ["SEA", "ARI", "NYY"],
            "stint_ID": [2, 1, 1],
        }
    )
    out = team_ids.attach(df, bwar)
    assert out.loc[0, "TmID"] == "ARI,SEA"  # stint order, not row order
    assert out.loc[1, "TmID"] == "NYY"
    assert out.loc[2, "TmID"] == ""  # no match -> empty string, not NaN
    # The original string dtype of the caller's mlbID column is untouched.
    assert out["mlbID"].tolist() == ["1", "2", "3"]


def test_park_factors_primary_team_by_ipouts():
    bwar = pd.DataFrame(
        {
            "mlb_ID": [1, 1],
            "team_ID": ["SEA", "ARI"],
            "IPouts": [100, 300],
            "PPF": [95, 105],
        }
    )
    primary = park_factors.primary_team(bwar)
    assert primary["team_ID"].tolist() == ["ARI"]


def test_park_factors_attach_falls_back_to_neutral():
    park_df = pd.DataFrame({"team_ID": ["SEA"], "PF": [0.95]})
    df = pd.DataFrame({"team_ID": ["SEA", "XXX", None]})
    pf = park_factors.attach(df, park_df)
    assert pf.tolist() == [0.95, 1.0, 1.0]


def test_guts_known_season_is_not_estimated():
    g = guts.for_season(2024)
    assert g["estimated"] is False
    assert g["cFIP"] == pytest.approx(3.166)


def test_guts_unknown_season_falls_back_to_nearest():
    g = guts.for_season(2014)
    assert g["estimated"] is True
    assert g["woba"] == guts.GUTS[2015]["woba"]


def test_fix_mojibake_repairs_byte_escapes():
    assert _fix_mojibake("Acu\\xc3\\xb1a Jr., Ronald") == "Acuña Jr., Ronald"
    assert _fix_mojibake("Plain Name") == "Plain Name"
    assert _fix_mojibake(42) == 42
    # Un-decodable escape sequences are left alone rather than raising.
    assert _fix_mojibake("bad\\xff\\xfe") == "bad\\xff\\xfe"


def test_records_converts_nan_to_none():
    df = pd.DataFrame({"Name": ["A", "B"], "ERA": [3.5, np.nan]})
    records = _records(df)
    assert records[1]["ERA"] is None
    assert records[0]["ERA"] == 3.5
