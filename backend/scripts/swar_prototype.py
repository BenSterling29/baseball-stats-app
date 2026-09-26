"""Offline prototype: a Stuff + Command pitcher WAR ("sWAR").

Answers one question before anything touches the app: does a pitch-quality
metric -- a Stuff model (expected run value from a pitch's physical traits,
after tjStuff+, github.com/tnestico/tjstuff_plus) stacked with a Command
model (how the pitch missed the catcher's target, from OpenCommand) --
predict next-season run prevention better than FIP / rFIP / the cWAR blend?

Nothing here is imported by the app. Run it by hand:

    cd backend && ./venv/bin/pip install -r requirements-prototype.txt
    ./venv/bin/python -m scripts.swar_prototype              # pull (cached) + report
    ./venv/bin/python -m scripts.swar_prototype --pull-only  # just fill the cache
    ./venv/bin/python -m scripts.swar_prototype --max-chunks 4   # quick look: first
        # 4 cached weekly chunks per season, no Statcast network calls

Data (cached under backend/data/swar/, gitignored):
  - Statcast pitch-level data via pybaseball, one parquet per season,
    pulled in weekly chunks so an interrupted pull resumes.
  - OpenCommand (huggingface.co/datasets/tomdoyo/open-command, CC BY-NC-SA
    4.0 -- attribution required, non-commercial only): only each season's
    pbp_info / targets files, skipping ~9.5 GB of raw video tracks.

OpenCommand keys pitches by (game_pk, play_id) and pybaseball's Statcast
has no play_id, so pitches are joined on (game_pk, pitcher, batter, vx0,
vy0) with the velocities rounded to 0.01 -- a spot check matched 99.9% of
pitches with no duplicates. The join rate is printed and enforced.

Pipeline (the math lives in app/swar.py; this script only trains and scores):
  1. Target: swar.pitch_run_value, variant A (raw -delta_run_exp) and B
     (balls in play de-lucked with xwOBA and the season's guts woba_scale).
     Both are trained end to end; the one whose Stuff+Command validates
     better becomes the headline.
  2. Stuff: one HistGradientBoostingRegressor per pitch group on
     swar.STUFF_FEATURES (+ arm_angle if every season has it), leave-one-
     season-out, so no pitch is scored by a model that saw it.
  3. Command: a model of (run value - Stuff prediction) on
     swar.COMMAND_FEATURES, 2024+ plausible targets only, leave-one-season-
     out. A pitch's command_rv is predict(actual miss) - predict(on target).
  4. swar.aggregate -> pitcher-seasons -> next-season park-adjusted ERA
     validation with scale-invariant scoring (as in tune_cwar_weights), plus
     BB% / split-half reliability checks, the fitted k, and sWAR for the
     latest complete season next to bWAR/fWAR/cWAR.
"""
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pybaseball as pb  # noqa: E402

from app import cwar, guts, park_factors, swar  # noqa: E402
from app import pybaseball_client as pbc  # noqa: E402  (also enables pb.cache)
from scripts import tune_cwar_weights as tune  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "swar"
OC_DIR = DATA_DIR / "open_command"
OC_REPO = "tomdoyo/open-command"
OC_SEASONS = (2024, 2025, 2026)
DEFAULT_SEASONS = (2023, 2024, 2025, 2026)
CURRENT_SEASON = date.today().year
MIN_JOIN_RATE = 0.90

STATCAST_COLS = [
    "game_date", "game_pk", "game_type", "at_bat_number", "pitch_number",
    "pitcher", "batter", "p_throws", "stand", "pitch_type",
    "release_speed", "release_spin_rate", "spin_axis", "release_extension",
    "release_pos_x", "release_pos_z", "arm_angle", "pfx_x", "pfx_z",
    "vx0", "vy0", "vz0", "ax", "ay", "az", "plate_x", "plate_z", "sz_top", "sz_bot",
    "balls", "strikes", "type", "description", "events", "bb_type",
    "delta_run_exp", "estimated_woba_using_speedangle", "woba_value", "woba_denom",
]
OC_COLS = ["plausible", "inferred_x_in", "inferred_z_in", "plate_x_in", "plate_z_in"]
# Everything the modeling needs; the rest is dropped early (~3M rows x 4 seasons).
MODEL_COLS = [
    "season", "game_date", "game_pk", "at_bat_number", "pitch_number",
    "pitcher", "batter", "p_throws", "stand", "pitch_type",
    "release_speed", "release_spin_rate", "spin_axis", "release_extension",
    "release_pos_x", "release_pos_z", "arm_angle", "pfx_x", "pfx_z",
    "sz_top", "sz_bot", "balls", "strikes", "type",
    "delta_run_exp", "estimated_woba_using_speedangle", "woba_value", "woba_denom",
] + OC_COLS

VARIANTS = ("A", "B")  # A = raw run value, B = BIP de-lucked with xwOBA
ARM_ANGLE_MIN_COVERAGE = 0.95
MAX_TRAIN_ROWS = 300_000   # per fit; per-pitch run value is noise-dominated,
                           # so more rows buy little beyond this
