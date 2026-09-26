"""sWAR (prototype): a pitch-quality pitcher WAR from a Stuff model stacked
with a Command model. Pure pandas/numpy -- no pybaseball import, no model
training (the offline `scripts/swar_prototype.py` trains the models and
passes their per-pitch predictions in). Not wired into the app yet.

  - Stuff: expected run value from a pitch's physical traits alone
    (velocity, movement, spin, release, extension, and differences from the
    pitcher's primary fastball), after tjStuff+
    (github.com/tnestico/tjstuff_plus). No location, count, or batter.
  - Command: from OpenCommand's inferred catcher target
    (huggingface.co/datasets/tomdoyo/open-command, CC BY-NC-SA 4.0), the
    runs a pitch gained or lost by *missing* its target -- the command
    model's prediction at the actual miss minus its prediction had the
    pitch hit the target exactly. Where the catcher set up (and the count)
    is held fixed, so a pitcher isn't credited for target selection.

Both terms are per pitch, pitcher-perspective (positive = good for the
pitcher), centered on the season's league average. Per-pitch runs saved
become a runs-allowed-per-9 rate that goes through the same
`pitcher_war_chassis.war_from_rate` as bWAR/fWAR/cWAR, so sWAR stays
comparable with them.
"""
import numpy as np
import pandas as pd

from app import park_factors, pitcher_war_chassis

FASTBALLS = ("FF", "SI", "FC")
BREAKING = ("SL", "ST", "SV", "CU", "KC", "CS")
OFFSPEED = ("CH", "FS", "FO", "SC")
PITCH_GROUPS = {**{p: "fastball" for p in FASTBALLS},
                **{p: "breaking" for p in BREAKING},
                **{p: "offspeed" for p in OFFSPEED}}
# Anything else (knuckleball, eephus, pitchout, intentional/auto balls,
# unknown) is left out of both models.

STUFF_FEATURES = [
    "release_speed", "release_spin_rate", "spin_axis_m", "release_extension",
    "release_pos_x_m", "release_pos_z", "hb_in", "ivb_in",
    "velo_diff", "hb_diff", "ivb_diff",
]
COMMAND_FEATURES = [
    "miss_x_arm", "miss_z", "miss_dist",
    "target_x_away", "target_z_rel",
    "balls", "strikes", "same_hand", "group_code",
]
GROUP_CODES = {"fastball": 0, "breaking": 1, "offspeed": 2}

# Pitches of regression toward league average when rolling up to a pitcher,
# from the prototype script's split-half reliability check on 2023-2026
# (implied k: Stuff ~4-5, Command ~40-55 at 250-1000 pitch caps). Stuff is a
# function of pitch physics, so it is nearly noise-free within a season;
# that is reliability, not proof it predicts anything.
STUFF_SHRINK_PITCHES = 5
COMMAND_SHRINK_PITCHES = 50


def pitch_group(pitch_type):
    return pitch_type.map(PITCH_GROUPS)


def pitch_run_value(df, woba_scale=None):
    """Pitcher-perspective run value of each pitch.

    Statcast's delta_run_exp is the change in run expectancy from the
    batting team's side, so the pitcher's value is its negative. With
    `woba_scale`, balls in play are de-lucked: the gap between the actual
    and expected (exit velo/launch angle) wOBA, in runs, is removed while
    the base-out context stays -- rv - (xwOBA - wOBA) / wOBA_scale.
    """
    rv = -pd.to_numeric(df["delta_run_exp"], errors="coerce")
    if woba_scale is None:
        return rv
    xwoba = pd.to_numeric(df["estimated_woba_using_speedangle"], errors="coerce")
    woba = pd.to_numeric(df["woba_value"], errors="coerce")
    bip = (df["type"] == "X") & (pd.to_numeric(df["woba_denom"], errors="coerce") == 1) & xwoba.notna()
    # A pitcher allowing less wOBA than expected (luck) gives that back.
    return rv.where(~bip, rv + (woba - xwoba) / woba_scale)


