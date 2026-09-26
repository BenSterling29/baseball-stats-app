"""Unit tests for the sWAR (Stuff + Command) prototype math, on small
synthetic frames. No network and no pybaseball: the model predictions that
the offline prototype script would produce are passed in directly.

The bref fixture mimics the real quirks: a 1-based index and string-typed
numeric columns.
"""
import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from app import park_factors, pitcher_war_chassis, swar

SWAR_PATH = pathlib.Path(swar.__file__)


# --------------------------------------------------------------------------
# module hygiene


def test_swar_never_imports_pybaseball():
    tree = ast.parse(SWAR_PATH.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.update(alias.name for alias in node.names)
    assert "pybaseball" not in imported


# --------------------------------------------------------------------------
# pitch_group / pitch_run_value


def test_pitch_group_maps_known_types_and_drops_the_rest():
    out = swar.pitch_group(pd.Series(["FF", "SL", "CH", "KN", "EP"]))
    assert out.tolist()[:3] == ["fastball", "breaking", "offspeed"]
    assert out.iloc[3:].isna().all()


def _rv_frame():
    return pd.DataFrame(
        {
            # string-typed numerics, like a raw Statcast CSV can be
            "delta_run_exp": ["0.05", "-0.10", "0.30", "-0.25", "0.40"],
            "type": ["S", "X", "X", "X", "X"],
            "woba_denom": [np.nan, 1, 1, 0, 1],
            "woba_value": [np.nan, 0.0, 0.9, 0.0, 0.9],
            "estimated_woba_using_speedangle": [np.nan, 0.6, 0.2, 0.5, np.nan],
        }
    )


def test_pitch_run_value_is_pitcher_perspective():
    rv = swar.pitch_run_value(_rv_frame())
    assert rv.tolist() == pytest.approx([-0.05, 0.10, -0.30, 0.25, -0.40])


def test_bip_delucking_only_touches_scored_balls_in_play_with_xwoba():
    scale = 1.25
    raw = swar.pitch_run_value(_rv_frame())
    de = swar.pitch_run_value(_rv_frame(), woba_scale=scale)
    # Row 0: not in play. Row 3: woba_denom 0. Row 4: no xwOBA. Untouched.
    for i in (0, 3, 4):
        assert de.iloc[i] == pytest.approx(raw.iloc[i])
    # Row 1: lucky out (wOBA 0 on xwOBA .6) -> gives luck back (lower value).
    assert de.iloc[1] == pytest.approx(0.10 + (0.0 - 0.6) / scale)
    assert de.iloc[1] < raw.iloc[1]
    # Row 2: unlucky hit (wOBA .9 on xwOBA .2) -> credited back.
    assert de.iloc[2] == pytest.approx(-0.30 + (0.9 - 0.2) / scale)
    assert de.iloc[2] > raw.iloc[2]


# --------------------------------------------------------------------------
# stuff_features


def _pitch(season, pitcher, hand, ptype, velo, pfx_x, pfx_z, rel_x, axis):
    return {
        "season": season, "pitcher": pitcher, "p_throws": hand, "pitch_type": ptype,
        "release_speed": velo, "pfx_x": pfx_x, "pfx_z": pfx_z,
        "release_pos_x": rel_x, "spin_axis": axis,
    }


def test_lhp_is_mirrored_onto_rhp():
    """A lefty and a righty with mirror-image pitches get identical
    handedness-neutral features."""
    rows = [
        _pitch(2024, 1, "R", "FF", 95.0, -0.7, 1.3, -2.0, 210.0),
        _pitch(2024, 1, "R", "SL", 86.0, 0.4, 0.2, -2.1, 90.0),
        _pitch(2024, 2, "L", "FF", 95.0, 0.7, 1.3, 2.0, 150.0),
        _pitch(2024, 2, "L", "SL", 86.0, -0.4, 0.2, 2.1, 270.0),
    ]
    out = swar.stuff_features(pd.DataFrame(rows, index=[1, 2, 3, 4]))
    r = out[out["pitcher"] == 1].reset_index(drop=True)
    left = out[out["pitcher"] == 2].reset_index(drop=True)
    cols = ["hb_in", "ivb_in", "release_pos_x_m", "spin_axis_m", "velo_diff", "hb_diff", "ivb_diff"]
    pd.testing.assert_frame_equal(r[cols], left[cols])
    # Movement is in inches.
    # A righty's arm-side run (negative Statcast pfx_x) comes out positive.
    assert r.loc[0, "hb_in"] == pytest.approx(0.7 * 12)
    assert r.loc[0, "ivb_in"] == pytest.approx(1.3 * 12)


def test_fastball_reference_is_most_thrown_fastball():
    rows = (
        [_pitch(2024, 1, "R", "SI", 94.0, -1.2, 0.6, -2.0, 225.0)] * 3
        + [_pitch(2024, 1, "R", "FF", 96.0, -0.6, 1.4, -2.0, 205.0)] * 1
        # The sweeper is the most-thrown pitch overall but not a fastball.
        + [_pitch(2024, 1, "R", "ST", 84.0, 1.2, 0.0, -2.0, 60.0)] * 5
    )
    out = swar.stuff_features(pd.DataFrame(rows))
    si = out[out["pitch_type"] == "SI"].iloc[0]
    ff = out[out["pitch_type"] == "FF"].iloc[0]
    st = out[out["pitch_type"] == "ST"].iloc[0]
    assert (si["velo_diff"], si["hb_diff"], si["ivb_diff"]) == (0.0, 0.0, 0.0)
    assert ff["velo_diff"] == pytest.approx(2.0)
    assert st["velo_diff"] == pytest.approx(-10.0)
    # Sweeper breaks glove side of the arm-side sinker: negative, in inches.
    assert st["hb_diff"] == pytest.approx(-(1.2 - -1.2) * 12)
    assert st["ivb_diff"] == pytest.approx((0.0 - 0.6) * 12)


def test_no_fastball_falls_back_to_hardest_pitch():
    rows = (
        [_pitch(2024, 7, "R", "CH", 88.0, -1.0, 0.5, -2.0, 240.0)] * 2
        + [_pitch(2024, 7, "R", "CU", 78.0, 0.8, -1.0, -2.0, 30.0)] * 4
    )
    out = swar.stuff_features(pd.DataFrame(rows))
    ch = out[out["pitch_type"] == "CH"]
    cu = out[out["pitch_type"] == "CU"]
    assert (ch[["velo_diff", "hb_diff", "ivb_diff"]] == 0.0).all().all()
    assert cu["velo_diff"].iloc[0] == pytest.approx(-10.0)


def test_reference_is_per_pitcher_season():
    rows = [
        _pitch(2023, 1, "R", "FF", 93.0, -0.6, 1.3, -2.0, 210.0),
        _pitch(2024, 1, "R", "FF", 96.0, -0.6, 1.3, -2.0, 210.0),
        _pitch(2024, 1, "R", "SL", 86.0, 0.4, 0.2, -2.0, 90.0),
    ]
    out = swar.stuff_features(pd.DataFrame(rows))
    assert out["velo_diff"].tolist() == pytest.approx([0.0, 0.0, -10.0])


# --------------------------------------------------------------------------
# command_features / on_target


def _cmd_frame():
    return pd.DataFrame(
        {
            "plausible": [True, True, False, True],
            "p_throws": ["R", "L", "R", "R"],
            "stand": ["R", "R", "L", "L"],
            "inferred_x_in": [2.0, 2.0, 0.0, -4.0],
            "inferred_z_in": [30.0, 30.0, 30.0, 24.0],
            "plate_x_in": [5.0, 5.0, 3.0, -4.0],
            "plate_z_in": [34.0, 34.0, 26.0, 24.0],
            "sz_top": [3.5, 3.5, 3.5, 3.5],
            "sz_bot": [1.5, 1.5, 1.5, 1.5],
            "balls": [0, 1, 2, 3],
            "strikes": [0, 1, 2, 2],
            "group": ["fastball", "breaking", "offspeed", "fastball"],
        },
        index=[1, 2, 3, 4],
    )


def test_command_miss_vector_and_mirroring():
    out = swar.command_features(_cmd_frame())
    # RHP: miss x (5-2)=+3 toward first base = glove side; miss z +4;
    # distance is the hypot.
    assert out.loc[1, "miss_x_arm"] == pytest.approx(-3.0)
    assert out.loc[1, "miss_z"] == pytest.approx(4.0)
    assert out.loc[1, "miss_dist"] == pytest.approx(5.0)
    # Same miss by a LHP is arm side.
    assert out.loc[2, "miss_x_arm"] == pytest.approx(3.0)
    assert out.loc[2, "miss_dist"] == pytest.approx(5.0)
    # Target x mirrored by batter hand; height relative to the zone.
    assert out.loc[1, "target_x_away"] == pytest.approx(2.0)
    assert out.loc[4, "target_x_away"] == pytest.approx(4.0)
    assert out.loc[1, "target_z_rel"] == pytest.approx((30 - 18) / 24)
    assert out.loc[1, "same_hand"] == 1.0 and out.loc[4, "same_hand"] == 0.0
    assert out["group_code"].tolist() == [0, 1, 2, 0]
    # A pitch dead on target misses by zero.
    assert out.loc[4, "miss_dist"] == pytest.approx(0.0)


def test_rows_without_plausible_target_get_nan_miss():
    out = swar.command_features(_cmd_frame())
    for col in ("miss_x_arm", "miss_z", "miss_dist", "target_x_away", "target_z_rel"):
        assert np.isnan(out.loc[3, col])
    assert out.drop(index=3)["miss_dist"].notna().all()


def test_on_target_zeroes_only_the_miss_columns():
    feats = swar.command_features(_cmd_frame())
    hit = swar.on_target(feats)
    assert (hit[["miss_x_arm", "miss_z", "miss_dist"]] == 0.0).all().all()
    others = [c for c in swar.COMMAND_FEATURES if c not in ("miss_x_arm", "miss_z", "miss_dist")]
    pd.testing.assert_frame_equal(hit[others], feats[others])
    # The input is not mutated.
    assert feats.loc[1, "miss_dist"] == pytest.approx(5.0)


# --------------------------------------------------------------------------
# aggregate


def _per_pitch():
    # 2024: pitcher 1 throws 4 pitches at +0.02, pitcher 2 throws 4 at 0.00
    # -> league mean 0.01. 2025 has a very different level (mean 0.10) to
    # check that centering is per season.
    return pd.DataFrame(
        {
            "season": [2024] * 8 + [2025] * 4,
            "pitcher": [1] * 4 + [2] * 4 + [1] * 2 + [2] * 2,
            "stuff_rv": [0.02] * 4 + [0.0] * 4 + [0.15, 0.15, 0.05, 0.05],
            "command_rv": [0.01, 0.01, np.nan, np.nan, -0.01, -0.01, -0.01, -0.01,
                           0.2, 0.2, 0.0, 0.0],
        }
    )


def test_aggregate_centers_per_season_and_shrinks():
    out = swar.aggregate(_per_pitch(), stuff_k=4, command_k=2).set_index(["season", "pitcher"])
    # Pitcher 1 2024: centered stuff = 0.01 on 4 pitches -> 0.04 / (4 + 4).
    assert out.loc[(2024, 1), "stuff_rv"] == pytest.approx(0.04 / 8)
    assert out.loc[(2024, 2), "stuff_rv"] == pytest.approx(-0.04 / 8)
    # Command: league mean (NaN skipped) = (0.02 - 0.04) / 6 = -1/300;
    # pitcher 1 has 2 targeted pitches.
    lg = (0.01 * 2 - 0.01 * 4) / 6
    assert out.loc[(2024, 1), "cmd_n"] == 2
    assert out.loc[(2024, 1), "command_rv"] == pytest.approx(2 * (0.01 - lg) / (2 + 2))
    assert out.loc[(2024, 1), "total_rv"] == pytest.approx(
        out.loc[(2024, 1), "stuff_rv"] + out.loc[(2024, 1), "command_rv"]
    )
    # 2025's much higher raw level is centered away, on its own mean.
    assert out.loc[(2025, 1), "stuff_rv"] == pytest.approx(2 * 0.05 / (2 + 4))
    assert out.loc[(2025, 2), "stuff_rv"] == pytest.approx(-2 * 0.05 / (2 + 4))
    assert out.loc[(2024, 1), "pitches"] == 4


def test_aggregate_more_pitches_means_less_shrinkage():
    df = pd.DataFrame(
        {
            "season": [2024] * 110,
            "pitcher": [1] * 10 + [2] * 100,
            "stuff_rv": [0.05] * 10 + [0.0] * 100,
            "command_rv": [np.nan] * 110,
        }
    )
    out = swar.aggregate(df, stuff_k=100).set_index("pitcher")
    lg = 0.5 / 110
    assert out.loc[1, "stuff_rv"] == pytest.approx(10 * (0.05 - lg) / 110)
    # With no targets at all, command is shrunk all the way to 0, not NaN.
    assert (out["command_rv"] == 0.0).all()


def test_plus_index_is_100_for_a_pitch_weighted_average_pitcher():
    # Pitchers 1 and 3 are symmetric about pitcher 2, with equal pitches,
    # so pitcher 2 sits exactly at the pitch-weighted mean.
    df = pd.DataFrame(
        {
            "season": [2024] * 30,
            "pitcher": [1] * 10 + [2] * 10 + [3] * 10,
            "stuff_rv": [0.03] * 10 + [0.0] * 10 + [-0.03] * 10,
            "command_rv": [0.01] * 10 + [0.0] * 10 + [-0.01] * 10,
        }
    )
    out = swar.aggregate(df).set_index("pitcher")
    for col in ("Stuff+", "Cmd+", "Pitching+"):
        assert out.loc[2, col] == pytest.approx(100.0)
        assert out.loc[1, col] > 100 > out.loc[3, col]
        # 10 points = one pitch-weighted SD; here +/-1 SD exactly.
        assert out.loc[1, col] == pytest.approx(100 + 10 * np.sqrt(1.5))


def test_plus_index_with_no_spread_is_flat_100():
    df = pd.DataFrame(
        {"season": [2024] * 4, "pitcher": [1, 1, 2, 2],
         "stuff_rv": [0.01] * 4, "command_rv": [np.nan] * 4}
    )
    out = swar.aggregate(df)
    assert (out["Stuff+"] == 100.0).all()
    assert (out["Cmd+"] == 100.0).all()


# --------------------------------------------------------------------------
# compute


def _pitching_df():
    # Two identical starters in different parks plus a reliever. 1-based
    # index and string-typed numerics, like pitching_stats_bref.
    return pd.DataFrame(
        {
            "mlbID": ["1", "2", "3"],
            "Name": ["Neutral Park SP", "Hitter Park SP", "RP"],
            "IP": ["180.0", "180.0", "60.0"],
            "G": ["30", "30", "60"],
            "GS": ["30", "30", "0"],
            "R": ["80", "80", "30"],
        },
        index=[1, 2, 3],
    )


def _bwar_pitch_df():
    return pd.DataFrame(
        {
            "mlb_ID": ["1", "2", "3"],
            "team_ID": ["ANY", "SLG", "ANY"],
            "stint_ID": [1, 1, 1],
            "IPouts": ["540", "540", "180"],
            "PPF": ["100", "110", "100"],
        }
    )


def _pitcher_rv(total=(0.0, 0.0, 0.0), pitches=(2800, 2800, 950)):
    total = list(total)
    return pd.DataFrame(
        {
            "season": [2024] * 3,
            "pitcher": [1, 2, 3],
            "pitches": list(pitches),
            "stuff_rv": total,
            "command_rv": [0.0] * 3,
            "total_rv": total,
            "Stuff+": [100.0] * 3,
            "Cmd+": [100.0] * 3,
            "Pitching+": [100.0] * 3,
        }
    )


def _compute(pitcher_rv, k=1.0):
    bwar = _bwar_pitch_df()
    return swar.compute(pitcher_rv, _pitching_df(), bwar, park_factors.from_bwar_pitch(bwar), k=k)


def _league_ra9():
    return 9 * (80 + 80 + 30) / (180 + 180 + 60)


def test_compute_league_average_pitcher_gets_replacement_bump():
    """Everyone at league-average total_rv -> sRA9 is the league RA9 and
    sWAR is exactly the chassis's replacement bump, in either park."""
    out = _compute(_pitcher_rv())
    assert out["sRA9"].tolist() == pytest.approx([_league_ra9()] * 3)

    chassis_df = pd.DataFrame({"IP": [180.0, 180.0, 60.0], "G": [30, 30, 60], "GS": [30, 30, 0]})
    league = pd.Series([_league_ra9()] * 3)
    expected = pitcher_war_chassis.war_from_rate(chassis_df, league, _league_ra9(), pd.Series([1.0] * 3))
    assert out["sWAR"].tolist() == pytest.approx(expected.tolist())
    assert out.loc[0, "sWAR"] == pytest.approx(0.12 * 180 / 9)
    assert out.loc[2, "sWAR"] == pytest.approx(0.03 * 60 / 9)
    assert out.loc[1, "sWAR"] == pytest.approx(out.loc[0, "sWAR"])


def test_compute_average_pitcher_matches_chassis_at_league_rate():
    """With the rest of the league non-average, a pitcher at total_rv 0 is
    at league RA9 -- compare against the chassis called directly with the
    compute's IP-weighted league sRA9."""
    out = _compute(_pitcher_rv(total=(0.0, 0.004, -0.002)))
    assert out.loc[0, "sRA9"] == pytest.approx(_league_ra9())
    ip = np.array([180.0, 180.0, 60.0])
    league_rate = np.average(out["sRA9"], weights=ip)
    row = pd.DataFrame({"IP": [180.0], "G": [30], "GS": [30]})
    expected = pitcher_war_chassis.war_from_rate(
        row, pd.Series([_league_ra9()]), league_rate, pd.Series([1.0])
    )
    assert out.loc[0, "sWAR"] == pytest.approx(expected.iloc[0])


def test_compute_sra9_math_and_better_stuff_means_more_swar():
    out = _compute(_pitcher_rv(total=(0.004, 0.0, 0.0)))
    saved_p9 = 0.004 * 2800 / 180 * 9
    assert out.loc[0, "sRA9"] == pytest.approx(_league_ra9() - saved_p9)
    base = _compute(_pitcher_rv())
    assert out.loc[0, "sWAR"] > base.loc[0, "sWAR"]
    # k scales the models' runs linearly.
    half = _compute(_pitcher_rv(total=(0.004, 0.0, 0.0)), k=0.5)
    assert half.loc[0, "sRA9"] == pytest.approx(_league_ra9() - 0.5 * saved_p9)


def test_compute_park_factor_does_not_move_sra9_or_swar_rate():
    """Pitchers 1 and 2 are identical except for park (PF 1.00 vs 1.10).
    sRA9 is built from pitch physics, so it's already park-neutral: same
    sRA9, and the chassis must not hand the hitter-park pitcher a bonus."""
    out = _compute(_pitcher_rv(total=(0.003, 0.003, -0.001)))
    assert out.loc[0, "PF"] == pytest.approx(1.0)
    assert out.loc[1, "PF"] == pytest.approx(1.10)
    assert out.loc[0, "sRA9"] == pytest.approx(out.loc[1, "sRA9"])
    # Park-rate is identical, so RAA is identical; only dynamic runs-per-win
    # could differ, and it takes park_rate (not PF) -> identical sWAR.
    assert out.loc[1, "sWAR"] == pytest.approx(out.loc[0, "sWAR"])


def test_compute_pitcher_without_statcast_is_league_average():
    rv = _pitcher_rv().iloc[:2]  # reliever has no pitch data
    out = _compute(rv)
    assert out.loc[2, "sRA9"] == pytest.approx(_league_ra9())
    assert np.isnan(out.loc[2, "Pitching+"])
    assert len(out) == 3
    assert "pitcher" not in out.columns and "team_ID" not in out.columns
