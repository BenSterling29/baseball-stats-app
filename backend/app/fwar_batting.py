"""fWAR for batters: wOBA-based runs above average, adjusted for park and
league, plus baserunning/fielding/positional value and a replacement-level
bump. Follows FanGraphs' published methodology
(https://library.fangraphs.com/war/war-position-players/) with two
disclosed substitutions:

  - Fielding value uses Statcast Outs Above Average (`fielding_runs_prevented`,
    from Baseball Savant's outs-above-average leaderboard) for non-catchers,
    and Statcast catcher framing runs for catchers -- both real, public,
    modern metrics, but neither is FanGraphs' own UZR (proprietary, no free
    source exists). A player Statcast doesn't cover (e.g. a pitcher's rare
    plate appearance) falls back to Baseball-Reference's own `runs_field`.
  - The positional adjustment reuses bref's `runs_position` (from
    `pb.bwar_bat`) rather than FanGraphs' own per-1350-innings table --
    conceptually the same idea, not numerically identical.

Baserunning (BsR) is wSB, computed here from the real FanGraphs formula
(SB/CS plus this season's Guts run values), plus bref's `runs_dp` (double
plays are a distinct, non-overlapping component). FanGraphs' own BsR also
includes UBR (credit for taking extra bases, tagging up, etc.), which needs
proprietary video-review data with no public source -- that piece is
omitted rather than approximated, since reusing bref's all-in-one
`runs_br` here would double-count the stolen-base value wSB already
captures and there's no way to cleanly subtract just that portion out.

wOBA itself is built here from bref counting stats and this season's Guts
linear weights (`guts.py`), rather than reused from Statcast's finished
per-batter wOBA: that covers every batter (Statcast's qualified-batter
minPA cutoff would drop most of the league) and keeps the wOBA numerator
and the wOBA scale on the same convention.
"""
import pandas as pd

from app import park_factors

COUNTING_COLS = ["PA", "AB", "R", "H", "2B", "3B", "HR", "BB", "IBB", "HBP", "SF", "SB", "CS"]


def _woba(df, g):
    singles = df["H"] - df["2B"] - df["3B"] - df["HR"]
    numerator = (
        g["wBB"] * (df["BB"] - df["IBB"])
        + g["wHBP"] * df["HBP"]
        + g["w1B"] * singles
        + g["w2B"] * df["2B"]
        + g["w3B"] * df["3B"]
        + g["wHR"] * df["HR"]
    )
    denominator = df["AB"] + df["BB"] - df["IBB"] + df["SF"] + df["HBP"]
    return numerator / denominator


def _wsb(df, g):
    """FanGraphs' weighted stolen base runs: SB/CS value above what a
    league-average baserunner would produce with the same on-base
    opportunities (times on 1B/2B via 1B/BB/HBP, minus IBB since a pitcher
    intentionally issuing one isn't a real "opportunity")."""
    opportunities = (df["H"] - df["2B"] - df["3B"] - df["HR"]) + df["BB"] + df["HBP"] - df["IBB"]
    lg_sb_runs = df["SB"].sum() * g["runSB"] + df["CS"].sum() * g["runCS"]
    lg_wsb_rate = lg_sb_runs / opportunities.sum()
    return df["SB"] * g["runSB"] + df["CS"] * g["runCS"] - lg_wsb_rate * opportunities


