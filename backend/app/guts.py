"""FanGraphs "Guts!" constants: wOBA linear weights and league run
environment, by season. Published at fangraphs.com/guts.aspx?type=cn --
pybaseball has no API for these, so they're hand-transcribed here. Add a new
season's row once FanGraphs publishes it (they update in-season, not just
after it ends).

Sourced 2026-08-22 from https://www.fangraphs.com/guts.aspx?type=cn.
"""

GUTS = {
    2026: {"woba": 0.316, "woba_scale": 1.239, "wBB": 0.698, "wHBP": 0.729,
           "w1B": 0.890, "w2B": 1.261, "w3B": 1.596, "wHR": 2.050,
           "runSB": 0.200, "runCS": -0.411, "r_per_pa": 0.118,
           "r_per_w": 9.809, "cFIP": 3.085},
    2025: {"woba": 0.313, "woba_scale": 1.232, "wBB": 0.691, "wHBP": 0.722,
           "w1B": 0.882, "w2B": 1.252, "w3B": 1.584, "wHR": 2.037,
           "runSB": 0.200, "runCS": -0.410, "r_per_pa": 0.118,
           "r_per_w": 9.774, "cFIP": 3.135},
    2024: {"woba": 0.310, "woba_scale": 1.242, "wBB": 0.689, "wHBP": 0.720,
           "w1B": 0.882, "w2B": 1.254, "w3B": 1.590, "wHR": 2.050,
           "runSB": 0.200, "runCS": -0.405, "r_per_pa": 0.117,
           "r_per_w": 9.683, "cFIP": 3.166},
    2023: {"woba": 0.318, "woba_scale": 1.204, "wBB": 0.696, "wHBP": 0.726,
           "w1B": 0.883, "w2B": 1.244, "w3B": 1.569, "wHR": 2.004,
           "runSB": 0.200, "runCS": -0.422, "r_per_pa": 0.122,
           "r_per_w": 10.028, "cFIP": 3.255},
    2022: {"woba": 0.310, "woba_scale": 1.259, "wBB": 0.689, "wHBP": 0.720,
           "w1B": 0.884, "w2B": 1.261, "w3B": 1.601, "wHR": 2.072,
           "runSB": 0.200, "runCS": -0.397, "r_per_pa": 0.114,
           "r_per_w": 9.524, "cFIP": 3.112},
    2021: {"woba": 0.314, "woba_scale": 1.209, "wBB": 0.692, "wHBP": 0.722,
           "w1B": 0.879, "w2B": 1.242, "w3B": 1.568, "wHR": 2.007,
           "runSB": 0.200, "runCS": -0.419, "r_per_pa": 0.121,
           "r_per_w": 9.973, "cFIP": 3.170},
    2020: {"woba": 0.320, "woba_scale": 1.185, "wBB": 0.699, "wHBP": 0.728,
           "w1B": 0.883, "w2B": 1.238, "w3B": 1.558, "wHR": 1.979,
           "runSB": 0.200, "runCS": -0.435, "r_per_pa": 0.125,
           "r_per_w": 10.282, "cFIP": 3.191},
    2019: {"woba": 0.320, "woba_scale": 1.157, "wBB": 0.690, "wHBP": 0.719,
           "w1B": 0.870, "w2B": 1.217, "w3B": 1.529, "wHR": 1.940,
           "runSB": 0.200, "runCS": -0.435, "r_per_pa": 0.126,
           "r_per_w": 10.296, "cFIP": 3.214},
    2018: {"woba": 0.315, "woba_scale": 1.226, "wBB": 0.690, "wHBP": 0.720,
           "w1B": 0.880, "w2B": 1.247, "w3B": 1.578, "wHR": 2.031,
           "runSB": 0.200, "runCS": -0.407, "r_per_pa": 0.117,
           "r_per_w": 9.714, "cFIP": 3.160},
    2017: {"woba": 0.321, "woba_scale": 1.185, "wBB": 0.693, "wHBP": 0.723,
           "w1B": 0.877, "w2B": 1.232, "w3B": 1.552, "wHR": 1.980,
           "runSB": 0.200, "runCS": -0.423, "r_per_pa": 0.122,
           "r_per_w": 10.048, "cFIP": 3.158},
    2016: {"woba": 0.318, "woba_scale": 1.212, "wBB": 0.691, "wHBP": 0.721,
           "w1B": 0.878, "w2B": 1.242, "w3B": 1.569, "wHR": 2.015,
           "runSB": 0.200, "runCS": -0.410, "r_per_pa": 0.118,
           "r_per_w": 9.778, "cFIP": 3.147},
    2015: {"woba": 0.313, "woba_scale": 1.251, "wBB": 0.687, "wHBP": 0.718,
           "w1B": 0.881, "w2B": 1.256, "w3B": 1.594, "wHR": 2.065,
           "runSB": 0.200, "runCS": -0.392, "r_per_pa": 0.112,
           "r_per_w": 9.421, "cFIP": 3.134},
}

# FanGraphs allocates a ~1000-win replacement-level pool per season, split
# 57%/43% between position players and pitchers; 570 is the position-player
# share. fwar_batting.py turns this into a runs-per-PA rate using the
# season's actual league PA and runs-per-win, rather than a fixed runs/PA
# table, so it stays consistent with that season's run environment.
BATTER_REPLACEMENT_WIN_POOL = 570.0

_SEASONS = sorted(GUTS)


def for_season(season):
    """Look up this season's Guts constants. Falls back to the nearest
    published season for years outside the table (these constants move
    slowly year to year, so last year's are a reasonable stand-in) and
    flags the result as estimated so callers can surface that."""
    if season in GUTS:
        return {**GUTS[season], "estimated": False}
    nearest = min(_SEASONS, key=lambda y: abs(y - season))
    return {**GUTS[nearest], "estimated": True}
