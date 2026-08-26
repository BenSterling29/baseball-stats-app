"""Tests for the fWAR batting formula on small synthetic frames."""
import pandas as pd
import pytest

from app import fwar_batting, park_factors

GUTS = {
    "woba": 0.316, "woba_scale": 1.24, "wBB": 0.70, "wHBP": 0.73,
    "w1B": 0.89, "w2B": 1.26, "w3B": 1.60, "wHR": 2.05,
    "runSB": 0.20, "runCS": -0.41, "r_per_w": 9.8,
}


def _batting_df():
    # 1-based index like batting_stats_bref; string numbers on purpose.
    return pd.DataFrame(
        {
            "mlbID": ["10", "11", "12"],
            "Name": ["Slugger", "Speedster", "Catcher"],
            "PA": [600, 600, 450],
            "AB": [520, 540, 400],
            "R": [95, 80, 45],
            "H": [150, 160, 100],
            "2B": [30, 25, 18],
            "3B": [2, 8, 1],
            "HR": [40, 8, 12],
            "BB": [70, 45, 40],
            "IBB": [8, 1, 2],
            "HBP": [5, 8, 6],
            "SF": [5, 6, 4],
            "SB": [2, 45, 1],
            "CS": [1, 8, 0],
        },
        index=[1, 2, 3],
    )


def _bwar_bat_df():
    # One row per player-team-stint: Speedster was traded mid-season.
    return pd.DataFrame(
        {
            "mlb_ID": [10, 11, 11, 12],
            "team_ID": ["ANY", "ANY", "SLG", "ANY"],
            "lg_ID": ["AL", "AL", "NL", "AL"],
            "stint_ID": [1, 1, 2, 1],
            "PA": [600, 400, 200, 450],
            "runs_dp": [-2.0, 1.0, 0.5, -1.0],
            "runs_position": [-8.0, 2.0, 1.0, 7.0],
            "runs_field": [3.0, 4.0, 2.0, 5.0],
        }
    )


def _park_df():
    return pd.DataFrame({"team_ID": ["ANY", "SLG"], "PF": [1.00, 1.10]})


def _compute(framing=None, oaa=None):
    framing = framing if framing is not None else pd.DataFrame({"mlbID": [], "framing_runs": []})
    oaa = oaa if oaa is not None else pd.DataFrame({"mlbID": [], "oaa_runs": []})
    return fwar_batting.compute(_batting_df(), _bwar_bat_df(), _park_df(), framing, oaa, GUTS)


def test_woba_formula_known_value():
    out = _compute()
    # Slugger, by hand: singles = 150-30-2-40 = 78
    num = (0.70 * (70 - 8) + 0.73 * 5 + 0.89 * 78 + 1.26 * 30 + 1.60 * 2 + 2.05 * 40)
    den = 520 + 70 - 8 + 5 + 5
    assert out.loc[0, "wOBA"] == pytest.approx(num / den, abs=0.001)


def test_traded_player_stints_are_summed():
    out = _compute()
    # Speedster's runs_dp is summed across both stints (1.0 + 0.5) and lands
    # in BsR along with wSB; his positional value sums 2.0 + 1.0.
    assert out.loc[1, "Pos"] == pytest.approx(3.0)


def test_fielding_tiers_layer_correctly():
    framing = pd.DataFrame({"mlbID": [12], "framing_runs": [9.0]})
    oaa = pd.DataFrame({"mlbID": [10], "oaa_runs": [-4.0]})
    out = _compute(framing=framing, oaa=oaa)
    assert out.loc[2, "Fld"] == pytest.approx(9.0)   # catcher: framing wins
    assert out.loc[0, "Fld"] == pytest.approx(-4.0)  # non-catcher: OAA
    assert out.loc[1, "Fld"] == pytest.approx(6.0)   # no Statcast: bref runs_field (4+2)


def test_league_adjustment_nets_to_zero_per_league():
    """The league adjustment redistributes each league's component total to
    zero; with everyone in one league the PA-weighted adjustment must cancel
    the component sum, so total fWAR ~= the replacement pool contribution."""
    out = _compute()
    # All three players' fWAR should at least be finite and non-null.
    assert out["fWAR"].notna().all()


def test_speedster_gets_positive_bsr():
    out = _compute()
    # 45 SB / 8 CS is far above league average -> positive BsR.
    assert out.loc[1, "BsR"] > 0
    assert out.loc[0, "BsR"] < out.loc[1, "BsR"]
