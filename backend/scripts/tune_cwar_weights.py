"""Offline: empirically fit cWAR's rFIP/xERA blend weights.

cwar.FIP_WEIGHT_BASE / CONTACT_WEIGHT_BASE started as hand-picked,
research-informed guesses (70/25). This script fits them the standard way a
weighted predictor mix is fit: regress *next* season's park-adjusted ERA on
*this* season's rFIP and xERA, and see what mix predicts best out of sample.

Read-only and network-bound (it pulls several seasons through pybaseball's
disk cache). Nothing here is imported by the app; run it by hand, read the
tables, and decide whether to paste the fitted constants into cwar.py:

    cd backend && ./venv/bin/python -m scripts.tune_cwar_weights

Design notes (see the README's cWAR section for the full rationale):
  - Target is next-season ERA, park-adjusted, because both predictors are
    ERA-scale and the year-N rFIP input is itself park-adjusted. RA9 would
    add unearned-run and defense noise neither predictor claims to model;
    Savant's own wOBA would be circular (it shares xERA's event model).
  - Only full-reliability pitcher-seasons are fitted (IP >= 50, BIP >= 100),
    since these constants ARE the full-trust weights -- the runtime's
    reliability shrinkage interpolates below them and is a deliberate design
    choice, not something to re-fit here.
  - Predictors and target are centered within each season pair, so the fit
    can't win by guessing a season's run environment; only the relative
    ordering of pitchers informs the weights. That matches the runtime
    formula, which is intercept-free and gets centered by the WAR chassis.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pybaseball as pb  # noqa: E402

from app import cwar, park_factors  # noqa: E402
from app import pybaseball_client as pbc  # noqa: E402  (also enables pb.cache)

# 2020 is excluded entirely: a 60-game season is both an unstable predictor
# and a degenerate target. 2025->2026 is excluded from fitting because the
# 2026 season is still in progress; it's printed separately as an extra
# out-of-sample look.
SEASON_PAIRS = [(2021, 2022), (2022, 2023), (2023, 2024), (2024, 2025)]
IN_PROGRESS_PAIR = (2025, 2026)

MIN_IP_N = 50        # matches cwar.IP_FULL_TRUST
MIN_BIP_N = 100      # matches cwar.BIP_FULL_TRUST
MIN_IP_NEXT = 40     # keep one-blowup ERAs from dominating the target

BASELINES = {
    "FIP-only": 1.00,
    "xERA-only": 0.00,
    "50/50": 0.50,
}


def _current_fip_weight():
    """The runtime's normalized full-trust rFIP weight (the 0.70/0.25 pair
    expressed as a convex combination), so 'current' in the tables below is
    exactly what the app ships today."""
    total = cwar.FIP_WEIGHT_BASE + cwar.CONTACT_WEIGHT_BASE
    return cwar.FIP_WEIGHT_BASE / total


def build_features(season):
    """One row per pitcher-season with the same inputs the runtime blends:
    park-adjusted rFIP, raw xERA, batted-ball rates, and park-adjusted ERA
    (used as the *target* when this season is the N+1 side of a pair)."""
    # 1-based index from bref -> reset before any Series math (AGENTS.md).
    raw = pb.pitching_stats_bref(season).reset_index(drop=True).copy()
    for col in ("IP", "SO", "BB", "HBP", "HR", "ER", "BF"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw["mlbID"] = pd.to_numeric(raw["mlbID"], errors="coerce")

    if raw["mlbID"].duplicated().any():
        dupes = int(raw["mlbID"].duplicated().sum())
        print(f"  ! {season}: {dupes} duplicate mlbID rows in bref output; keeping first")
        raw = raw.drop_duplicates("mlbID")

    # Same rFIP the runtime blends -- shared helper, so this cannot drift.
    rfip_series, fip_series, _ = cwar.rfip(raw)
    gb_pct, fb_pct = cwar._batted_ball_rates(raw)

    bwar_pitch = pbc._bwar_pitch_for_season(season)
    park_df = park_factors.from_bwar_pitch(bwar_pitch)
    team_lookup = park_factors.primary_team(bwar_pitch)

    df = pd.DataFrame({
        "mlbID": raw["mlbID"],
        "IP": raw["IP"],
        "rFIP": rfip_series,
        "FIP": fip_series,
        "ERA": 9 * raw["ER"] / raw["IP"],
        "GB%": gb_pct,
        "FB%": fb_pct,
        "PU%": pd.to_numeric(raw.get("PU"), errors="coerce"),
    })
    df = df.merge(team_lookup, left_on="mlbID", right_on="mlb_ID", how="left")
    df["PF"] = park_factors.attach(df, park_df, team_col="team_ID")

    # Park-adjust the outcome-based rates; xERA (added below) stays raw,
    # exactly as cwar.compute treats them.
    df["rFIP_park"] = df["rFIP"] / df["PF"]
    df["ERA_park"] = df["ERA"] / df["PF"]

    exp = pb.statcast_pitcher_expected_stats(season, minPA=1).reset_index(drop=True)
    if "xera" not in exp.columns:
        raise SystemExit(
            f"{season}: Savant expected-stats leaderboard has no 'xera' column "
            f"(got {sorted(exp.columns)}). The leaderboard format likely changed."
        )
    exp = pd.DataFrame({
        "mlbID": pd.to_numeric(exp["player_id"], errors="coerce"),
        "xERA": pd.to_numeric(exp["xera"], errors="coerce"),
        "bip": pd.to_numeric(exp["bip"], errors="coerce"),
    })
    df = df.merge(exp, on="mlbID", how="left")

    coverage = df["xERA"].notna().mean()
    if coverage < 0.5:
        raise SystemExit(
            f"{season}: only {coverage:.0%} of pitchers matched an xERA row -- "
            "expected >50%. Check the Savant join key or a stale cache."
        )
    return df.drop(columns=["mlb_ID", "team_ID"], errors="ignore")


def build_pair(features, year_n, year_next):
    """Join season N's predictors to season N+1's park-adjusted ERA."""
    a = features[year_n]
    b = features[year_next][["mlbID", "IP", "ERA_park"]].rename(
        columns={"IP": "IP_next", "ERA_park": "target"}
    )
    df = a.merge(b, on="mlbID", how="inner")

    df = df[(df["IP"] >= MIN_IP_N) & (df["bip"] >= MIN_BIP_N) & (df["IP_next"] >= MIN_IP_NEXT)]
    # Drop rather than fill missing xERA: the runtime's fillna(rFIP) fallback
    # is right for scoring a player but would contaminate the regression by
    # smuggling FIP values into the xERA column.
    df = df.dropna(subset=["rFIP_park", "xERA", "target"])

    # Harmonic mean of the two IP totals: a pitcher has to be well-measured
    # on BOTH sides of the pair to say much about year-to-year skill.
    df = df.copy()
    df["w"] = 2 * df["IP"] * df["IP_next"] / (df["IP"] + df["IP_next"])
    return df.reset_index(drop=True)


def _center(df):
    """Weighted-center target and predictors within one season pair."""
    w = df["w"].to_numpy()
    out = {}
    for col in ("rFIP_park", "xERA", "target", "GB%", "FB%", "PU%"):
        v = df[col].to_numpy(dtype=float)
        out[col] = v - np.average(v, weights=w)
    out["w"] = w
    return out


def fit_weights(centered_pairs):
    """Weighted, no-intercept, nonnegative least squares of next-season ERA
    on (rFIP_park, xERA), then normalized to sum to 1.

    Returns (fip_weight, xera_weight, raw_coefficient_sum). The raw sum runs
    well below 1 because every pitcher regresses toward the mean year over
    year -- that's a global shrinkage the WAR chassis handles by centering on
    the league average, not something the blend's mix should absorb, so only
    the *ratio* between the two coefficients is carried over.
    """
    a = np.vstack([np.concatenate([c["rFIP_park"] for c in centered_pairs]),
                   np.concatenate([c["xERA"] for c in centered_pairs])]).T
    y = np.concatenate([c["target"] for c in centered_pairs])
    w = np.concatenate([c["w"] for c in centered_pairs])

    sw = np.sqrt(w)
    # Nonnegative bounds guard against a small-sample negative coefficient
    # normalizing into a nonsensical >1 / negative weight pair.
    res = lsq_linear(a * sw[:, None], y * sw, bounds=(0, np.inf))
    b_fip, b_xera = res.x
    total = b_fip + b_xera
    if total <= 0:
        raise SystemExit("Degenerate fit: both coefficients are zero.")
    return b_fip / total, b_xera / total, total


def _wrmse_wmae(centered, fip_w):
    pred = fip_w * centered["rFIP_park"] + (1 - fip_w) * centered["xERA"]
    err = pred - centered["target"]
    w = centered["w"]
    rmse = float(np.sqrt(np.average(err ** 2, weights=w)))
    mae = float(np.average(np.abs(err), weights=w))
    return rmse, mae


def loo_eval(centered_pairs, labels, fitted_all):
    """Leave-one-season-pair-out: fit on the other pairs, score the held-out
    one. With only four pairs, a mix that wins pooled but loses on individual
    folds is overfitting the pooled sample."""
    current = _current_fip_weight()
    rows = []
    fold_weights = []

    for i, label in enumerate(labels):
        train = [c for j, c in enumerate(centered_pairs) if j != i]
        held = centered_pairs[i]
        fold_fip_w, _, _ = fit_weights(train)
        fold_weights.append(fold_fip_w)

        settings = {"fitted (LOO)": fold_fip_w, "current": current, **BASELINES}
        row = {"pair": label, "n": len(held["w"])}
        for name, w in settings.items():
            rmse, mae = _wrmse_wmae(held, w)
            row[f"{name} RMSE"] = rmse
            row[f"{name} MAE"] = mae
        rows.append(row)

    table = pd.DataFrame(rows).set_index("pair")

    print("\n" + "=" * 78)
    print("LEAVE-ONE-SEASON-PAIR-OUT (weighted RMSE of predicted next-season ERA)")
    print("=" * 78)
    rmse_cols = [c for c in table.columns if c.endswith("RMSE")]
    print(table[["n"] + rmse_cols].to_string(
        float_format=lambda v: f"{v:.4f}", header=[c.replace(" RMSE", "") for c in ["n"] + rmse_cols]
    ))

    print("\nWeighted MAE:")
    mae_cols = [c for c in table.columns if c.endswith("MAE")]
    print(table[mae_cols].to_string(
        float_format=lambda v: f"{v:.4f}", header=[c.replace(" MAE", "") for c in mae_cols]
    ))

    print(f"\nPer-fold fitted rFIP weight: "
          f"{', '.join(f'{lbl}={w:.3f}' for lbl, w in zip(labels, fold_weights))}")
    spread = max(fold_weights) - min(fold_weights)
    print(f"  fold spread: {spread:.3f}  (mean {np.mean(fold_weights):.3f}, "
          f"all-pairs fit {fitted_all:.3f})")
    if spread > 0.20:
        print("  ! Wide spread across folds -- the point estimate is unstable.")
        print("    Prefer the fold mean, or keep the current weights.")

    wins = sum(1 for r in rows if r["fitted (LOO) RMSE"] < r["current RMSE"])
    print(f"\nFolds where fitted beats current: {wins}/{len(rows)}")
    return wins, len(rows), fold_weights


def batted_ball_residual_check(centered_pairs, fip_w):
    """Diagnostic only -- deliberately NOT co-fit with the blend weights.

    xERA is built from exit velocity AND launch angle, so batted-ball mix is
    already largely inside it; and GB/FB/PU are structurally collinear (they
    sum to 1 with LD, and PU barely varies). Throwing them into the main
    regression would split shared signal arbitrarily between the terms.
    Instead: does the blend leave any batted-ball-shaped residual behind?
    """
    x = np.vstack([np.concatenate([c[k] for c in centered_pairs])
                   for k in ("GB%", "FB%", "PU%")]).T
    resid = np.concatenate([
        c["target"] - (fip_w * c["rFIP_park"] + (1 - fip_w) * c["xERA"])
        for c in centered_pairs
    ])
    w = np.concatenate([c["w"] for c in centered_pairs])

    ok = np.isfinite(x).all(axis=1) & np.isfinite(resid)
    x, resid, w = x[ok], resid[ok], w[ok]

    sw = np.sqrt(w)
    xw, yw = x * sw[:, None], resid * sw
    coefs, *_ = np.linalg.lstsq(xw, yw, rcond=None)

    # Standard errors from the weighted normal equations.
    dof = max(len(yw) - x.shape[1], 1)
    sigma2 = float(((yw - xw @ coefs) ** 2).sum() / dof)
    try:
        se = np.sqrt(np.diag(sigma2 * np.linalg.inv(xw.T @ xw)))
    except np.linalg.LinAlgError:
        se = np.full(3, np.nan)

    # Runtime signs: GB and PU are credits (negative runs), FB is a penalty.
    current = {"GB%": -cwar.GB_RUN_COEF, "FB%": cwar.FB_RUN_COEF, "PU%": -cwar.PU_RUN_COEF}
    print("\n" + "=" * 78)
    print("BATTED-BALL RESIDUAL CHECK (diagnostic, not auto-applied)")
    print("=" * 78)
    print(f"{'term':<8}{'fitted':>10}{'std err':>10}{'t':>8}   current (runtime)")
    for i, key in enumerate(("GB%", "FB%", "PU%")):
        t = coefs[i] / se[i] if se[i] and np.isfinite(se[i]) else float("nan")
        print(f"{key:<8}{coefs[i]:>10.3f}{se[i]:>10.3f}{t:>8.2f}   {current[key]:+.2f}")
    print("\nReading this: a fitted coefficient near zero (|t| < 2) means the blend")
    print("already captures that batted-ball signal -- mostly via xERA -- and the")
    print("runtime adjustment is double-counting, so it should shrink or go. Same")
    print("sign and rough magnitude as 'current' means the hand-picked value holds up.")


def batted_ball_variant_check(pairs, labels, fip_w):
    """The decisive version of the diagnostic above: rebuild the runtime's
    actual batted-ball adjustment (IP-reliability-scaled, real coefficients)
    and score the blend with it, without it, and at half strength.

    This is what drove the runtime's complementary weighting -- the
    adjustment is now multiplied by (1 - bip_reliability), so it fades out as
    xERA takes over. Note the sample here is all bip >= 100, i.e. exactly
    the full-xERA-trust regime where double-counting is maximal; it says
    nothing about pitchers with little tracked data, which is precisely the
    regime the runtime keeps the adjustment for.
    """
    print("\n" + "=" * 78)
    print("BATTED-BALL ADJUSTMENT, AS ACTUALLY APPLIED (weighted RMSE)")
    print("=" * 78)
    print(f"{'pair':<14}{'n':>5}{'full adj':>11}{'half adj':>11}{'no adj':>11}")

    totals = {"full": [], "half": [], "none": []}
    for df, label in zip(pairs, labels):
        w = df["w"].to_numpy()
        lg = {k: np.average(df[k].fillna(df[k].mean()), weights=df["IP"])
              for k in ("GB%", "FB%", "PU%")}
        adj = (-cwar.GB_RUN_COEF * (df["GB%"].fillna(lg["GB%"]) - lg["GB%"])
               + cwar.FB_RUN_COEF * (df["FB%"].fillna(lg["FB%"]) - lg["FB%"])
               - cwar.PU_RUN_COEF * (df["PU%"].fillna(lg["PU%"]) - lg["PU%"]))
        adj = (adj * (df["IP"] / cwar.IP_FULL_TRUST).clip(upper=1).fillna(0)).to_numpy()

        base = fip_w * df["rFIP_park"].to_numpy() + (1 - fip_w) * df["xERA"].to_numpy()
        y = df["target"].to_numpy()
        y_c = y - np.average(y, weights=w)

        scores = {}
        for name, blend in (("full", base + adj), ("half", base + 0.5 * adj), ("none", base)):
            err = (blend - np.average(blend, weights=w)) - y_c
            scores[name] = float(np.sqrt(np.average(err ** 2, weights=w)))
            totals[name].append(scores[name])
        print(f"{label:<14}{len(df):>5}{scores['full']:>11.4f}"
              f"{scores['half']:>11.4f}{scores['none']:>11.4f}")

    print(f"\n{'MEAN':<14}{'':>5}{np.mean(totals['full']):>11.4f}"
          f"{np.mean(totals['half']):>11.4f}{np.mean(totals['none']):>11.4f}")
    helps = sum(1 for f, n in zip(totals["full"], totals["none"]) if f < n)
    print(f"\nFolds where the full adjustment beats none: {helps}/{len(labels)}")
    if helps == 0:
        print("  -> Double-counting confirmed in the full-xERA-trust regime. The runtime")
        print("     scales this adjustment by (1 - bip_reliability) so it only applies")
        print("     where xERA is absent or weakly trusted.")


def main():
    seasons = sorted({s for pair in SEASON_PAIRS + [IN_PROGRESS_PAIR] for s in pair})
    print(f"Fetching {len(seasons)} seasons (first run is slow; later runs hit the disk cache)...")
    features = {}
    for season in seasons:
        print(f"  {season}...", flush=True)
        features[season] = build_features(season)

    pairs, labels = [], []
    for n, nxt in SEASON_PAIRS:
        df = build_pair(features, n, nxt)
        pairs.append(df)
        labels.append(f"{n}->{nxt}")
        print(f"  {n}->{nxt}: {len(df)} qualified pitcher-seasons")

    centered = [_center(df) for df in pairs]

    fip_w, xera_w, raw_total = fit_weights(centered)
    print("\n" + "=" * 78)
    print("FITTED WEIGHTS (all pairs pooled, weighted NNLS, normalized to sum 1)")
    print("=" * 78)
    print(f"  rFIP  {fip_w:.3f}")
    print(f"  xERA  {xera_w:.3f}")
    print(f"  (raw coefficient sum before normalizing: {raw_total:.3f} -- well under 1 is")
    print("   expected and fine; it's year-to-year regression to the mean, which the WAR")
    print("   chassis handles by centering on the league average.)")
    print(f"  current runtime weights: rFIP {_current_fip_weight():.3f} / "
          f"xERA {1 - _current_fip_weight():.3f}")

    wins, folds, fold_weights = loo_eval(centered, labels, fip_w)
    batted_ball_residual_check(centered, fip_w)
    batted_ball_variant_check(pairs, labels, _current_fip_weight())

    # Extra, clearly-labeled look at the in-progress season. Not fitted on.
    n, nxt = IN_PROGRESS_PAIR
    partial = build_pair(features, n, nxt)
    if len(partial):
        c = _center(partial)
        print("\n" + "=" * 78)
        print(f"EXTRA OUT-OF-SAMPLE CHECK: {n}->{nxt} (season IN PROGRESS -- not fitted on)")
        print("=" * 78)
        print(f"  n = {len(partial)}")
        for name, w in {"fitted": fip_w, "current": _current_fip_weight(), **BASELINES}.items():
            rmse, mae = _wrmse_wmae(c, w)
            print(f"    {name:<12} RMSE {rmse:.4f}   MAE {mae:.4f}")

    print("\n" + "=" * 78)
    print("DECISION")
    print("=" * 78)
    consistent = wins == folds
    if consistent:
        print(f"Fitted weights beat current on {wins}/{folds} folds -- consistent. Paste into")
        print("app/cwar.py (already normalized, so the runtime's normalization is a no-op):")
        print()
        print(f"    FIP_WEIGHT_BASE = {fip_w:.2f}")
        print(f"    CONTACT_WEIGHT_BASE = {xera_w:.2f}")
        print(f"    # Fitted against next-season park-adjusted ERA over "
              f"{labels[0].split('->')[0]}-{labels[-1].split('->')[1]} season pairs")
        print(f"    # (scripts/tune_cwar_weights.py). Beat the prior hand-picked mix on "
              f"{wins}/{folds} LOO folds.")
    else:
        print(f"Fitted weights beat current on only {wins}/{folds} folds -- NOT consistent.")
        print("Keep the current weights and record that they were validated, e.g.:")
        print()
        print("    # Validated against next-season park-adjusted ERA "
              f"({labels[0].split('->')[0]}-{labels[-1].split('->')[1]} pairs,")
        print(f"    # scripts/tune_cwar_weights.py): an empirical refit won only {wins}/{folds}")
        print("    # leave-one-season-out folds, so these hand-picked values stand.")


if __name__ == "__main__":
    main()
