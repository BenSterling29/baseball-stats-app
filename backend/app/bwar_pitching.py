"""bWAR for pitchers: an RA9 (actual-runs-allowed) based WAR, following
Baseball-Reference's own methodology in spirit
(https://www.baseball-reference.com/about/war_explained_pitch.shtml), with
two disclosed simplifications:

  - bref's real formula further adjusts for the strength of the batters a
    pitcher actually faced and the quality of the defense playing behind
    him (RA9opp, RA9def, RA9role) -- neither is available from pybaseball,
    so this compares a pitcher's park-adjusted RA9 to a flat league
    average instead of an opponent/defense-adjusted one.
  - Replacement level and the starter/reliever split reuse FanGraphs' own
    published win-percentage gap (.12/.03 WPG, see fwar_pitching.py) rather
    than bref's own (unpublished-via-pybaseball) replacement constant --
    the shape of the adjustment, rewarding a starter's larger workload, is
    the same idea either way.

The key methodological difference from `fwar_pitching.py` is the rate stat
itself: bWAR uses the pitcher's own actual runs allowed (RA9), not an
FIP-estimated one -- Baseball-Reference deliberately credits/blames a
pitcher for everything that happened while he was in the game, including
BABIP/sequencing/defense, rather than isolating FIP's three "true
outcomes." `fwar_pitching.py` and `cwar.py` are untouched by this.
"""
import pandas as pd

from app import park_factors

REPLACEMENT_WPG_RELIEVER = 0.03  # see fwar_pitching.py
REPLACEMENT_WPG_STARTER = 0.12


def compute(pitching_df, bwar_pitch_df, park_df):
    """pitching_df: pb.pitching_stats_bref() output.
    bwar_pitch_df: pb.bwar_pitch(return_all=True) output, pre-filtered to season.
    park_df: park_factors.from_bwar_pitch() output (team_ID -> PF).
    Returns pitching_df with RA9/PF/bWAR columns added.
    """
    # pitching_stats_bref returns a 1-based index; reset before any
    # Series-producing math, per the documented bref gotcha (see cwar.py).
    df = pitching_df.reset_index(drop=True).copy()
    for col in ("IP", "R", "G", "GS"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["mlbID"] = pd.to_numeric(df["mlbID"], errors="coerce")

    league_ip = df["IP"].sum()
    league_ra9 = 9 * df["R"].sum() / league_ip

    df["RA9"] = 9 * df["R"] / df["IP"]

    team_lookup = park_factors.primary_team(bwar_pitch_df)
    df = df.merge(team_lookup, left_on="mlbID", right_on="mlb_ID", how="left")
    df["PF"] = park_factors.attach(df, park_df, team_col="team_ID")

    park_ra9 = df["RA9"] / df["PF"]
    raa_p9 = league_ra9 - park_ra9

    # Dynamic runs-per-win: same idea as fwar_pitching.py -- a pitcher's own
    # innings/game and park-adjusted run environment shift how many runs one
    # win is worth for him.
    ip_per_g = df["IP"] / df["G"]
    dynamic_rpw = (((18 - ip_per_g) * league_ra9 + ip_per_g * park_ra9) / 18 + 2) * 1.5

    wins_above_avg_per_g = raa_p9 / dynamic_rpw
    gs_share = (df["GS"] / df["G"]).clip(0, 1).fillna(0)
    replacement_wpg = REPLACEMENT_WPG_RELIEVER * (1 - gs_share) + REPLACEMENT_WPG_STARTER * gs_share
    wins_above_rep_per_g = wins_above_avg_per_g + replacement_wpg

    df["bWAR"] = (wins_above_rep_per_g * df["IP"] / 9).round(1)
    df["RA9"] = df["RA9"].round(2)
    df["PF"] = df["PF"].round(3)

    return df.drop(columns=["mlb_ID", "team_ID"], errors="ignore")