def stuff_features(df):
    """Adds STUFF_FEATURES columns. Statcast x is catcher's view (+ = first-
    base side), so a righty's arm side is negative; x is flipped for righties
    so arm side is positive for both hands. Movement is in inches. Differences are from the
    pitcher-season's primary fastball (most-thrown FF/SI/FC); a pitcher
    with no fastball is measured from his hardest pitch type instead."""
    df = df.reset_index(drop=True).copy()
    lefty = df["p_throws"] == "L"
    sign = np.where(lefty, 1.0, -1.0)
    df["hb_in"] = pd.to_numeric(df["pfx_x"], errors="coerce") * 12 * sign
    df["ivb_in"] = pd.to_numeric(df["pfx_z"], errors="coerce") * 12
    df["release_pos_x_m"] = pd.to_numeric(df["release_pos_x"], errors="coerce") * sign
    axis = pd.to_numeric(df["spin_axis"], errors="coerce")
    df["spin_axis_m"] = np.where(lefty, 360 - axis, axis)

    ref = _reference_pitch(df)
    df = df.merge(ref, on=["season", "pitcher"], how="left")
    df["velo_diff"] = df["release_speed"] - df["ref_velo"]
    df["hb_diff"] = df["hb_in"] - df["ref_hb"]
    df["ivb_diff"] = df["ivb_in"] - df["ref_ivb"]
    return df.drop(columns=["ref_velo", "ref_hb", "ref_ivb"])


def _reference_pitch(df):
    by_type = (
        df.groupby(["season", "pitcher", "pitch_type"])
        .agg(n=("release_speed", "size"), ref_velo=("release_speed", "mean"),
             ref_hb=("hb_in", "mean"), ref_ivb=("ivb_in", "mean"))
        .reset_index()
    )
    by_type["is_fb"] = by_type["pitch_type"].isin(FASTBALLS)
    # Fastballs first (most-thrown wins), then hardest pitch as a fallback.
    # Count only ranks fastballs; among non-fastballs velo alone decides.
    by_type["fb_n"] = by_type["n"].where(by_type["is_fb"], 0)
    by_type = by_type.sort_values(["is_fb", "fb_n", "ref_velo"], ascending=False)
    ref = by_type.drop_duplicates(["season", "pitcher"])
    return ref[["season", "pitcher", "ref_velo", "ref_hb", "ref_ivb"]]


def command_features(df):
    """Adds COMMAND_FEATURES from OpenCommand's per-pitch location and
    inferred target (inches, catcher's view, +x = first-base side).
    Target x is mirrored by batter hand (+ = away from the batter), miss x
    by pitcher hand (+ = arm side); target height is relative to the
    batter's zone (0 = bottom, 1 = top). Rows without a plausible target
    get NaN features and are excluded from the command model."""
    df = df.copy()
    has_target = df["plausible"].eq(True)
    tx = pd.to_numeric(df["inferred_x_in"], errors="coerce").where(has_target)
    tz = pd.to_numeric(df["inferred_z_in"], errors="coerce").where(has_target)
    px = pd.to_numeric(df["plate_x_in"], errors="coerce")
    pz = pd.to_numeric(df["plate_z_in"], errors="coerce")

    pitcher_sign = np.where(df["p_throws"] == "L", 1.0, -1.0)  # + = arm side
    batter_sign = np.where(df["stand"] == "L", -1.0, 1.0)
    df["miss_x_arm"] = (px - tx) * pitcher_sign
    df["miss_z"] = pz - tz
    df["miss_dist"] = np.hypot(df["miss_x_arm"], df["miss_z"])
    df["target_x_away"] = tx * batter_sign
    zone_h = (df["sz_top"] - df["sz_bot"]) * 12
    df["target_z_rel"] = (tz - df["sz_bot"] * 12) / zone_h
    df["same_hand"] = (df["p_throws"] == df["stand"]).astype(float)
    df["group_code"] = df["group"].map(GROUP_CODES)
    return df


def on_target(features):
    """The same command-feature rows, as if every pitch hit its target."""
    out = features.copy()
    out[["miss_x_arm", "miss_z", "miss_dist"]] = 0.0
    return out


