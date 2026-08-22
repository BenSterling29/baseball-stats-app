"""cWAR: a FIP-flavored pitcher WAR that also credits the parts of contact
quality that research shows are actually repeatable skill, rather than only
the three FIP true outcomes (K, BB, HR).

Design (see conversation / commit message for the full research rationale):
  - FIP core (K, BB, HBP, HR, IP) carries most of the weight -- K% and BB%
    are the most repeatable pitcher skills (year-to-year r ~ 0.7-0.8).
  - Statcast's xERA (from the expected-stats leaderboard) adds back contact
    quality -- exit velocity/launch angle allowed -- which FIP ignores
    entirely. This is a real, moderately repeatable skill, just noisier
    than K/BB, so it gets a smaller weight that shrinks toward FIP when a
    pitcher's batted-ball sample is small.
  - A small batted-ball-mix adjustment rewards ground-ball/pop-up rates,
    which are far more repeatable than BABIP itself (the whole premise of
    DIPS) and are otherwise invisible to FIP.
"""
import numpy as np
import pandas as pd

FIP_WEIGHT_BASE = 0.70
CONTACT_WEIGHT_BASE = 0.25

# Batted-ball metrics need a bigger sample to stabilize than K/BB do, so a
# pitcher's contact-quality (xERA) weight is scaled down below these
# thresholds instead of trusted at full strength from a handful of balls in
# play. Batted-ball *mix* (GB/FB/PU) is derived from full-season-tracked
# data and is itself a repeatable skill, so it needs a smaller IP sample to
# trust than a single-season exit-velocity read does.
BIP_FULL_TRUST = 100
IP_FULL_TRUST = 50

GB_RUN_COEF = 1.5   # runs/9 penalty per unit (0-1) of GB% below league average
FB_RUN_COEF = 0.8   # runs/9 penalty per unit of FB% above league average
PU_RUN_COEF = 3.0   # runs/9 credit per unit of PU% above league average (weakest contact type)

REPLACEMENT_LEVEL_FACTOR = 1.13  # standard sabermetric constant: replacement-level RA9 runs ~13% higher than league average
RUNS_PER_WIN = 10.0  # standard rule-of-thumb approximation, roughly stable across normal run environments


def _batted_ball_rates(df):
    """bref's column is labeled 'GB/FB' but pybaseball actually returns GB%
    (confirmed against known extreme groundball/flyball pitchers -- e.g.
    submarine sinkerballer Tyler Rogers tops the leaderboard at 0.64, not a
    plausible GB/FB ratio). FB% is the remainder after LD%/PU%/GB%."""
    gb_pct = pd.to_numeric(df.get("GB/FB"), errors="coerce")
    ld = pd.to_numeric(df.get("LD"), errors="coerce")
    pu = pd.to_numeric(df.get("PU"), errors="coerce")
    fb_pct = 1 - ld - pu - gb_pct
    return gb_pct, fb_pct


def compute(pitching_df, expected_df):
    """pitching_df: pb.pitching_stats_bref() output.
    expected_df: pb.statcast_pitcher_expected_stats() output (any minPA).
    Returns pitching_df with FIP/xERA/batted-ball/cWAR columns added.
    """
    # pitching_stats_bref returns a 1-based index; reset it so every Series
    # computed below (and the post-merge frame) shares one consistent index
    # instead of silently misaligning on combination.
    df = pitching_df.reset_index(drop=True).copy()
    for col in ("IP", "SO", "BB", "HBP", "HR", "ER"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    league_ip = df["IP"].sum()
    league_era = 9 * df["ER"].sum() / league_ip
    fip_constant = league_era - (
        13 * df["HR"].sum() + 3 * (df["BB"].sum() + df["HBP"].sum()) - 2 * df["SO"].sum()
    ) / league_ip

    df["FIP"] = (
        13 * df["HR"] + 3 * (df["BB"] + df["HBP"]) - 2 * df["SO"]
    ) / df["IP"] + fip_constant

    gb_pct, fb_pct = _batted_ball_rates(df)
    pu_pct = pd.to_numeric(df.get("PU"), errors="coerce")
    df["GB%"] = gb_pct
    df["FB%"] = fb_pct
    df["PU%"] = pu_pct

    exp = expected_df.reset_index(drop=True).copy()
    exp["mlbID"] = pd.to_numeric(exp["player_id"], errors="coerce")
    exp["xera"] = pd.to_numeric(exp["xera"], errors="coerce")
    exp["bip"] = pd.to_numeric(exp["bip"], errors="coerce")
    df["mlbID"] = pd.to_numeric(df["mlbID"], errors="coerce")
    df = df.merge(exp[["mlbID", "xera", "bip"]], on="mlbID", how="left")
    df = df.rename(columns={"xera": "xERA"})

    league_gb = np.average(df["GB%"].dropna(), weights=df.loc[df["GB%"].notna(), "IP"]) if df["GB%"].notna().any() else 0
    league_fb = np.average(df["FB%"].dropna(), weights=df.loc[df["FB%"].notna(), "IP"]) if df["FB%"].notna().any() else 0
    league_pu = np.average(df["PU%"].dropna(), weights=df.loc[df["PU%"].notna(), "IP"]) if df["PU%"].notna().any() else 0

    # Missing batted-ball data falls back to league average, which makes the
    # adjustment a no-op for that pitcher rather than injecting bias.
    gb_for_adj = df["GB%"].fillna(league_gb)
    fb_for_adj = df["FB%"].fillna(league_fb)
    pu_for_adj = df["PU%"].fillna(league_pu)

    batted_ball_adj = (
        -GB_RUN_COEF * (gb_for_adj - league_gb)
        + FB_RUN_COEF * (fb_for_adj - league_fb)
        - PU_RUN_COEF * (pu_for_adj - league_pu)
    )
    ip_reliability = (df["IP"] / IP_FULL_TRUST).clip(upper=1).fillna(0)
    batted_ball_adj = batted_ball_adj * ip_reliability

    bip_reliability = (df["bip"] / BIP_FULL_TRUST).clip(upper=1).fillna(0)
    contact_weight = CONTACT_WEIGHT_BASE * bip_reliability
    fip_weight = FIP_WEIGHT_BASE + CONTACT_WEIGHT_BASE * (1 - bip_reliability)
    xera_filled = df["xERA"].fillna(df["FIP"])  # no matching contact-quality data -> lean fully on FIP

    df["blendedERA"] = fip_weight * df["FIP"] + contact_weight * xera_filled + batted_ball_adj

    replacement_ra9 = league_era * REPLACEMENT_LEVEL_FACTOR
    runs_above_replacement = (replacement_ra9 - df["blendedERA"]) * df["IP"] / 9
    df["cWAR"] = runs_above_replacement / RUNS_PER_WIN

    for col in ("FIP", "xERA", "GB%", "FB%", "PU%", "blendedERA"):
        df[col] = df[col].round(3)
    df["cWAR"] = df["cWAR"].round(2)

    return df.drop(columns=["mlbID", "bip"])