RELIABILITY_CAPS = (250, 500, 1000)
# Per-pitch run value is almost all noise, so leaves must be large: a leaf of a
# few dozen pitches just memorizes which of them happened to get hit.
HGB_PARAMS = dict(max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
                  min_samples_leaf=500, l2_regularization=1.0, early_stopping=True,
                  validation_fraction=0.1, n_iter_no_change=20, random_state=0)


# --- Data pull ----------------------------------------------------------------

def _season_window(season):
    """Regular season fits inside mid-March..early October; never past yesterday."""
    start = date(season, 3, 15)
    end = min(date(season, 10, 5), date.today() - timedelta(days=1))
    return start, end


def pull_statcast(season):
    """One parquet per season, built from weekly chunk parquets (resumable)."""
    out = DATA_DIR / f"statcast_{season}.parquet"
    in_progress = season >= date.today().year
    if out.exists() and not in_progress:
        return pd.read_parquet(out)

    chunk_dir = DATA_DIR / "chunks" / str(season)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    start, end = _season_window(season)
    frames = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=6), end)
        path = chunk_dir / f"{cur.isoformat()}_{stop.isoformat()}.parquet"
        # The last chunk of an in-progress season is always refetched.
        if path.exists() and not (in_progress and stop == end):
            df = pd.read_parquet(path)
        else:
            print(f"  statcast {cur} .. {stop}", flush=True)
            df = pb.statcast(start_dt=cur.isoformat(), end_dt=stop.isoformat(), verbose=False)
            if df is None:
                df = pd.DataFrame(columns=STATCAST_COLS)
            df = df[[c for c in STATCAST_COLS if c in df.columns]]
            df.to_parquet(path, index=False)
        frames.append(df)
        cur = stop + timedelta(days=1)

    df = pd.concat([f for f in frames if len(f)], ignore_index=True)
    df = df[df["game_type"] == "R"].reset_index(drop=True)
    df.to_parquet(out, index=False)
    return df


def local_chunks(season, max_chunks):
    """The first `max_chunks` weekly chunks already on disk -- no network. For
    quick runs; pitcher-season totals are then partial, so the report's
    numbers only exercise the code, they don't mean anything."""
    frames = []
    for path in sorted((DATA_DIR / "chunks" / str(season)).glob("*.parquet"))[:max_chunks]:
        try:
            frames.append(pd.read_parquet(path))
        except Exception as exc:  # a chunk mid-write by a concurrent pull
            print(f"  skipping unreadable chunk {path.name}: {exc}")
    if not frames:
        raise SystemExit(f"{season}: no cached Statcast chunks under {DATA_DIR / 'chunks'}")
    df = pd.concat(frames, ignore_index=True)
    return df[df["game_type"] == "R"].reset_index(drop=True)


def pull_open_command(season):
    """pbp_info + targets for one season, joined into one per-pitch frame."""
    files = [OC_DIR / str(season) / name for name in ("pbp_info.csv.gz", "targets.csv.gz")]
    # Completed seasons don't change upstream; skip the hub round trip.
    if season >= CURRENT_SEASON or not all(f.exists() for f in files):
        from huggingface_hub import snapshot_download

        snapshot_download(
            OC_REPO, repo_type="dataset", local_dir=str(OC_DIR),
            allow_patterns=[f"{season}/pbp_info.csv.gz", f"{season}/targets.csv.gz"],
        )
    pbp = pd.read_csv(files[0], usecols=["game_pk", "play_id", "game_type", "pitcher_id",
                                         "batter_id", "vx0", "vy0"])
    tgt = pd.read_csv(files[1], usecols=["game_pk", "play_id", "plate_x_in", "plate_z_in",
                                         "plausible", "inferred_x_in", "inferred_z_in"])
    pbp = pbp[pbp["game_type"] == "R"]
    return pbp.merge(tgt, on=["game_pk", "play_id"], how="left")


def _join_key(df, pitcher_col, batter_col):
    return pd.DataFrame({
        "game_pk": df["game_pk"].astype("int64"),
        "pitcher": df[pitcher_col].astype("int64"),
        "batter": df[batter_col].astype("int64"),
        "vx0r": df["vx0"].round(2),
        "vy0r": df["vy0"].round(2),
    })


