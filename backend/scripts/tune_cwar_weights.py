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
    park-adjusted rFIP, raw xERA, the batted-ball adjustment, and
    park-adjusted ERA (the *target* when this season is the N+1 side)."""
    # 1-based index from bref -> reset before any Series math (AGENTS.md).
    raw = pb.pitching_stats_bref(season).reset_index(drop=True).copy()
    for col in ("IP", "SO", "BB", "HBP", "HR", "ER", "BF"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw["mlbID"] = pd.to_numeric(raw["mlbID"], errors="coerce")

    if raw["mlbID"].duplicated().any():
        dupes = int(raw["mlbID"].duplicated().sum())
        print(f"  ! {season}: {dupes} duplicate mlbID rows in bref output; keeping first")
        raw = raw.drop_duplicates("mlbID").reset_index(drop=True)

    # Same rFIP the runtime blends -- shared helper, so this cannot drift.
    rfip_series, fip_series, _ = cwar.rfip(raw)
    gb_pct, fb_pct = cwar._batted_ball_rates(raw)

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
    # Same batted-ball adjustment the runtime applies (shared helper), and
    # computed here, on the full league, so its league averages match the
    # runtime's -- not on the filtered sample built later.
    df["bb_adj"] = cwar.batted_ball_adjustment(df)

    bwar_pitch = pbc._bwar_pitch_for_season(season)
    park_df = park_factors.from_bwar_pitch(bwar_pitch)
    team_lookup = park_factors.primary_team(bwar_pitch)
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
    df = df.dropna(subset=["rFIP_park", "xERA", "target", "bb_adj"])

    # Harmonic mean of the two IP totals: a pitcher has to be well-measured
    # on BOTH sides of the pair to say much about year-to-year skill.
    df = df.copy()
    df["w"] = 2 * df["IP"] * df["IP_next"] / (df["IP"] + df["IP_next"])
    return df.reset_index(drop=True)


def _center(df):
    """Weighted-center target and predictors within one season pair."""
    w = df["w"].to_numpy()
    out = {}
    for col in ("rFIP_park", "xERA", "target", "bb_adj", "GB%", "FB%", "PU%"):
        v = df[col].to_numpy(dtype=float)
        out[col] = v - np.average(v, weights=w)
    out["w"] = w
    return out


# --- Scale-invariant scoring ------------------------------------------------
#
# Next-season ERA regresses ~40% toward the mean for everyone, so the best
# prediction of it is not the blend itself but a *shrunk* blend, s * blend,
# with s ~0.6. The runtime never needs s -- the WAR chassis centers on the
# league average -- so what the constants encode is only the blend's
# *direction* (the rFIP:xERA mix). Scoring every setting at s = 1 would
# instead reward whichever mix happens to minimize the over-dispersion
# error, which is not the same as the mix that predicts best. (An earlier
# version of this script did exactly that, and its "current weights win 0/4
# folds" verdict was an artifact of it.) So every setting below gets its own
# best s, fitted on the training folds only, and is then scored on the
# held-out fold. The comparison is then purely about the mix.

def _direction(c, fip_w, bb_k=0.0):
    """Centered blend for one pair: the runtime's mix, plus bb_k times the
    runtime's batted-ball adjustment."""
    return fip_w * c["rFIP_park"] + (1 - fip_w) * c["xERA"] + bb_k * c["bb_adj"]


def _fit_scale(train, fip_w, bb_k=0.0):
    """Best weighted-least-squares scale s for s * direction, on train only."""
    num = sum(float(np.sum(c["w"] * c["target"] * _direction(c, fip_w, bb_k))) for c in train)
    den = sum(float(np.sum(c["w"] * _direction(c, fip_w, bb_k) ** 2)) for c in train)
    return num / den if den > 0 else 0.0


def _score(held, fip_w, bb_k, scale):
    err = scale * _direction(held, fip_w, bb_k) - held["target"]
    w = held["w"]
    rmse = float(np.sqrt(np.average(err ** 2, weights=w)))
    mae = float(np.average(np.abs(err), weights=w))
    return rmse, mae


