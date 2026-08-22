"""Per-team park factors, derived from Baseball-Reference's daily pitcher WAR
file rather than a static table -- that keeps factors current every season
with no manual maintenance. `PPF` (100 = neutral) is bref's own park factor;
we don't have FanGraphs' published one, so this is a documented
approximation, not a bit-exact match.

Batters reuse the pitching-derived PPF. bref also publishes a separate BPF,
but only PPF is pulled here (via `pb.bwar_pitch`); the two are close for the
same stadium in the same season.
"""
import pandas as pd

NEUTRAL_PF = 1.0


def primary_team(bwar_pitch_df):
    """bwar_pitch has one row per player-team-stint; take the highest-IP
    stint's team as primary, e.g. for a park factor lookup."""
    df = bwar_pitch_df.copy()
    df["mlb_ID"] = pd.to_numeric(df["mlb_ID"], errors="coerce")
    df["IPouts"] = pd.to_numeric(df["IPouts"], errors="coerce")
    idx = df.groupby("mlb_ID")["IPouts"].idxmax()
    return df.loc[idx, ["mlb_ID", "team_ID"]]


def from_bwar_pitch(bwar_pitch_df):
    """bwar_pitch_df: pb.bwar_pitch(return_all=True) output, already
    filtered to one season. Returns DataFrame[team_ID, PF] (~1.0 = neutral).
    """
    df = bwar_pitch_df.copy()
    df["PF"] = pd.to_numeric(df["PPF"], errors="coerce") / 100.0
    return df.groupby("team_ID", as_index=False)["PF"].mean()


def attach(df, park_df, team_col="team_ID"):
    """Left-join PF onto df by team; teams with no match (traded players
    with no primary team resolved, or a team code park_df doesn't have)
    fall back to NEUTRAL_PF so missing park data is a no-op, not a bias."""
    merged = df.merge(park_df, on=team_col, how="left", suffixes=("", "_pf"))
    return merged["PF"].fillna(NEUTRAL_PF)