def attach_open_command(sc, oc, season):
    """Left-join OpenCommand targets onto Statcast pitches; enforce the match rate."""
    key_cols = ["game_pk", "pitcher", "batter", "vx0r", "vy0r"]
    # Rows missing any key field can't be joined (and pandas would match NaN
    # keys to each other). OpenCommand's are dropped; Statcast's stay in as
    # unmatched, so the join rate is still over every Statcast pitch.
    oc_ok = oc[["game_pk", "pitcher_id", "batter_id", "vx0", "vy0"]].notna().all(axis=1)
    sc_ok = sc[["game_pk", "pitcher", "batter", "vx0", "vy0"]].notna().all(axis=1)
    if (~oc_ok).any() or (~sc_ok).any():
        print(f"  {season}: {(~oc_ok).sum():,} OpenCommand and {(~sc_ok).sum():,} Statcast "
              "rows have a missing join-key field (left unmatched)")
    oc = oc[oc_ok].reset_index(drop=True)
    oc_keyed = pd.concat([_join_key(oc, "pitcher_id", "batter_id"), oc[OC_COLS]], axis=1)
    # A key shared by two OpenCommand pitches can't be attributed; drop both.
    oc_keyed = oc_keyed.drop_duplicates(key_cols, keep=False)

    unkeyed = sc[~sc_ok]
    sc = sc[sc_ok].reset_index(drop=True)
    sc = pd.concat([sc, _join_key(sc, "pitcher", "batter")[["vx0r", "vy0r"]]], axis=1)
    sc["game_pk"] = sc["game_pk"].astype("int64")
    sc["pitcher"] = sc["pitcher"].astype("int64")
    sc["batter"] = sc["batter"].astype("int64")
    merged = sc.merge(oc_keyed, on=key_cols, how="left", indicator=True)
    if len(unkeyed):
        merged = pd.concat([merged, unkeyed], ignore_index=True)

    is_match = merged["_merge"] == "both"
    matched = is_match.mean()
    has_target = (merged["plausible"] == True).mean()  # noqa: E712  (NaN-safe)
    print(f"  {season}: {matched:.1%} of Statcast pitches matched OpenCommand; "
          f"{has_target:.1%} have a plausible inferred target")
    rate = matched
    if season >= CURRENT_SEASON:
        # OpenCommand's in-progress season lags Statcast by weeks, so the
        # key-drift check only counts games it has published; later pitches
        # just have no target (Stuff still scores them).
        covered = merged["game_pk"].isin(set(oc_keyed["game_pk"]))
        rate = is_match[covered].mean()
        print(f"  {season}: in progress -- OpenCommand covers {covered.mean():.1%} of pitches "
              f"(through {merged.loc[covered, 'game_date'].max()}); "
              f"{rate:.1%} of those matched")
    if rate < MIN_JOIN_RATE:
        raise SystemExit(f"{season}: OpenCommand join rate {rate:.1%} < {MIN_JOIN_RATE:.0%}. "
                         "The join key likely drifted; inspect before trusting any command numbers.")

    # swar.command_features mixes OpenCommand's plate location with its
    # inferred target, so both must be in OpenCommand's frame -- check that
    # frame is Statcast's (feet -> inches, same sign) so Statcast's zone
    # (sz_top/sz_bot) can be used with it.
    dx = (merged["plate_x_in"] - merged["plate_x"].astype(float) * 12).abs().median()
    dz = (merged["plate_z_in"] - merged["plate_z"].astype(float) * 12).abs().median()
    print(f"  {season}: median |plate_x_in - plate_x*12| = {dx:.2f} in, "
          f"|plate_z_in - plate_z*12| = {dz:.2f} in")
    if dx > 2 or dz > 2:
        print(f"  ! {season}: OpenCommand's plate frame disagrees with Statcast's; "
              "command features are suspect.")
    return merged.drop(columns=["_merge", "vx0r", "vy0r"])


def load_season(season, max_chunks=None):
    sc = pull_statcast(season) if max_chunks is None else local_chunks(season, max_chunks)
    if season in OC_SEASONS:
        sc = attach_open_command(sc, pull_open_command(season), season)
    return sc


# --- Pitch frame --------------------------------------------------------------

def prepare(pitches):
    """{season: raw frame} -> one modeling frame with season/group, both
    targets, and the Stuff/Command features. Returns (df, stuff_features)."""
    frames = []
    for season, raw in pitches.items():
        df = raw.copy()
        df["season"] = season
        for col in OC_COLS:  # 2023 has no OpenCommand
            if col not in df.columns:
                df[col] = np.nan
        frames.append(df[MODEL_COLS])
    df = pd.concat(frames, ignore_index=True)
    no_id = df[["game_pk", "pitcher", "batter"]].isna().any(axis=1)
    if no_id.any():
        print(f"  dropping {no_id.sum():,} pitches with no game/pitcher/batter id")
        df = df[~no_id].reset_index(drop=True)

    # pybaseball returns nullable Int64/Float64; sklearn wants plain floats (NA -> NaN).
    for col in df.columns:
        if col in ("game_pk", "pitcher", "batter", "season"):
            df[col] = df[col].astype("int64")
        elif pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
            df[col] = df[col].astype("float64")

    df["group"] = swar.pitch_group(df["pitch_type"])
    dropped = df["group"].isna()
    print(f"  dropping {dropped.sum():,} unmodeled pitches ({dropped.mean():.2%}: "
          f"{', '.join(df.loc[dropped, 'pitch_type'].fillna('none').value_counts().index[:6])})")
    df = df[~dropped].reset_index(drop=True)

    df["rv_A"] = swar.pitch_run_value(df)
    df["rv_B"] = np.nan
    for season in df["season"].unique():
        in_s = df["season"] == season
        g = guts.for_season(int(season))
        df.loc[in_s, "rv_B"] = swar.pitch_run_value(df[in_s], woba_scale=g["woba_scale"])
    df = df.drop(columns=["estimated_woba_using_speedangle", "woba_value", "woba_denom"])

    df = swar.command_features(swar.stuff_features(df))

    features = list(swar.STUFF_FEATURES)
    cov = df.groupby("season")["arm_angle"].apply(lambda s: s.notna().mean())
    print("  arm_angle coverage: " + ", ".join(f"{s}={c:.1%}" for s, c in cov.items()))
    if cov.min() >= ARM_ANGLE_MIN_COVERAGE:
        features.append("arm_angle")
        print("  -> arm_angle included as a Stuff feature")
    else:
        print("  -> arm_angle dropped (not populated in every season)")
    return df, features


