from fastapi import APIRouter, Query

from app import pybaseball_client as pbc

router = APIRouter(prefix="/api", tags=["stats"])

# pybaseball scrapes fail slowly and confusingly on nonsense seasons (0, a
# half-typed year, a far-future date), so reject those at the HTTP layer
# instead of letting a bad scrape surface as an opaque 500.
SeasonParam = Query(None, ge=1871, le=2100, description="MLB season year")


@router.get("/standings")
def standings(season: int | None = SeasonParam):
    return pbc.get_standings(season)


@router.get("/stats/batting")
def batting_stats(season: int | None = SeasonParam):
    return pbc.get_batting_stats(season)


@router.get("/stats/pitching")
def pitching_stats(season: int | None = SeasonParam):
    return pbc.get_pitching_stats(season)


@router.get("/stats/pitching/cwar")
def pitching_cwar(season: int | None = SeasonParam):
    return pbc.get_pitcher_cwar(season)


@router.get("/stats/batting/fwar")
def batting_fwar(season: int | None = SeasonParam):
    return pbc.get_batter_fwar(season)


@router.get("/stats/pitching/fwar")
def pitching_fwar(season: int | None = SeasonParam):
    return pbc.get_pitcher_fwar(season)


@router.get("/stats/pitching/bwar")
def pitching_bwar(season: int | None = SeasonParam):
    return pbc.get_pitcher_bwar(season)


@router.get("/stats/pitching/war-compare")
def pitching_war_compare(season: int | None = SeasonParam):
    return pbc.get_pitcher_war_compare(season)


@router.get("/players/search")
def player_search(last: str = Query(..., min_length=1), first: str | None = None):
    return pbc.search_player(last, first)


@router.get("/statcast/recent")
def recent_statcast(days_back: int = Query(3, ge=1, le=30)):
    # Capped: pb.statcast fetches pitch-level rows per day; an unbounded
    # days_back would try to pull months of full-league Statcast data in one
    # request.
    return pbc.get_recent_statcast(days_back)


@router.get("/savant/batting/exitvelo")
def batter_exitvelo_barrels(season: int | None = SeasonParam, min_bbe: int = Query(50, ge=0)):
    return pbc.get_batter_exitvelo_barrels(season, min_bbe)


@router.get("/savant/pitching/exitvelo")
def pitcher_exitvelo_barrels(season: int | None = SeasonParam, min_bbe: int = Query(50, ge=0)):
    return pbc.get_pitcher_exitvelo_barrels(season, min_bbe)


@router.get("/savant/batting/expected")
def batter_expected_stats(season: int | None = SeasonParam, min_pa: int = Query(50, ge=0)):
    return pbc.get_batter_expected_stats(season, min_pa)


@router.get("/savant/pitching/expected")
def pitcher_expected_stats(season: int | None = SeasonParam, min_pa: int = Query(50, ge=0)):
    return pbc.get_pitcher_expected_stats(season, min_pa)
