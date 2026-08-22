"""Thin wrapper around pybaseball with on-disk caching enabled.

pybaseball pulls from Baseball Savant / FanGraphs / Baseball Reference, which
are slow to scrape repeatedly. Its built-in cache keeps identical calls fast
across requests during local development.
"""
import io
import json
import re
from datetime import date, timedelta

import pandas as pd
import pybaseball as pb
import requests
from pybaseball import cache as pb_cache

from app import cwar, fwar_batting, fwar_pitching, guts, park_factors

pb.cache.enable()

_FRAMING_URL = (
    "https://baseballsavant.mlb.com/leaderboard/catcher-framing"
    "?type=catcher&seasonStart={season}&seasonEnd={season}&team=&min=1"
    "&sortColumn=rv_tot&sortDirection=desc&csv=true"
)

_BYTE_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})")


def _fix_mojibake(value):
    """bref scraping sometimes yields names like 'Acu\\xc3\\xb1a' (literal
    backslash-x escapes, not real UTF-8) for accented players. Decode those
    back to 'Acuña'."""
    if not isinstance(value, str) or "\\x" not in value:
        return value
    try:
        raw = _BYTE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), value)
        return raw.encode("latin1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def _records(df):
    """DataFrame -> list of dicts. Goes through df.to_json so NaN becomes JSON
    null instead of raw float('nan'), which the JSON encoder would reject."""
    records = json.loads(df.reset_index(drop=True).to_json(orient="records"))
    return [{k: _fix_mojibake(v) for k, v in row.items()} for row in records]


def get_standings(season: int | None = None):
    season = season or date.today().year
    divisions = pb.standings(season)
    return [_records(df) for df in divisions]


def get_batting_stats(season: int | None = None):
    """Baseball-Reference batting stats. FanGraphs' scrape (pb.batting_stats)
    is blocked by their bot protection (403), so we use bref instead."""
    season = season or date.today().year
    df = pb.batting_stats_bref(season)
    return _records(df)


def get_pitching_stats(season: int | None = None):
    season = season or date.today().year
    df = pb.pitching_stats_bref(season)
    return _records(df)


def search_player(last: str, first: str | None = None):
    df = pb.playerid_lookup(last, first) if first else pb.playerid_lookup(last)
    return _records(df)


def get_recent_statcast(days_back: int = 3):
    end = date.today()
    start = end - timedelta(days=days_back)
    df = pb.statcast(start_dt=start.isoformat(), end_dt=end.isoformat())
    return _records(df)


def get_batter_exitvelo_barrels(season: int | None = None, min_bbe: int = 50):
    season = season or date.today().year
    df = pb.statcast_batter_exitvelo_barrels(season, minBBE=min_bbe)
    return _records(df)


def get_pitcher_exitvelo_barrels(season: int | None = None, min_bbe: int = 50):
    season = season or date.today().year
    df = pb.statcast_pitcher_exitvelo_barrels(season, minBBE=min_bbe)
    return _records(df)


def get_batter_expected_stats(season: int | None = None, min_pa: int = 50):
    season = season or date.today().year
    df = pb.statcast_batter_expected_stats(season, minPA=min_pa)
    return _records(df)


def get_pitcher_expected_stats(season: int | None = None, min_pa: int = 50):
    season = season or date.today().year
    df = pb.statcast_pitcher_expected_stats(season, minPA=min_pa)
    return _records(df)


def get_pitcher_cwar(season: int | None = None):
    """FIP core blended with Statcast contact quality (xERA) and a
    batted-ball-mix adjustment. See app/cwar.py for the full formula."""
    season = season or date.today().year
    pitching_df = pb.pitching_stats_bref(season)
    # min PA of 1 (rather than this app's usual 50 default) so as many
    # pitchers as possible get a real contact-quality read instead of
    # falling back to FIP-only.
    expected_df = pb.statcast_pitcher_expected_stats(season, minPA=1)
    df = cwar.compute(pitching_df, expected_df)
    return _records(df)


@pb_cache.df_cache()
def _get_catcher_framing(season: int):
    """pybaseball's own statcast_catcher_framing() hits a catcher_framing URL
    Baseball Savant has since retired in favor of this leaderboard path;
    fetch it directly instead. Returns DataFrame[mlbID, framing_runs]."""
    res = requests.get(_FRAMING_URL.format(season=season), timeout=30)
    res.raise_for_status()
    raw = pd.read_csv(io.StringIO(res.text))
    return pd.DataFrame({
        "mlbID": pd.to_numeric(raw["id"], errors="coerce"),
        "framing_runs": pd.to_numeric(raw["rv_tot"], errors="coerce"),
    })


def _get_fielding_oaa(season: int):
    """Sum Statcast Outs Above Average's fielding_runs_prevented across every
    non-catcher defensive position (1B-RF; catchers aren't covered by this
    leaderboard) for each player -- a multi-position player accrues value at
    each spot he plays, the same idea as bref's own runs_field."""
    frames = []
    for pos in (3, 4, 5, 6, 7, 8, 9):
        pos_df = pb.statcast_outs_above_average(season, pos, min_att=1)
        frames.append(pos_df[["player_id", "fielding_runs_prevented"]])
    all_pos = pd.concat(frames, ignore_index=True)
    all_pos["player_id"] = pd.to_numeric(all_pos["player_id"], errors="coerce")
    grouped = all_pos.groupby("player_id", as_index=False)["fielding_runs_prevented"].sum()
    return grouped.rename(columns={"player_id": "mlbID", "fielding_runs_prevented": "oaa_runs"})


def get_batter_fwar(season: int | None = None):
    """Real fWAR methodology (see app/fwar_batting.py for the formula and
    its disclosed approximations), unlike cWAR above which is a distinct,
    xERA-blended metric."""
    season = season or date.today().year
    batting_df = pb.batting_stats_bref(season)
    bwar_bat_df = pb.bwar_bat(return_all=True)
    bwar_bat_df = bwar_bat_df[bwar_bat_df["year_ID"] == season]
    bwar_pitch_df = pb.bwar_pitch(return_all=True)
    bwar_pitch_df = bwar_pitch_df[bwar_pitch_df["year_ID"] == season]
    park_df = park_factors.from_bwar_pitch(bwar_pitch_df)
    framing_df = _get_catcher_framing(season)
    oaa_df = _get_fielding_oaa(season)
    df = fwar_batting.compute(batting_df, bwar_bat_df, park_df, framing_df, oaa_df, guts.for_season(season))
    return _records(df)


def get_pitcher_fwar(season: int | None = None):
    """Real fWAR methodology (see app/fwar_pitching.py for the formula and
    its disclosed approximations), unlike cWAR above which is a distinct,
    xERA-blended metric."""
    season = season or date.today().year
    pitching_df = pb.pitching_stats_bref(season)
    bwar_pitch_df = pb.bwar_pitch(return_all=True)
    bwar_pitch_df = bwar_pitch_df[bwar_pitch_df["year_ID"] == season]
    park_df = park_factors.from_bwar_pitch(bwar_pitch_df)
    df = fwar_pitching.compute(pitching_df, bwar_pitch_df, park_df, guts.for_season(season))
    return _records(df)