# --- Models -------------------------------------------------------------------

def _folds(df, mask, seasons):
    """Leave-one-season-out over `seasons`, as (label, train, test) masks.
    With a single season (sample runs) there is nothing to leave out, so it
    falls back to a 2-fold split on game_pk parity -- still out-of-sample
    per pitch, just not across seasons."""
    if len(seasons) >= 2:
        for s in seasons:
            yield str(s), mask & (df["season"] != s), mask & (df["season"] == s)
    else:
        print(f"    (only season {seasons[0]} available: 2-fold game_pk split instead of LOSO)")
        odd = df["game_pk"] % 2 == 1
        yield "even", mask & odd, mask & ~odd
        yield "odd", mask & ~odd, mask & odd


def _fit(x, y, rng):
    from sklearn.ensemble import HistGradientBoostingRegressor

    if len(y) > MAX_TRAIN_ROWS:
        idx = rng.choice(len(y), MAX_TRAIN_ROWS, replace=False)
        x, y = x[idx], y[idx]
    return HistGradientBoostingRegressor(**HGB_PARAMS).fit(x, y)


def fit_stuff(df, target, features, rng):
    """Out-of-sample Stuff prediction for every pitch: one model per pitch
    group, each season scored by a model trained on the others."""
    pred = np.full(len(df), np.nan)
    seasons = sorted(df["season"].unique())
    x_all = df[features].to_numpy(dtype=float)
    y_all = df[target].to_numpy(dtype=float)
    for group in swar.GROUP_CODES:
        t0 = time.perf_counter()
        in_g = df["group"] == group
        for _, train, test in _folds(df, in_g, seasons):
            train = (train & df[target].notna()).to_numpy()
            test = test.to_numpy()
            if not train.any() or not test.any():
                continue
            model = _fit(x_all[train], y_all[train], rng)
            pred[test] = model.predict(x_all[test])
        print(f"    stuff[{target}] {group:<9} {in_g.sum():>9,} pitches  {time.perf_counter() - t0:6.1f}s")
    return pred


def fit_command(df, target, stuff_pred, rng):
    """Out-of-sample command_rv: a model of the residual (run value minus
    Stuff) on the miss and target features, evaluated at the actual miss
    minus at a perfect hit. Count, target location, and hands are held fixed
    in that difference, so it credits only the miss. NaN where there's no
    usable target."""
    resid = df[target].to_numpy(dtype=float) - stuff_pred
    usable = df["plausible"].eq(True) & df["miss_dist"].notna() & np.isfinite(stuff_pred)
    x_df = df[swar.COMMAND_FEATURES]
    x_all = x_df.to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    seasons = sorted(df.loc[usable, "season"].unique())
    if not seasons:
        print("    no plausible OpenCommand targets: command model skipped")
        return out
    t0 = time.perf_counter()
    for _, train, test in _folds(df, usable, seasons):
        train = (train & np.isfinite(resid)).to_numpy()
        test = test.to_numpy()
        model = _fit(x_all[train], resid[train], rng)
        x_test = x_df[test]
        out[test] = (model.predict(x_all[test])
                     - model.predict(swar.on_target(x_test).to_numpy(dtype=float)))
    print(f"    command[{target}] {usable.sum():>9,} pitches  {time.perf_counter() - t0:6.1f}s")
    return out


# --- Pitcher seasons + baselines ----------------------------------------------

