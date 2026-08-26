"""cWAR: a FIP-flavored pitcher WAR that also credits the parts of contact
quality that research shows are actually repeatable skill, rather than only
the three FIP true outcomes (K, BB, HR).

Design (see conversation / commit message for the full research rationale):
  - FIP core (K, BB, HBP, HR, IP) carries most of the weight -- K% and BB%
    are the most repeatable pitcher skills (year-to-year r ~ 0.7-0.8). The
    HR term is regressed toward league HR/FB first (see `rfip` below), since
    raw HR total is by far the noisiest FIP input.
  - Statcast's xERA (from the expected-stats leaderboard) adds back contact
    quality -- exit velocity/launch angle allowed -- which FIP ignores
    entirely. This is a real, moderately repeatable skill, just noisier
    than K/BB, so it gets a smaller weight that shrinks toward FIP when a
    pitcher's batted-ball sample is small.
  - A small batted-ball-mix adjustment rewards ground-ball/pop-up rates,
    which are far more repeatable than BABIP itself (the whole premise of
    DIPS) and are otherwise invisible to FIP. It applies only in proportion
    to how little xERA is being trusted for that pitcher, since xERA already
    encodes batted-ball mix -- see the complementary weighting in `compute`.

Park adjustment applies to the rFIP component only, not to the whole blend:
rFIP is built from actual outcomes (HRs that Coors' altitude helped leave
the yard), so it's park-inflated, while xERA is derived from exit
velocity/launch angle -- batted-ball *inputs* that the park barely touches.
Dividing xERA by the park factor too would hand extreme-park pitchers a
second credit for the same effect. This is why cWAR passes an already-
park-adjusted rate to the shared chassis (with a neutral PF) instead of
letting the chassis divide, the way fwar/bwar correctly do for their
fully-outcome-based rates.
"""
import numpy as np
import pandas as pd

from app import park_factors, pitcher_war_chassis

# Validated, not fitted. scripts/tune_cwar_weights.py regressed next-season
# park-adjusted ERA on (rFIP, xERA) over the 2021-2025 season pairs: the
# empirical refit (0.66/0.34) failed to beat these on any of 4
# leave-one-season-out folds, and swung across folds (0.53-0.78), so the
# hand-picked pair stands. Re-run that script before changing them.
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

# HR/FB is the least repeatable of FIP's three inputs -- the reason xFIP
# exists at all -- so a pitcher's HR total is regressed toward what his own
# fly-ball count would yield at the league HR/FB rate. Full trust needs
# roughly two full seasons' worth of fly balls: a 180-IP starter allows only
# ~200, so even a workhorse keeps about half his own HR total and borrows the
# rest from league rate. Unlike xFIP (which discards the actual HR total
# entirely), this keeps real signal for pitchers with the sample to back it.
FB_FULL_TRUST = 400

GB_RUN_COEF = 1.5   # runs/9 penalty per unit (0-1) of GB% below league average
FB_RUN_COEF = 0.8   # runs/9 penalty per unit of FB% above league average
PU_RUN_COEF = 3.0   # runs/9 credit per unit of PU% above league average (weakest contact type)

# WAR conversion (park adjustment, dynamic runs-per-win, starter/reliever
# replacement split) lives in pitcher_war_chassis.py, shared with
# fwar_pitching.py and bwar_pitching.py so the three metrics are directly
# comparable -- only the rate stat (the blend below) is cWAR-specific.


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


def rfip(df):
    """HR-regressed FIP, as a Series. `df` must be a bref pitching frame with
    IP/SO/BB/HBP/HR/ER/BF already numeric-coerced and a 0-based index.

    Returns (rfip_series, fip_series, regression_diagnostics_dict). The plain
    FIP is returned alongside so callers can display both without recomputing.

    The FIP constant is derived from *actual* league totals, and the HR
    regression is league-total-preserving by construction (the league's
    expected-HR total equals its actual HR total, since league HR/FB is
    defined as that ratio), so league rFIP lands on league ERA the same way
    league FIP does -- the regression only moves HRs *between* pitchers.

    Shared with scripts/tune_cwar_weights.py so the weights are fitted on
    exactly the rate the runtime blends; do not inline this math elsewhere.
    """
    league_ip = df["IP"].sum()
    league_era = 9 * df["ER"].sum() / league_ip
    fip_constant = league_era - (
        13 * df["HR"].sum() + 3 * (df["BB"].sum() + df["HBP"].sum()) - 2 * df["SO"].sum()
    ) / league_ip

    def _fip_from(hr):
        return (13 * hr + 3 * (df["BB"] + df["HBP"]) - 2 * df["SO"]) / df["IP"] + fip_constant

    _, fb_pct = _batted_ball_rates(df)
    # Balls in play, from bref's batters-faced count. BF - SO - BB - HBP - HR
    # leaves everything that was actually put in play (sacrifices and
    # interference are a rounding error at this scale).
    bf = pd.to_numeric(df.get("BF"), errors="coerce")
    bip = bf - df["SO"] - df["BB"] - df["HBP"] - df["HR"]
    fb_count = fb_pct * bip

    league_fb_count = fb_count.sum()
    league_hr_per_fb = df["HR"].sum() / league_fb_count if league_fb_count > 0 else float("nan")
    expected_hr = fb_count * league_hr_per_fb

    # A pitcher with no usable batted-ball data keeps his own HR total
    # (reliability 1) rather than being regressed toward a number we can't
    # compute for him -- missing data stays a no-op, per this module's
    # convention for the batted-ball and contact-quality inputs.
    fb_reliability = (fb_count / FB_FULL_TRUST).clip(upper=1).fillna(1.0)
    regressed_hr = fb_reliability * df["HR"] + (1 - fb_reliability) * expected_hr.fillna(df["HR"])

    diagnostics = {
        "fip_constant": fip_constant,
        "league_era": league_era,
        "league_hr_per_fb": league_hr_per_fb,
    }
    return _fip_from(regressed_hr), _fip_from(df["HR"]), diagnostics


