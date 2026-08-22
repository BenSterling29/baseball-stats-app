"""Resolves Baseball-Reference's ambiguous `Tm` column to real team codes.

`pitching_stats_bref`/`batting_stats_bref` give a player's team as a bare
city name (e.g. "Chicago", "Los Angeles", "New York") with no franchise
suffix -- fine for one-team cities, but it silently merges the Cubs with
the White Sox, the Dodgers with the Angels, and the Yankees with the Mets
into a single filter bucket. `pb.bwar_bat`/`pb.bwar_pitch`'s `team_ID`
column (e.g. "CHC"/"CHW", "LAD"/"LAA", "NYM"/"NYY") doesn't have this
problem, so this attaches it as a separate `TmID` column for the frontend
to filter on, alongside (not replacing) the human-readable `Tm` column.
"""
import pandas as pd


def attach(df, bwar_df, mlbid_col="mlbID"):
    """bwar_df: pb.bwar_bat/pb.bwar_pitch output (return_all=True),
    pre-filtered to season. Adds a `TmID` column to df: comma-joined team
    codes for that player-season, ordered by stint so a traded player's
    codes read in the order he actually played for them -- the same shape
    as `Tm`'s comma-joined city names, just unambiguous. Players bwar_df
    has no matching row for fall back to an empty string.
    """
    stints = bwar_df.copy()
    stints["mlb_ID"] = pd.to_numeric(stints["mlb_ID"], errors="coerce")
    stints["stint_ID"] = pd.to_numeric(stints["stint_ID"], errors="coerce")
    stints = stints.sort_values(["mlb_ID", "stint_ID"]).drop_duplicates(["mlb_ID", "team_ID"])

    team_ids = (
        stints.groupby("mlb_ID")["team_ID"]
        .apply(lambda s: ",".join(s))
        .rename("TmID")
        .reset_index()
    )

    # Some callers (e.g. raw pitching_stats_bref/batting_stats_bref output)
    # have mlbID as a string column; others (fwar/cwar/bwar compute output)
    # have already coerced it to numeric. Coerce a copy here so the merge
    # key always matches bwar_df's numeric mlb_ID, without mutating the
    # caller's original column's dtype.
    merge_key = pd.to_numeric(df[mlbid_col], errors="coerce")
    merged = df.merge(
        team_ids, left_on=merge_key, right_on="mlb_ID", how="left"
    )
    merged["TmID"] = merged["TmID"].fillna("")
    return merged.drop(columns=["mlb_ID"])