def season_table(season):
    """Baselines for one season: tune_cwar_weights.build_features (so ERA /
    FIP / rFIP / xERA match what the tuning script and runtime use) plus the
    bref columns this script also needs."""
    feats = tune.build_features(season)
    raw = pb.pitching_stats_bref(season).reset_index(drop=True)  # 1-based index (AGENTS.md)
    extra = pd.DataFrame({
        "mlbID": pd.to_numeric(raw["mlbID"], errors="coerce"),
        "Name": raw["Name"].map(pbc._fix_mojibake),
        "R": pd.to_numeric(raw["R"], errors="coerce"),
        "BB": pd.to_numeric(raw["BB"], errors="coerce"),
        "BF": pd.to_numeric(raw["BF"], errors="coerce"),
    }).drop_duplicates("mlbID")
    df = feats.merge(extra, on="mlbID", how="left")
    df["RA9_park"] = 9 * df["R"] / df["IP"] / df["PF"]
    df["FIP_park"] = df["FIP"] / df["PF"]
    df["BB%"] = df["BB"] / df["BF"]
    # The cWAR blend at full trust, as tune_cwar_weights scores it.
    fip_w = cwar.FIP_WEIGHT_BASE / (cwar.FIP_WEIGHT_BASE + cwar.CONTACT_WEIGHT_BASE)
    df["blend"] = fip_w * df["rFIP_park"] + (1 - fip_w) * df["xERA"]
    return df


def build_pair(tables, aggs, n, nxt):
    """Season N's predictors (baselines + pitch-model rv) joined to season
    N+1's park-adjusted ERA/RA9 and BB%, filtered and weighted exactly as
    tune_cwar_weights.build_pair (IP >= 50, next IP >= 40, harmonic IP)."""
    a = tables[n].merge(aggs[n].drop(columns="Name"), left_on="mlbID", right_on="pitcher",
                        how="inner")
    b = tables[nxt][["mlbID", "IP", "ERA_park", "RA9_park", "BB%"]].rename(columns={
        "IP": "IP_next", "ERA_park": "target", "RA9_park": "target_ra9", "BB%": "bb_next"})
    df = a.merge(b, on="mlbID", how="inner")
    df = df[(df["IP"] >= tune.MIN_IP_N) & (df["IP_next"] >= tune.MIN_IP_NEXT)].copy()
    # Per-pitch runs saved -> an ERA-scale direction: -runs saved per 9.
    per9 = df["pitches"] / df["IP"] * 9
    df["stuff_era"] = -df["stuff_rv"] * per9
    df["cmd_era"] = -df["command_rv"] * per9
    df["total_era"] = -df["total_rv"] * per9
    if n not in OC_SEASONS:
        df["cmd_era"] = np.nan  # aggregate() leaves it at 0, which isn't a measurement
    df["w"] = 2 * df["IP"] * df["IP_next"] / (df["IP"] + df["IP_next"])
    return df.reset_index(drop=True)


# --- Scale-invariant scoring (after tune_cwar_weights) --------------------------
#
# Next-season ERA regresses ~40% toward the mean, so each predictor is scored
# as its best multiple -- beta fitted on the training pair(s) only, then
# applied to the held-out pair. That compares directions, not dispersion,
# which is all the WAR chassis cares about. It's also what makes the rv
# predictors comparable with ERA-scale ones at all.

PREDICTORS = [  # (name, columns, needs command)
    ("ERA", ["ERA_park"], False),
    ("FIP", ["FIP_park"], False),
    ("rFIP", ["rFIP_park"], False),
    ("xERA", ["xERA"], False),
    ("cWAR blend", ["blend"], False),
    ("Stuff", ["stuff_era"], False),
    ("Command", ["cmd_era"], True),
    ("Stuff+Command", ["total_era"], True),
    ("S+C+xERA (joint)", ["total_era", "xERA"], True),
]


def _center(df, cols):
    """Weighted-center the given columns within one pair."""
    w = df["w"].to_numpy(dtype=float)
    out = {"w": w}
    for col in cols:
        v = df[col].to_numpy(dtype=float)
        out[col] = v - np.average(v, weights=w)
    return out


def _fit_beta(train, cols, tgt="target"):
    """Weighted no-intercept least squares on centered pairs (a single
    scale when len(cols) == 1)."""
    x = np.vstack([np.column_stack([c[k] for k in cols]) for c in train])
    y = np.concatenate([c[tgt] for c in train])
    sw = np.sqrt(np.concatenate([c["w"] for c in train]))
    beta, *_ = np.linalg.lstsq(x * sw[:, None], y * sw, rcond=None)
    return beta


def _wcorr(x, y, w):
    mx, my = np.average(x, weights=w), np.average(y, weights=w)
    cov = np.average((x - mx) * (y - my), weights=w)
    vx, vy = np.average((x - mx) ** 2, weights=w), np.average((y - my) ** 2, weights=w)
    return float(cov / np.sqrt(vx * vy)) if vx > 0 and vy > 0 else float("nan")


def _score(held, cols, beta, tgt="target"):
    x = np.column_stack([held[k] for k in cols])
    pred = x @ beta
    err = pred - held[tgt]
    w = held["w"]
    # A lone predictor's corr uses its raw direction, so a wrong sign shows.
    corr = _wcorr(x[:, 0] if len(cols) == 1 else pred, held[tgt], w)
    return (float(np.sqrt(np.average(err ** 2, weights=w))),
            float(np.average(np.abs(err), weights=w)), corr)


