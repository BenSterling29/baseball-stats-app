"""fWAR for pitchers: FIP-based, scaled to a runs-allowed basis, park- and
dynamic-runs-per-win-adjusted, with FanGraphs' starter/reliever
replacement-level split. Follows FanGraphs' published methodology
(https://library.fangraphs.com/war/calculating-war-pitchers/) with two
disclosed simplifications:

  - Standard FIP is used instead of FanGraphs' "ifFIP" (which folds infield
    fly balls in with strikeouts) -- bref's batted-ball columns give a rate,
    not a raw infield-fly count, and the correction is small.
  - The relief-pitcher leverage-index regression and the final league-wide
    additive correction (calibrated so total published pitcher WAR sums to
    a fixed target) are both omitted -- game leverage index isn't available
    from pybaseball, and the league correction is a global calibration
    nicety that doesn't change player-to-player rankings.

This is a close approximation of fWAR, not a bit-exact replica. `cwar.py` is
a deliberately different, xERA-blended metric and is untouched by this.
"""
import pandas as pd

from app import park_factors, pitcher_war_chassis


def compute(pitching_df, bwar_pitch_df, park_df, guts_for_season):
    """pitching_df: pb.pitching_stats_bref() output.
    bwar_pitch_df: pb.bwar_pitch(return_all=True) output, pre-filtered to season.
    park_df: park_factors.from_bwar_pitch() output (team_ID -> PF).
    guts_for_season: guts.for_season(season) output.
    Returns pitching_df with FIP/PF/fWAR columns added.
    """
    g = guts_for_season
    # pitching_stats_bref returns a 1-based index; reset before any
    # Series-producing math, per the documented bref gotcha (see cwar.py).
    df = pitching_df.reset_index(drop=True).copy()
    for col in ("IP", "SO", "BB", "HBP", "HR", "ER", "R", "G", "GS"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["mlbID"] = pd.to_numeric(df["mlbID"], errors="coerce")

    league_ip = df["IP"].sum()
    league_era = 9 * df["ER"].sum() / league_ip
    league_ra9 = 9 * df["R"].sum() / league_ip

    df["FIP"] = (13 * df["HR"] + 3 * (df["BB"] + df["HBP"]) - 2 * df["SO"]) / df["IP"] + g["cFIP"]
    # Scale FIP (an ERA-estimator) onto the same runs-allowed (RA9) basis the
    # rest of the formula uses, via the league gap between the two.
    fip_r9 = df["FIP"] + (league_ra9 - league_era)
    # IP-weighted, like league_era/league_ra9 above -- a plain .mean() here
    # is badly skewed by mop-up/one-inning pitchers with extreme small-
    # sample FIPs. Simplification: MLB-wide, not split by AL/NL.
    league_fip_r9 = (fip_r9 * df["IP"]).sum() / league_ip

    team_lookup = park_factors.primary_team(bwar_pitch_df)
    df = df.merge(team_lookup, left_on="mlbID", right_on="mlb_ID", how="left")
    df["PF"] = park_factors.attach(df, park_df, team_col="team_ID")

    df["fWAR"] = pitcher_war_chassis.war_from_rate(df, fip_r9, league_fip_r9, df["PF"]).round(1)
    df["FIP"] = df["FIP"].round(2)
    df["PF"] = df["PF"].round(3)

    return df.drop(columns=["mlb_ID", "team_ID"], errors="ignore")