def fit_weights(centered_pairs):
    """Weighted, no-intercept, nonnegative least squares of next-season ERA
    on (rFIP_park, xERA), normalized to sum to 1.

    Returns (fip_weight, xera_weight, raw_coefficient_sum). Normalizing keeps
    the regression's direction -- the mix with the highest correlation to
    next-season ERA -- and drops its overall scale, which is exactly the
    split the scale-invariant scoring above makes. The raw sum (~0.6) is the
    year-to-year regression to the mean, printed only as a diagnostic.
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


def _print_table(title, rows, value_key):
    names = list(rows[0][value_key].keys())
    col = max(14, max(len(n) for n in names) + 2)
    if title:
        print(f"\n{title}")
    print(f"{'pair':<12}{'n':>5}" + "".join(f"{n:>{col}}" for n in names))
    for r in rows:
        print(f"{r['pair']:<12}{r['n']:>5}" + "".join(f"{r[value_key][n]:>{col}.4f}" for n in names))
    means = {n: np.mean([r[value_key][n] for r in rows]) for n in names}
    print(f"{'MEAN':<12}{'':>5}" + "".join(f"{means[n]:>{col}.4f}" for n in names))


def loo_eval(centered_pairs, labels, fitted_all):
    """Leave-one-season-pair-out: fit the mix (and every setting's scale) on
    the other pairs, score the held-out one. With only four pairs, a mix
    that wins pooled but loses on individual folds is overfitting."""
    current = _current_fip_weight()
    rows, fold_weights = [], []

    for i, label in enumerate(labels):
        train = [c for j, c in enumerate(centered_pairs) if j != i]
        held = centered_pairs[i]
        fold_fip_w, _, _ = fit_weights(train)
        fold_weights.append(fold_fip_w)

        settings = {"fitted (LOO)": fold_fip_w, "current": current, **BASELINES}
        rmse, mae = {}, {}
        for name, fip_w in settings.items():
            s = _fit_scale(train, fip_w)
            rmse[name], mae[name] = _score(held, fip_w, 0.0, s)
        rows.append({"pair": label, "n": len(held["w"]), "rmse": rmse, "mae": mae})

    print("\n" + "=" * 86)
    print("LEAVE-ONE-SEASON-PAIR-OUT: predicting next-season park-adjusted ERA")
    print("(each setting's scale fitted on the training folds; see _fit_scale)")
    print("=" * 86)
    _print_table("Weighted RMSE:", rows, "rmse")
    _print_table("Weighted MAE:", rows, "mae")

    print(f"\nPer-fold fitted rFIP weight: "
          f"{', '.join(f'{lbl}={w:.3f}' for lbl, w in zip(labels, fold_weights))}")
    spread = max(fold_weights) - min(fold_weights)
    print(f"  fold spread: {spread:.3f}  (mean {np.mean(fold_weights):.3f}, "
          f"all-pairs fit {fitted_all:.3f})")
    if spread > 0.20:
        print("  ! Wide spread across folds -- the point estimate is unstable.")
        print("    Prefer the fold mean, or keep the current weights.")

    deltas = [r["rmse"]["fitted (LOO)"] - r["rmse"]["current"] for r in rows]
    wins = sum(1 for d in deltas if d < 0)
    print(f"\nFolds where fitted beats current: {wins}/{len(rows)}   "
          f"(RMSE delta per fold: {', '.join(f'{d:+.4f}' for d in deltas)})")
    return wins, len(rows), fold_weights


def batted_ball_joint_fit(centered_pairs, fip_w):
    """Diagnostic: regress next-season ERA on the blend AND the three
    batted-ball rates jointly, so the blend's coefficient absorbs the
    regression-to-the-mean scale. (An earlier version regressed the raw
    residual y - blend on the rates; that residual carries a -0.4 * blend
    term which is itself correlated with GB/PU rate, and produced
    spurious, sign-flipped coefficients.)

    Each rate coefficient is divided by the blend's coefficient to express
    it in the runtime's blend units, comparable to the hand-picked
    GB/FB/PU coefficients. GB/FB/PU are structurally collinear (they sum to
    1 with LD) and xERA already carries much of their signal, so treat
    these as a sanity check, not values to paste in.
    """
    cols = ("GB%", "FB%", "PU%")
    base = np.concatenate([_direction(c, fip_w) for c in centered_pairs])
    rates = np.vstack([np.concatenate([c[k] for c in centered_pairs]) for k in cols]).T
    x = np.column_stack([base, rates])
    y = np.concatenate([c["target"] for c in centered_pairs])
    w = np.concatenate([c["w"] for c in centered_pairs])

    ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
    x, y, w = x[ok], y[ok], w[ok]
    sw = np.sqrt(w)
    xw, yw = x * sw[:, None], y * sw
    coefs, *_ = np.linalg.lstsq(xw, yw, rcond=None)

    dof = max(len(yw) - x.shape[1], 1)
    sigma2 = float(((yw - xw @ coefs) ** 2).sum() / dof)
    try:
        se = np.sqrt(np.diag(sigma2 * np.linalg.inv(xw.T @ xw)))
    except np.linalg.LinAlgError:
        se = np.full(x.shape[1], np.nan)

    s = coefs[0]
    # Runtime signs: GB and PU are credits (negative runs), FB is a penalty.
    current = {"GB%": -cwar.GB_RUN_COEF, "FB%": cwar.FB_RUN_COEF, "PU%": -cwar.PU_RUN_COEF}
    print("\n" + "=" * 86)
    print("BATTED-BALL JOINT FIT (diagnostic, not auto-applied)")
    print("=" * 86)
    print(f"blend coefficient (the regression-to-the-mean scale): {s:.3f}")
    print(f"{'term':<8}{'coef':>10}{'std err':>10}{'t':>8}{'blend units':>14}   current")
    for i, key in enumerate(cols, start=1):
        t = coefs[i] / se[i] if np.isfinite(se[i]) and se[i] > 0 else float("nan")
        blend_units = coefs[i] / s if s else float("nan")
        print(f"{key:<8}{coefs[i]:>10.3f}{se[i]:>10.3f}{t:>8.2f}{blend_units:>14.2f}   {current[key]:+.2f}")
    print("\n|t| < 2 means no detectable batted-ball signal left beyond the blend.")


def batted_ball_variant_check(centered_pairs, labels, fip_w):
    """The decisive batted-ball test. Score the blend with the runtime's
    batted-ball adjustment (shared helper, league-wide averages) at full,
    half, and zero strength, each with its own training-fold scale.

    The sample is all bip >= 100: the full-xERA-trust regime. There the
    runtime's (1 - bip_reliability) fade makes the adjustment exactly 0, so
    'none' IS the runtime; 'full' is the old unconditional version.
    """
    variants = {"full (old)": 1.0, "half": 0.5, "none (runtime)": 0.0}
    rows = []
    for i, label in enumerate(labels):
        train = [c for j, c in enumerate(centered_pairs) if j != i]
        held = centered_pairs[i]
        rmse = {}
        for name, k in variants.items():
            s = _fit_scale(train, fip_w, k)
            rmse[name], _ = _score(held, fip_w, k, s)
        rows.append({"pair": label, "n": len(held["w"]), "rmse": rmse})

    print("\n" + "=" * 86)
    print("BATTED-BALL ADJUSTMENT ON TOP OF THE BLEND (weighted RMSE, LOO, own scale)")
    print("=" * 86)
    _print_table("", rows, "rmse")

    deltas = [r["rmse"]["full (old)"] - r["rmse"]["none (runtime)"] for r in rows]
    helps = sum(1 for d in deltas if d < 0)
    print(f"\nFolds where the full adjustment beats none: {helps}/{len(rows)}   "
          f"(RMSE delta per fold: {', '.join(f'{d:+.4f}' for d in deltas)})")
    return helps, len(rows)


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
    current = _current_fip_weight()

    fip_w, xera_w, raw_total = fit_weights(centered)
    print("\n" + "=" * 86)
    print("FITTED WEIGHTS (all pairs pooled, weighted NNLS, normalized to sum 1)")
    print("=" * 86)
    print(f"  rFIP  {fip_w:.3f}")
    print(f"  xERA  {xera_w:.3f}")
    print(f"  (raw coefficient sum before normalizing: {raw_total:.3f} -- the year-to-year")
    print("   regression to the mean; the scoring below fits it per setting.)")
    print(f"  current runtime weights: rFIP {current:.3f} / xERA {1 - current:.3f}")

    wins, folds, _ = loo_eval(centered, labels, fip_w)
    batted_ball_joint_fit(centered, current)
    bb_helps, bb_folds = batted_ball_variant_check(centered, labels, current)

    # Extra, clearly-labeled look at the in-progress season. Not fitted on;
    # every setting's scale comes from the four completed pairs.
    n, nxt = IN_PROGRESS_PAIR
    partial = build_pair(features, n, nxt)
    if len(partial):
        c = _center(partial)
        print("\n" + "=" * 86)
        print(f"EXTRA OUT-OF-SAMPLE CHECK: {n}->{nxt} (season IN PROGRESS -- not fitted on)")
        print("=" * 86)
        print(f"  n = {len(partial)}")
        for name, w in {"fitted": fip_w, "current": current, **BASELINES}.items():
            rmse, mae = _score(c, w, 0.0, _fit_scale(centered, w))
            print(f"    {name:<12} RMSE {rmse:.4f}   MAE {mae:.4f}")

    first, last = labels[0].split("->")[0], labels[-1].split("->")[1]
    print("\n" + "=" * 86)
    print("DECISION")
    print("=" * 86)
    if wins == folds:
        print(f"Blend weights: fitted beats current on {wins}/{folds} folds -- consistent.")
        print("Paste into app/cwar.py (already normalized, so the runtime's normalization")
        print("is a no-op):")
        print()
        print(f"    FIP_WEIGHT_BASE = {fip_w:.2f}")
        print(f"    CONTACT_WEIGHT_BASE = {xera_w:.2f}")
        print(f"    # Fitted against next-season park-adjusted ERA over {first}-{last}")
        print(f"    # season pairs (scripts/tune_cwar_weights.py); beat the prior mix on")
        print(f"    # {wins}/{folds} leave-one-season-out folds.")
    else:
        print(f"Blend weights: fitted beats current on only {wins}/{folds} folds -- not")
        print("consistent. Keep the current weights.")

    print()
    if bb_helps == bb_folds:
        print(f"Batted-ball adjustment: full strength beats none on {bb_helps}/{bb_folds} folds,")
        print("even with xERA at full trust -- it carries signal xERA misses. Consider")
        print("dropping the runtime's (1 - bip_reliability) fade.")
    elif bb_helps == 0:
        print(f"Batted-ball adjustment: none beats full strength on {bb_folds}/{bb_folds} folds --")
        print("at full xERA trust it only adds noise. The runtime's fade is justified.")
    else:
        print(f"Batted-ball adjustment: full strength beats none on {bb_helps}/{bb_folds} folds --")
        print("inconclusive; no evidence either way at full xERA trust.")


if __name__ == "__main__":
    main()