def score_table(title, pairs, predictors):
    """Leave-one-pair-out over `pairs` [(label, df)]; with two pairs that's
    fit on one, score the other, both ways. Every predictor is scored on the
    same rows within a pair. Returns {name: {"rmse": [...], ...}}."""
    print(f"\n{title}")
    if len(pairs) < 2:
        print("  (needs at least two season pairs -- skipped)")
        return None
    cols = sorted({c for _, pc, _ in predictors for c in pc} | {"target"})
    centered = [_center(df.dropna(subset=cols), cols) for _, df in pairs]
    labels = [lbl for lbl, _ in pairs]
    res = {name: {"rmse": [], "mae": [], "corr": [], "beta": []} for name, _, _ in predictors}
    for i in range(len(pairs)):
        train = [c for j, c in enumerate(centered) if j != i]
        for name, pc, _ in predictors:
            beta = _fit_beta(train, pc)
            rmse, mae, corr = _score(centered[i], pc, beta)
            for key, val in (("rmse", rmse), ("mae", mae), ("corr", corr), ("beta", beta)):
                res[name][key].append(val)

    print("  held-out weighted RMSE per pair, then means; scale = beta fitted on the other pair(s)")
    print(f"  {'predictor':<18}" + "".join(f"{lbl:>13}" for lbl in labels)
          + f"{'RMSE':>9}{'MAE':>8}{'corr':>8}{'scale':>9}")
    print(f"  {'n':<18}" + "".join(f"{len(c['w']):>13}" for c in centered))
    for name, pc, _ in predictors:
        r = res[name]
        scale = f"{np.mean([b[0] for b in r['beta']]):>9.3f}" if len(pc) == 1 else f"{'joint':>9}"
        print(f"  {name:<18}" + "".join(f"{v:>13.4f}" for v in r["rmse"])
              + f"{np.mean(r['rmse']):>9.4f}{np.mean(r['mae']):>8.4f}{np.mean(r['corr']):>8.3f}{scale}")
    return res


def fit_k(pairs, tgt):
    """k: the scale mapping predicted runs saved per 9 onto `tgt` deviations
    (RA9 basis, which is what swar.compute's rate is). total_era is
    -runs_saved_p9, so its fitted scale is k directly."""
    cols = ["total_era", tgt]
    return float(_fit_beta([_center(df.dropna(subset=cols), cols) for df in pairs],
                           ["total_era"], tgt)[0])


# --- Extras -------------------------------------------------------------------

def bb_check(pairs):
    print("\nCOMMAND vs NEXT-SEASON BB% (weighted corr; Cmd is pitcher-positive, so "
          "expect negative)")
    print(f"  {'pair':<14}{'n':>6}{'Cmd~BB%next':>13}{'BB%~BB%next':>13}{'Cmd~BB%':>10}")
    for label, df in pairs:
        d = df.dropna(subset=["command_rv", "BB%", "bb_next"])
        w = d["w"].to_numpy()
        print(f"  {label:<14}{len(d):>6}{_wcorr(d['command_rv'], d['bb_next'], w):>13.3f}"
              f"{_wcorr(d['BB%'], d['bb_next'], w):>13.3f}{_wcorr(d['command_rv'], d['BB%'], w):>10.3f}")


def reliability(df, stuff_col, cmd_col):
    """Split-half (odd/even pitch within the pitcher-season, in game order) of
    the per-pitch means behind Stuff+ and Cmd+, among pitcher-seasons with
    at least `cap` pitches, using their first `cap`. Spearman-Brown gives the
    full-cap reliability; the implied shrink k (reliability = n / (n + k))
    is what swar.STUFF/COMMAND_SHRINK_PITCHES are guesses at."""
    print("\nSPLIT-HALF RELIABILITY (odd/even pitch, Spearman-Brown corrected)")
    print(f"  {'metric':<8}{'cap':>6}{'n':>6}{'r_half':>9}{'r_full':>9}{'implied k':>11}   current k")
    order = df.sort_values(["season", "pitcher", "game_date", "at_bat_number", "pitch_number"]).index
    for label, col, cur_k in (("Stuff+", stuff_col, swar.STUFF_SHRINK_PITCHES),
                              ("Cmd+", cmd_col, swar.COMMAND_SHRINK_PITCHES)):
        d = df.loc[order, ["season", "pitcher"]].assign(v=df.loc[order, col].to_numpy())
        d = d[d["v"].notna()]
        d["v"] -= d.groupby("season")["v"].transform("mean")
        d["i"] = d.groupby(["season", "pitcher"]).cumcount()
        n_tot = d.groupby(["season", "pitcher"])["v"].transform("size")
        for cap in RELIABILITY_CAPS:
            s = d[(n_tot >= cap) & (d["i"] < cap)]
            halves = s.assign(h=s["i"] % 2).groupby(["season", "pitcher", "h"])["v"].mean().unstack()
            if len(halves) < 10:
                print(f"  {label:<8}{cap:>6}{len(halves):>6}   (too few pitcher-seasons)")
                continue
            r = float(np.corrcoef(halves[0], halves[1])[0, 1])
            full = 2 * r / (1 + r) if r > -1 else float("nan")
            k = cap * (1 - full) / full if full > 0 else float("inf")
            print(f"  {label:<8}{cap:>6}{len(halves):>6}{r:>9.3f}{full:>9.3f}{k:>11.0f}   {cur_k}")