def aggregate(pitches, stuff_k=STUFF_SHRINK_PITCHES, command_k=COMMAND_SHRINK_PITCHES):
    """Per-pitch predictions -> one row per pitcher-season.

    pitches needs: season, pitcher, stuff_rv (every modeled pitch) and
    command_rv (NaN where there was no usable target). Each term is
    centered on the season's league per-pitch mean, then shrunk toward 0
    (league average) by n / (n + k). Returns per-pitch means plus
    100-scaled indexes (Stuff+/Cmd+: 100 = average, 10 = one pitcher-level
    standard deviation, pitch-weighted).
    """
    df = pitches.copy()
    for col in ("stuff_rv", "command_rv"):
        df[col] = df[col] - df.groupby("season")[col].transform("mean")

    g = df.groupby(["season", "pitcher"])
    out = pd.DataFrame({
        "pitches": g.size(),
        "stuff_sum": g["stuff_rv"].sum(),
        "cmd_n": g["command_rv"].count(),
        "cmd_sum": g["command_rv"].sum(),
    }).reset_index()
    out["stuff_rv"] = out["stuff_sum"] / (out["pitches"] + stuff_k)
    out["command_rv"] = out["cmd_sum"] / (out["cmd_n"] + command_k)
    out["total_rv"] = out["stuff_rv"] + out["command_rv"]

    for col, name in (("stuff_rv", "Stuff+"), ("command_rv", "Cmd+"), ("total_rv", "Pitching+")):
        out[name] = _plus_index(out, col)
    return out.drop(columns=["stuff_sum", "cmd_sum"])


def _plus_index(df, col):
    def scale(s):
        w = df.loc[s.index, "pitches"]
        mu = np.average(s, weights=w)
        sd = np.sqrt(np.average((s - mu) ** 2, weights=w))
        return 100 + 10 * (s - mu) / sd if sd > 0 else pd.Series(100.0, index=s.index)
    return df.groupby("season")[col].transform(scale)


def compute(pitcher_rv, pitching_df, bwar_pitch_df, park_df, k=1.0):
    """pitcher_rv: aggregate() output for ONE season.
    pitching_df: pb.pitching_stats_bref() for that season.
    k: runs-allowed per 9 per unit of predicted runs saved per 9 -- 1.0
       takes the models at face value; the prototype fits it against
       next-season runs allowed.
    Returns pitching_df with Stuff+/Cmd+/Pitching+, sRA9 and sWAR added.
    """
    df = pitching_df.reset_index(drop=True).copy()  # bref's 1-based index
    for col in ("IP", "R", "G", "GS"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["mlbID"] = pd.to_numeric(df["mlbID"], errors="coerce")

    league_ip = df["IP"].sum()
    league_ra9 = 9 * df["R"].sum() / league_ip

    cols = ["pitcher", "pitches", "stuff_rv", "command_rv", "total_rv", "Stuff+", "Cmd+", "Pitching+"]
    df = df.merge(pitcher_rv[cols], left_on="mlbID", right_on="pitcher", how="left")
    runs_saved_p9 = df["total_rv"] * df["pitches"] / df["IP"] * 9
    # No Statcast pitches (rare): league average rather than a guess.
    df["sRA9"] = league_ra9 - k * runs_saved_p9.fillna(0.0)

    team_lookup = park_factors.primary_team(bwar_pitch_df)
    df = df.merge(team_lookup, left_on="mlbID", right_on="mlb_ID", how="left")
    df["PF"] = park_factors.attach(df, park_df, team_col="team_ID")
    # sRA9 is already park-neutral (it's built from pitch physics and
    # per-pitch run values, not the pitcher's actual runs allowed), so it
    # must not be park-adjusted again. The chassis divides the rate by PF,
    # so pre-multiply to cancel that; PF still feeds dynamic runs-per-win.
    rate_r9 = df["sRA9"] * df["PF"]
    league_rate = np.average(df["sRA9"], weights=df["IP"])  # IP-weighted, per AGENTS.md
    df["sWAR"] = pitcher_war_chassis.war_from_rate(df, rate_r9, league_rate, df["PF"])
    return df.drop(columns=["pitcher", "mlb_ID", "team_ID"], errors="ignore")