def _aggregate_bwar_bat(bwar_bat_df):
    """bwar_bat has one row per player-team-stint -- a player traded
    mid-season has multiple rows. Sum the run components across stints per
    player, and take the highest-PA stint's team/league as the primary one
    (used for the park factor lookup and league adjustment)."""
    df = bwar_bat_df.copy()
    df["mlb_ID"] = pd.to_numeric(df["mlb_ID"], errors="coerce")
    df["PA"] = pd.to_numeric(df["PA"], errors="coerce")
    for col in ("runs_dp", "runs_position", "runs_field"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    sums = df.groupby("mlb_ID")[["runs_dp", "runs_position", "runs_field"]].sum()
    primary_idx = df.groupby("mlb_ID")["PA"].idxmax()
    primary = df.loc[primary_idx, ["mlb_ID", "team_ID", "lg_ID"]].set_index("mlb_ID")
    return sums.join(primary).reset_index()


def compute(batting_df, bwar_bat_df, park_df, framing_df, oaa_df, guts_for_season):
    """batting_df: pb.batting_stats_bref() output.
    bwar_bat_df: pb.bwar_bat(return_all=True) output, pre-filtered to season.
    park_df: park_factors.from_bwar_pitch() output (team_ID -> PF).
    framing_df: DataFrame[mlbID, framing_runs] (Statcast catcher framing).
    oaa_df: DataFrame[mlbID, oaa_runs] (Statcast outs above average, summed
        across non-catcher positions).
    guts_for_season: guts.for_season(season) output.
    Returns batting_df with wOBA/wRAA/BsR/Fld/Pos/fWAR columns added.
    """
    g = guts_for_season
    # batting_stats_bref returns a 1-based index (same gotcha as
    # pitching_stats_bref); reset it so Series computed below align with the
    # post-merge frame instead of silently misaligning on combination.
    df = batting_df.reset_index(drop=True).copy()
    for col in COUNTING_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["mlbID"] = pd.to_numeric(df["mlbID"], errors="coerce")

    df["wOBA"] = _woba(df, g)
    wsb = _wsb(df, g)

    bwar = _aggregate_bwar_bat(bwar_bat_df)
    df = df.merge(bwar, left_on="mlbID", right_on="mlb_ID", how="left")
    # Missing bwar data (no match) becomes a neutral no-op, not a bias --
    # matches cwar.py's convention for missing contact-quality data.
    for col in ("runs_dp", "runs_position", "runs_field"):
        df[col] = df[col].fillna(0)

    df = df.merge(framing_df, on="mlbID", how="left")
    df = df.merge(oaa_df, on="mlbID", how="left")

    df["PF"] = park_factors.attach(df, park_df, team_col="team_ID")

    lg_r_per_pa = df["R"].sum() / df["PA"].sum()
    df["wRAA"] = ((df["wOBA"] - g["woba"]) / g["woba_scale"]) * df["PA"]
    # Park adjustment: a hitter-friendly park (PF > 1) inflates his raw wOBA,
    # so this term is negative there (and positive in a pitcher's park).
    park_adj = (1 - df["PF"]) * lg_r_per_pa * df["PA"]
    bat_runs = df["wRAA"] + park_adj

    df["BsR"] = wsb + df["runs_dp"]
    # Layered fielding value: catcher framing runs where available (only
    # catchers appear in framing_df), else Statcast OAA (only non-catcher
    # positions appear there), else bref's own runs_field as a last resort
    # for anyone Statcast doesn't cover (e.g. a pitcher's rare plate
    # appearance). NaN (no match), not 0, is the "missing" sentinel here so
    # each tier only fills in for players the previous tier didn't cover.
    df["Fld"] = df["framing_runs"].fillna(df["oaa_runs"]).fillna(df["runs_field"])
    df["Pos"] = df["runs_position"]

    # League adjustment: force each league's (bat+BsR+Fld+Pos) total to net
    # zero, redistributed proportional to PA, mirroring FanGraphs' league
    # correction. Batters bref couldn't match to a league fall into a single
    # "UNK" bucket rather than being dropped.
    component_sum = bat_runs + df["BsR"] + df["Fld"] + df["Pos"]
    lg_key = df["lg_ID"].fillna("UNK")
    lg_totals = pd.DataFrame({"lg": lg_key, "component": component_sum, "PA": df["PA"]}) \
        .groupby("lg").agg(total_component=("component", "sum"), total_pa=("PA", "sum"))
    per_pa_adj = (-lg_totals["total_component"] / lg_totals["total_pa"])
    lg_adj = lg_key.map(per_pa_adj) * df["PA"]

    total_league_pa = df["PA"].sum()
    replacement_rate = g["r_per_w"] * 570.0 / total_league_pa  # see guts.BATTER_REPLACEMENT_WIN_POOL
    rep_runs = replacement_rate * df["PA"]

    total_runs = bat_runs + df["BsR"] + df["Fld"] + df["Pos"] + lg_adj + rep_runs
    df["fWAR"] = total_runs / g["r_per_w"]

    df["wOBA"] = df["wOBA"].round(3)
    for col in ("wRAA", "BsR", "Fld", "Pos"):
        df[col] = df[col].round(1)
    df["fWAR"] = df["fWAR"].round(1)

    return df.drop(columns=["mlb_ID", "runs_dp", "runs_position", "runs_field",
                             "framing_runs", "oaa_runs", "lg_ID", "team_ID", "PF", "R"],
                    errors="ignore")
