"""Merges the three pitcher WAR methodologies -- bWAR (runs-allowed based,
bwar_pitching.py), fWAR (FIP based, fwar_pitching.py), and cWAR (FIP/xERA
blend, cwar.py) -- onto one row per pitcher, for side-by-side comparison.
Pure merge, no new math; each metric's own module is untouched by this.
"""


def compute(bwar_df, fwar_df, cwar_df):
    """Each *_df: the already-computed output of the matching compute()
    function (one row per pitcher, with mlbID/Name/Tm/IP plus that metric's
    own WAR column). Returns Name/Tm/IP plus bWAR/fWAR/cWAR, left-joined
    from bWAR's rows (all three share the same pitching_stats_bref source,
    so in practice every pitcher has all three) with a WAR_spread column --
    the gap between the highest and lowest of the three -- to make players
    the methodologies disagree on easy to spot.
    """
    b = bwar_df[["mlbID", "Name", "Tm", "TmID", "IP", "bWAR"]]
    f = fwar_df[["mlbID", "fWAR"]]
    c = cwar_df[["mlbID", "cWAR"]]

    df = b.merge(f, on="mlbID", how="left").merge(c, on="mlbID", how="left")
    war_cols = df[["bWAR", "fWAR", "cWAR"]]
    df["WAR_spread"] = (war_cols.max(axis=1) - war_cols.min(axis=1)).round(2)

    return df.drop(columns=["mlbID"])
