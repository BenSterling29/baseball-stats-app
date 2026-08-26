"""Shared WAR-conversion chassis for the pitcher WAR metrics.

All three pitcher metrics (bWAR, fWAR, cWAR) differ in the *rate stat* they
believe -- actual runs allowed (RA9), FIP, or the FIP/xERA blend -- but
convert that rate to wins the same way, following FanGraphs' published
pitcher-WAR structure (https://library.fangraphs.com/war/calculating-war-pitchers/):

  1. Park-adjust the rate (divide by the team park factor).
  2. Compare against the IP-weighted league average of the same rate.
  3. Dynamic runs-per-win: a pitcher's own innings/game and park-adjusted
     run environment shift how many runs one win is worth for him, since
     (unlike a hitter) a pitcher directly shapes the game's run environment.
  4. Starter/reliever replacement-level split (.12/.03 wins per 9 IP),
     blended by each pitcher's GS/G share.

Keeping this in one place guarantees the three metrics stay directly
comparable on the WAR Compare tab: any spread between them reflects the
rate-stat methodology, never a mismatched replacement level or park
convention.
"""

REPLACEMENT_WPG_RELIEVER = 0.03  # FanGraphs: gap between avg (.500) and replacement (.470) win% for RP
REPLACEMENT_WPG_STARTER = 0.12   # FanGraphs: gap between avg (.500) and replacement (.380) win% for SP


def war_from_rate(df, rate_r9, league_rate_r9, pf):
    """Convert a runs-allowed-per-9 rate into WAR.

    df: pitching frame with numeric IP/G/GS columns.
    rate_r9: per-pitcher Series, the metric's believed runs-allowed rate on
        an RA9 (not ERA) basis, un-park-adjusted.
    league_rate_r9: scalar, the IP-weighted league average of rate_r9
        (weight by IP, not a plain mean -- see the AGENTS.md gotcha).
    pf: per-pitcher park factor Series (~1.0 = neutral).
    Returns the WAR Series (unrounded).
    """
    park_rate_r9 = rate_r9 / pf
    raa_p9 = league_rate_r9 - park_rate_r9

    ip_per_g = df["IP"] / df["G"]
    dynamic_rpw = (((18 - ip_per_g) * league_rate_r9 + ip_per_g * park_rate_r9) / 18 + 2) * 1.5

    gs_share = (df["GS"] / df["G"]).clip(0, 1).fillna(0)
    replacement_wpg = REPLACEMENT_WPG_RELIEVER * (1 - gs_share) + REPLACEMENT_WPG_STARTER * gs_share

    return (raa_p9 / dynamic_rpw + replacement_wpg) * df["IP"] / 9
