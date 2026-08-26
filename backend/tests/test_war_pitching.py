"""Tests for the fWAR/bWAR pitching formulas, the shared WAR chassis, and
the war_compare merge."""
import pandas as pd
import pytest

from app import bwar_pitching, fwar_pitching, park_factors, pitcher_war_chassis, war_compare

GUTS = {"cFIP": 3.15, "r_per_w": 9.8}


def _pitching_df():
    # Two identical league-average starters (in different parks) and one
    # reliever; 1-based index like the real bref frame.
    return pd.DataFrame(
        {
            "mlbID": [1, 2, 3],
            "Name": ["Neutral Park SP", "Hitter Park SP", "RP"],
            "Tm": ["Anytown", "Slugville", "Anytown"],
            "IP": [180.0, 180.0, 60.0],
            "G": [30, 30, 60],
            "GS": [30, 30, 0],
            "SO": [170, 170, 70],
            "BB": [50, 50, 20],
            "HBP": [6, 6, 2],
            "HR": [20, 20, 6],
            "ER": [80, 80, 25],
            "R": [88, 88, 27],
        },
        index=[1, 2, 3],
    )


def _bwar_pitch_df():
    return pd.DataFrame(
        {
            "mlb_ID": [1, 2, 3],
            "team_ID": ["ANY", "SLG", "ANY"],
            "stint_ID": [1, 1, 1],
            "IPouts": [540, 540, 180],
            "PPF": [100, 110, 100],
        }
    )


@pytest.fixture
def park_df():
    return park_factors.from_bwar_pitch(_bwar_pitch_df())


def test_fwar_park_adjustment_direction(park_df):
    """Identical raw lines: the pitcher in the hitter-friendly park (PF 1.10)
    must grade out better than the one in the neutral park."""
    out = fwar_pitching.compute(_pitching_df(), _bwar_pitch_df(), park_df, GUTS)
    assert out.loc[1, "fWAR"] > out.loc[0, "fWAR"]
    assert out.loc[1, "PF"] == pytest.approx(1.10)
    assert out.loc[0, "PF"] == pytest.approx(1.00)


def test_bwar_park_adjustment_direction(park_df):
    out = bwar_pitching.compute(_pitching_df(), _bwar_pitch_df(), park_df)
    assert out.loc[1, "bWAR"] > out.loc[0, "bWAR"]
    assert out.loc[0, "RA9"] == pytest.approx(9 * 88 / 180, abs=0.01)


def test_league_average_starter_gets_replacement_bump(park_df):
    """A league-average starter in a neutral park is worth ~the starter
    replacement gap (0.12 wins/9IP * 180 IP / 9 = 2.4 WAR), by construction
    of the formula."""
    df = _pitching_df()
    out = bwar_pitching.compute(df, _bwar_pitch_df(), park_df)
    # Pitcher 1 isn't exactly league average (the reliever is better), so
    # allow a loose band around 2.4.
    assert 1.5 < out.loc[0, "bWAR"] < 3.5


def test_chassis_league_average_starter_and_reliever():
    """A pitcher exactly at the league rate in a neutral park earns pure
    replacement value: .12 wins/9IP for a starter, .03 for a reliever."""
    df = pd.DataFrame({"IP": [180.0, 63.0], "G": [30, 63], "GS": [30, 0]})
    rate = pd.Series([4.5, 4.5])
    pf = pd.Series([1.0, 1.0])
    war = pitcher_war_chassis.war_from_rate(df, rate, 4.5, pf)
    assert war.iloc[0] == pytest.approx(0.12 * 180 / 9)  # 2.4
    assert war.iloc[1] == pytest.approx(0.03 * 63 / 9)   # 0.21


def test_chassis_park_adjustment_beats_flat_rate():
    """Identical raw rates, one in a PF-1.10 park: the hitter-park pitcher
    must come out ahead once park-adjusted."""
    df = pd.DataFrame({"IP": [180.0, 180.0], "G": [30, 30], "GS": [30, 30]})
    rate = pd.Series([4.5, 4.5])
    war = pitcher_war_chassis.war_from_rate(df, rate, 4.5, pd.Series([1.0, 1.10]))
    assert war.iloc[1] > war.iloc[0]


def test_war_compare_merges_and_spreads(park_df):
    from app import cwar, team_ids

    pitching = _pitching_df()
    pitching["GB/FB"] = 0.45
    pitching["LD"] = 0.20
    pitching["PU"] = 0.05
    pitching["BF"] = [720, 720, 260]  # cwar needs BF for the HR regression

    bwar_pitch = _bwar_pitch_df()
    empty_expected = pd.DataFrame({"player_id": [], "xera": [], "bip": []})

    b = team_ids.attach(bwar_pitching.compute(_pitching_df(), bwar_pitch, park_df), bwar_pitch)
    f = team_ids.attach(fwar_pitching.compute(_pitching_df(), bwar_pitch, park_df, GUTS), bwar_pitch)
    c = team_ids.attach(cwar.compute(pitching, empty_expected, bwar_pitch, park_df), bwar_pitch)

    out = war_compare.compute(b, f, c)
    assert list(out.columns) == ["Name", "Tm", "TmID", "IP", "bWAR", "fWAR", "cWAR", "WAR_spread"]
    assert len(out) == 3
    row = out.iloc[0]
    assert row["WAR_spread"] == pytest.approx(
        max(row["bWAR"], row["fWAR"], row["cWAR"]) - min(row["bWAR"], row["fWAR"], row["cWAR"]),
        abs=0.01,
    )