def compute(pitching_df, expected_df, bwar_pitch_df, park_df):
    """pitching_df: pb.pitching_stats_bref() output.
    expected_df: pb.statcast_pitcher_expected_stats() output (any minPA).
    bwar_pitch_df: pb.bwar_pitch(return_all=True) output, pre-filtered to
        season (for the primary-team lookup behind the park factor).
    park_df: park_factors.from_bwar_pitch() output (team_ID -> PF).
    Returns pitching_df with FIP/rFIP/xERA/batted-ball/PF/cWAR columns added.
    """
    # pitching_stats_bref returns a 1-based index; reset it so every Series
    # computed below (and the post-merge frame) shares one consistent index
    # instead of silently misaligning on combination.
    df = pitching_df.reset_index(drop=True).copy()
    for col in ("IP", "SO", "BB", "HBP", "HR", "ER", "R", "G", "GS", "BF"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    league_ip = df["IP"].sum()
    # rFIP (HR regressed toward league HR/FB) is what the blend uses; plain
    # FIP is kept as a display column so both are visible side by side.
    rfip_series, fip_series, fip_diag = rfip(df)
    league_era = fip_diag["league_era"]
    df["rFIP"] = rfip_series
    df["FIP"] = fip_series

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

    # Park-adjust the rFIP component only. rFIP is built from actual
    # outcomes and is park-inflated; xERA comes from exit velocity/launch
    # angle, which the park barely affects, so dividing it too would
    # double-credit extreme-park pitchers (see the module docstring).
    team_lookup = park_factors.primary_team(bwar_pitch_df)
    df = df.merge(team_lookup, left_on="mlbID", right_on="mlb_ID", how="left")
    df["PF"] = park_factors.attach(df, park_df, team_col="team_ID")
    rfip_park = df["rFIP"] / df["PF"]

    bip_reliability = (df["bip"] / BIP_FULL_TRUST).clip(upper=1).fillna(0)

    # Apply the batted-ball adjustment only where xERA ISN'T already
    # accounting for contact quality. xERA is built from exit velocity *and
    # launch angle*, so it already encodes batted-ball mix -- adding the
    # adjustment on top of a full-strength xERA double-counts. Measured:
    # against next-season ERA over the 2021-2025 season pairs, the
    # unconditional adjustment was worse than none on 4 of 4 folds, and
    # monotonically so (RMSE 1.0359 none / 1.0424 half / 1.0519 full; see
    # scripts/tune_cwar_weights.py). Complementary weighting keeps the
    # adjustment where it's the only contact signal available -- pitchers
    # with little or no tracked batted-ball data, whom that test could not
    # cover -- and fades it out exactly as xERA takes over.
    batted_ball_adj = batted_ball_adj * (1 - bip_reliability)
    # The rFIP/xERA weights must be a convex combination (sum to exactly 1) --
    # blendedERA is an ERA-scale estimate, so any shortfall in the weight sum
    # deflates it outright rather than reweighting it. Normalize the base
    # weights so their ratio is preserved but the total is 1.
    contact_weight_full = CONTACT_WEIGHT_BASE / (FIP_WEIGHT_BASE + CONTACT_WEIGHT_BASE)
    contact_weight = contact_weight_full * bip_reliability
    fip_weight = 1.0 - contact_weight
    # No matching contact-quality data -> lean fully on the (park-adjusted)
    # FIP side, so the blend stays on one consistent park basis.
    xera_filled = df["xERA"].fillna(rfip_park)

    df["blendedERA"] = fip_weight * rfip_park + contact_weight * xera_filled + batted_ball_adj

    # Scale the blend (an ERA estimator) onto an RA9 basis via the league
    # ER/R gap, then run the shared WAR chassis for the dynamic
    # runs-per-win and starter/reliever replacement split. The rate handed
    # over is already park-adjusted, so the chassis gets a neutral PF --
    # unlike fwar/bwar, which correctly let it divide their fully
    # outcome-based rates.
    league_ra9 = 9 * df["R"].sum() / league_ip
    blended_r9 = df["blendedERA"] + (league_ra9 - league_era)
    # IP-weighted league average, per the AGENTS.md gotcha -- a plain .mean()
    # is skewed by mop-up arms with extreme small-sample rates.
    league_blended_r9 = (blended_r9 * df["IP"]).sum() / league_ip

    neutral_pf = pd.Series(park_factors.NEUTRAL_PF, index=df.index)
    df["cWAR"] = pitcher_war_chassis.war_from_rate(df, blended_r9, league_blended_r9, neutral_pf)

    for col in ("FIP", "rFIP", "xERA", "GB%", "FB%", "PU%", "blendedERA", "PF"):
        df[col] = df[col].round(3)
    df["cWAR"] = df["cWAR"].round(2)

    return df.drop(columns=["bip", "mlb_ID", "team_ID"], errors="ignore")
