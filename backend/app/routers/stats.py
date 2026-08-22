from fastapi import APIRouter, Query

from app import pybaseball_client as pbc

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/standings")
def standings(season: int | None = None):
    return pbc.get_standings(season)


@router.get("/stats/batting")
def batting_stats(season: int | None = None):
    return pbc.get_batting_stats(season)


@router.get("/stats/pitching")
def pitching_stats(season: int | None = None):
    return pbc.get_pitching_stats(season)


@router.get("/stats/pitching/cwar")
def pitching_cwar(season: int | None = None):
    return pbc.get_pitcher_cwar(season)


@router.get("/players/search")
def player_search(last: str = Query(...), first: str | None = None):
    return pbc.search_player(last, first)


@router.get("/statcast/recent")
def recent_statcast(days_back: int = 3):
    return pbc.get_recent_statcast(days_back)


@router.get("/savant/batting/exitvelo")
def batter_exitvelo_barrels(season: int | None = None, min_bbe: int = 50):
    return pbc.get_batter_exitvelo_barrels(season, min_bbe)


@router.get("/savant/pitching/exitvelo")
def pitcher_exitvelo_barrels(season: int | None = None, min_bbe: int = 50):
    return pbc.get_pitcher_exitvelo_barrels(season, min_bbe)


@router.get("/savant/batting/expected")
def batter_expected_stats(season: int | None = None, min_pa: int = 50):
    return pbc.get_batter_expected_stats(season, min_pa)


@router.get("/savant/pitching/expected")
def pitcher_expected_stats(season: int | None = None, min_pa: int = 50):
    return pbc.get_pitcher_expected_stats(season, min_pa)