def swar_table(season, agg, k):
    """sWAR for one season via swar.compute, next to the app's bWAR/fWAR/cWAR
    (the same frames get_pitcher_war_compare merges)."""
    pitching = pb.pitching_stats_bref(season)
    bwar_pitch = pbc._bwar_pitch_for_season(season)
    park_df = park_factors.from_bwar_pitch(bwar_pitch)
    s = swar.compute(agg, pitching, bwar_pitch, park_df, k=k)
    s["Name"] = s["Name"].map(pbc._fix_mojibake)
    df = (s[["mlbID", "Name", "IP", "Stuff+", "Cmd+", "sWAR"]]
          .merge(pbc._pitcher_bwar_df(season)[["mlbID", "bWAR"]], on="mlbID", how="left")
          .merge(pbc._pitcher_fwar_df(season)[["mlbID", "fWAR"]], on="mlbID", how="left")
          .merge(pbc._pitcher_cwar_df(season)[["mlbID", "cWAR"]], on="mlbID", how="left"))

    fmt = {"IP": "{:.1f}".format, "Stuff+": "{:.0f}".format, "Cmd+": "{:.0f}".format,
           "sWAR": "{:.1f}".format, "bWAR": "{:.1f}".format, "fWAR": "{:.1f}".format,
           "cWAR": "{:.1f}".format}
    cols = ["Name", "IP", "Stuff+", "Cmd+", "sWAR", "bWAR", "fWAR", "cWAR"]
    ranked = df.sort_values("sWAR", ascending=False)
    print(f"\nsWAR {season} (k = {k:.3f}) -- TOP 15")
    print(ranked.head(15)[cols].to_string(index=False, formatters=fmt))
    print(f"\nsWAR {season} -- BOTTOM 15")
    print(ranked.tail(15)[cols].to_string(index=False, formatters=fmt))

    print(f"\nLEAGUE TOTALS {season} ({len(df)} pitchers)")
    for col in ("sWAR", "bWAR", "fWAR", "cWAR"):
        print(f"  {col}: {df[col].sum():8.1f}")
    corr = df[df["IP"] >= tune.MIN_IP_N][["sWAR", "bWAR", "fWAR", "cWAR"]].corr()["sWAR"]
    print(f"  corr with sWAR (IP >= {tune.MIN_IP_N}): "
          + ", ".join(f"{c}={corr[c]:.2f}" for c in ("bWAR", "fWAR", "cWAR")))


def verdict(res, variant):
    if res is None:
        print("\nVERDICT: no command-season pairs to validate on; nothing to conclude.")
        return
    m = {name: np.mean(r["rmse"]) for name, r in res.items()}
    folds = len(res["cWAR blend"]["rmse"])
    wins = sum(a < b for a, b in zip(res["Stuff+Command"]["rmse"], res["cWAR blend"]["rmse"]))
    wins_x = sum(a < b for a, b in zip(res["S+C+xERA (joint)"]["rmse"], res["cWAR blend"]["rmse"]))
    best_base = min(("FIP", "rFIP", "xERA", "cWAR blend"), key=m.get)
    beats = m["Stuff+Command"] < m[best_base]
    print("\nVERDICT")
    print(f"  Variant {variant}: Stuff+Command's held-out RMSE is {m['Stuff+Command']:.4f} vs "
          f"{m['rFIP']:.4f} rFIP, {m['xERA']:.4f} xERA and {m['cWAR blend']:.4f} for the cWAR blend; "
          f"it beats the blend on {wins}/{folds} folds, and adding xERA (joint fit) beats it on "
          f"{wins_x}/{folds} (mean {m['S+C+xERA (joint)']:.4f}). The best outcome-based "
          f"baseline is {best_base}, which Stuff+Command "
          f"{'beats' if beats else 'does not beat'} on average. With only "
          f"{folds} pairs (one of them a partial season), treat any gap under ~0.01 as noise: "
          + ("pitch quality looks like it carries next-season signal beyond the box-score "
             "estimators, worth a longer look." if beats and wins == folds else
             "this does not yet justify a fourth WAR in the app; at most it may be a useful "
             "input to the existing blend." if wins_x == folds else
             "this does not justify a fourth WAR in the app."))


# --- Main ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, nargs="+", default=list(DEFAULT_SEASONS))
    ap.add_argument("--pull-only", action="store_true")
    ap.add_argument("--max-chunks", type=int, default=None,
                    help="use only the first N cached weekly Statcast chunks per season "
                         "(no Statcast network calls); for quick, meaningless-numbers runs")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    pitches = {}
    for season in args.seasons:
        print(f"Loading {season}...", flush=True)
        pitches[season] = load_season(season, args.max_chunks)
        print(f"  {season}: {len(pitches[season]):,} regular-season pitches", flush=True)
    if args.pull_only:
        return
    report(pitches)


