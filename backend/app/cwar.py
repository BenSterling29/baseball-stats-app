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

# Hand-picked, and checked against data rather than fitted to it.
# scripts/tune_cwar_weights.py regresses next-season park-adjusted ERA on
# (rFIP, xERA) over the 2021-2025 season pairs. The empirical refit
# (~0.66/0.34) beat these on only 1 of 4 leave-one-season-out folds, by
# at most 0.0003 runs of RMSE, while losing the others by up to 0.007, and
# its own weight swung 0.53-0.78 across folds. The data can't distinguish
# mixes in that range, so there's no case for moving off this pair. Re-run
# that script before changing them.
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


def batted_ball_adjustment(df):
    """Runs/9 adjustment for batted-ball mix vs league average, scaled by
    IP reliability. `df` needs numeric IP plus GB%/FB%/PU% columns.

    League averages are IP-weighted over whatever frame is passed in, so the
    runtime passes the whole league. Missing batted-ball data falls back to
    league average, making the adjustment a no-op for that pitcher rather
    than injecting bias.

    Shared with scripts/tune_cwar_weights.py so its checks test exactly the
    adjustment the runtime applies; do not inline this math elsewhere.
    """
    def _league(col):
        ok = df[col].notna() & df["IP"].notna()
        return np.average(df.loc[ok, col], weights=df.loc[ok, "IP"]) if ok.any() else 0.0

    league = {col: _league(col) for col in ("GB%", "FB%", "PU%")}
    adj = (
        -GB_RUN_COEF * (df["GB%"].fillna(league["GB%"]) - league["GB%"])
        + FB_RUN_COEF * (df["FB%"].fillna(league["FB%"]) - league["FB%"])
        - PU_RUN_COEF * (df["PU%"].fillna(league["PU%"]) - league["PU%"])
    )
    ip_reliability = (df["IP"] / IP_FULL_TRUST).clip(lower=0, upper=1).fillna(0)
    return adj * ip_reliability


def rfip(df):
    """HR-regressed FIP, as a Series. `df` must be a bref pitching frame with
    IP/SO/BB/HBP/HR/ER/BF already numeric-coerced and a 0-based index.

    Returns (rfip_series, fip_series, regression_diagnostics_dict). The plain
    FIP is returned alongside so callers can display both without recomputing.

    The FIP constant is derived from *actual* league totals, and the HR
    regression is exactly league-total-preserving, so IP-weighted league
    rFIP equals league FIP -- the regression only moves HRs *between*
    pitchers. That does not come free: each pitcher's shift is
    (1 - r_i) * (expected_i - HR_i), and with reliability r_i varying across
    pitchers, a plain league HR/FB rate does not make those shifts sum to
    zero. So the rate used for expected HR is the (1 - r)-weighted one,
    which is the unique rate that does (see `league_hr_per_fb` below).

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
    # FB% is derived as 1 - LD - PU - GB%, and bref's rounded rates can sum
    # past 1 on tiny samples, pushing it (and so fb_count) slightly
    # negative. A negative count would make reliability negative and the
    # regression extrapolate outside both the actual and expected HR.
    fb_count = (fb_pct * bip).clip(lower=0)

    # A pitcher with no usable batted-ball data keeps his own HR total
    # (reliability 1) rather than being regressed toward a number we can't
    # compute for him -- missing data stays a no-op, per this module's
    # convention for the batted-ball and contact-quality inputs.
    fb_reliability = (fb_count / FB_FULL_TRUST).clip(upper=1).fillna(1.0)
    shrink = 1 - fb_reliability

    # League HR/FB, weighted by how much each pitcher is being regressed.
    # This is the rate k solving sum(shrink * (k * fb_count - HR)) = 0, which
    # is exactly what makes the regression league-total-preserving (see the
    # docstring). Pitchers with no FB data have shrink 0 and drop out of
    # both sums, so they can no longer inflate the numerator as they did
    # when this was a plain sum(HR) / sum(fb_count).
    weighted_fb = (shrink * fb_count).sum()
    league_hr_per_fb = (
        (shrink * df["HR"]).sum() / weighted_fb if weighted_fb > 0 else float("nan")
    )
    expected_hr = fb_count * league_hr_per_fb
    regressed_hr = fb_reliability * df["HR"] + shrink * expected_hr.fillna(df["HR"])

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

    batted_ball_adj = batted_ball_adjustment(df)

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
    # launch angle*, so it already encodes batted-ball mix, and adding the
    # adjustment on top of a full-strength xERA double-counts. That's the
    # reason for the fade; the data only weakly backs it. Against
    # next-season ERA over the 2021-2025 season pairs (full-xERA-trust
    # pitchers only), dropping the adjustment beat keeping it on 3 of 4
    # folds, with mean RMSE 1.0059 none / 1.0066 half / 1.0081 full -- small
    # gaps, inconclusive by the script's all-folds rule
    # (scripts/tune_cwar_weights.py).
    #
    # In practice this leaves the adjustment nearly inert: multiplied by
    # ip_reliability (inside batted_ball_adjustment), it peaks at about 17%
    # strength near 17 IP and is ~0 for anyone past ~35 IP, since those
    # pitchers have 100+ tracked balls in play. It survives only as a small
    # nudge for low-inning arms with little or no Statcast data.
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
