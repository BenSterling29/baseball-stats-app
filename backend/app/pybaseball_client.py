"""Thin wrapper around pybaseball with on-disk caching enabled.

pybaseball pulls from Baseball Savant / FanGraphs / Baseball Reference, which
are slow to scrape repeatedly. Its built-in cache keeps identical calls fast
across requests during local development.
"""
import json
import re
from datetime import date, timedelta

import pybaseball as pb

pb.cache.enable()

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