def report(pitches):
    t_start = time.perf_counter()
    print("\nPreparing pitch frame...", flush=True)
    df, stuff_feats = prepare(pitches)
    del pitches
    seasons = sorted(int(s) for s in df["season"].unique())
    print(f"  {len(df):,} modeled pitches, seasons {seasons}")

    rng = np.random.default_rng(0)
    for v in VARIANTS:
        print(f"\nTraining variant {v} ({'raw run value' if v == 'A' else 'BIP de-lucked'})...",
              flush=True)
        df[f"stuff_{v}"] = fit_stuff(df, f"rv_{v}", stuff_feats, rng)
        df[f"cmd_{v}"] = fit_command(df, f"rv_{v}", df[f"stuff_{v}"].to_numpy(), rng)

    print("\nFetching baselines (bref / Savant, disk-cached)...", flush=True)
    tables = {s: season_table(s) for s in seasons}

    aggs = {v: {} for v in VARIANTS}
    for v in VARIANTS:
        agg = swar.aggregate(df[["season", "pitcher"]].assign(
            stuff_rv=df[f"stuff_{v}"], command_rv=df[f"cmd_{v}"]))
        for s in seasons:
            a = agg[agg["season"] == s].drop(columns="season")
            aggs[v][s] = a.merge(tables[s][["mlbID", "Name"]], left_on="pitcher",
                                 right_on="mlbID", how="left").drop(columns="mlbID")

    # Pairs of consecutive loaded seasons; a pair whose N+1 is the current
    # season is partial and marked with '*'.
    pair_keys = [(n, n + 1) for n in seasons if n + 1 in seasons]
    results, pairs_by_v = {}, {}
    for v in VARIANTS:
        pairs = [(f"{n}->{nxt}{'*' if nxt >= CURRENT_SEASON else ''}", n, nxt,
                  build_pair(tables, aggs[v], n, nxt)) for n, nxt in pair_keys]
        pairs_by_v[v] = pairs
        print("\n" + "=" * 96)
        print(f"VARIANT {v}: predicting next-season park-adjusted ERA "
              "(* = next season in progress)")
        print("=" * 96)
        cmd_pairs = [(lbl, d) for lbl, n, _, d in pairs if n in OC_SEASONS]
        results[v] = score_table("Command-era pairs, all predictors:", cmd_pairs, PREDICTORS)
        score_table("All pairs, predictors that don't need OpenCommand:",
                    [(lbl, d) for lbl, _, _, d in pairs], [p for p in PREDICTORS if not p[2]])

    # Headline: the variant whose Stuff+Command (else Stuff) validates better.
    def _key(v):
        r = results[v]
        return np.mean(r["Stuff+Command"]["rmse"]) if r else float("inf")
    head = min(VARIANTS, key=_key)
    if all(results[v] is None for v in VARIANTS):
        print("\n(no command pairs; headline variant defaults to A)")
    else:
        print(f"\nHEADLINE VARIANT: {head} (Stuff+Command mean held-out RMSE "
              + ", ".join(f"{v}={_key(v):.4f}" for v in VARIANTS) + ")")

    pairs = pairs_by_v[head]
    cmd_pairs = [(lbl, d) for lbl, n, _, d in pairs if n in OC_SEASONS]
    bb_check(cmd_pairs)
    reliability(df, f"stuff_{head}", f"cmd_{head}")

    # k from completed command pairs (the in-progress one is too noisy to
    # set a constant with), falling back to whatever pairs exist.
    k_pairs = ([d for _, n, nxt, d in pairs if n in OC_SEASONS and nxt < CURRENT_SEASON]
               or [d for _, n, _, d in pairs if n in OC_SEASONS] or [d for *_, d in pairs])
    if k_pairs:
        k = fit_k(k_pairs, "target_ra9")
        # For reference: the same-season scale. The next-season k is smaller
        # because it also carries year-to-year regression to the mean, which
        # makes sWAR a forecast-flavored WAR (less spread than bWAR).
        k_same = fit_k([d.assign(ra9_now=d["RA9_park"]) for d in k_pairs], "ra9_now")
        print(f"\nFITTED k (runs saved per 9 -> next-season RA9_park deviation): {k:.3f}   "
              f"[same-season RA9 scale, for reference: {k_same:.3f}; n pairs = {len(k_pairs)}]")
    else:
        k = 1.0
        print("\n! no season pairs to fit k on; using k = 1.0")

    latest = max((s for s in seasons if s < CURRENT_SEASON), default=None)
    if latest is not None:
        swar_table(latest, aggs[head][latest], k)
    verdict(results[head], head)
    print(f"\n(total report time {time.perf_counter() - t_start:.0f}s)")


if __name__ == "__main__":
    main()
