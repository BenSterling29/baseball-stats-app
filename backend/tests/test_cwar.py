"""Unit tests for the cWAR blend math, on small synthetic frames.

The fixtures deliberately mimic the real bref quirks these modules guard
against: a 1-based index, string-typed numeric columns, and 'GB/FB' holding
GB% (a rate) rather than a ratio.
"""
import pandas as pd
import pytest

from app import cwar, park_factors


def _pitching_df():
    # 1-based index, like pitching_stats_bref really returns.
    return pd.DataFrame(
        {
            "mlbID": [1, 2, 3],
            "Name": ["Ace Starter", "Mid Rotation", "No Statcast"],
            "IP": [180.0, 150.0, 60.0],
            "G": [30, 28, 55],
            "GS": [30, 28, 0],
            "BF": [720, 640, 260],
            "SO": [220, 130, 45],
            "BB": [40, 55, 25],
            "HBP": [5, 6, 3],
            "HR": [15, 22, 9],
            "ER": [60, 75, 32],
            "R": [66, 82, 35],
            # league-average-ish mix for everyone so the batted-ball
            # adjustment stays ~0 and the blend itself is what's tested
            "GB/FB": [0.45, 0.45, 0.45],
            "LD": [0.20, 0.20, 0.20],
            "PU": [0.05, 0.05, 0.05],
        },
        index=[1, 2, 3],
    )


def _bwar_pitch_df():
    return pd.DataFrame(
        {
            "mlb_ID": [1, 2, 3],
            "team_ID": ["ANY", "ANY", "ANY"],
            "stint_ID": [1, 1, 1],
            "IPouts": [540, 450, 180],
            "PPF": [100, 100, 100],
        }
    )


def _no_expected():
    return pd.DataFrame({"player_id": [], "xera": [], "bip": []})


def _compute(pitching=None, expected=None, bwar_pitch=None):
    pitching = pitching if pitching is not None else _pitching_df()
    expected = expected if expected is not None else _no_expected()
    bwar_pitch = bwar_pitch if bwar_pitch is not None else _bwar_pitch_df()
    park_df = park_factors.from_bwar_pitch(bwar_pitch)
    return cwar.compute(pitching, expected, bwar_pitch, park_df)


def test_blend_weights_are_convex():
    """With a neutral park, a pitcher whose xERA equals his rFIP and whose
    batted-ball mix is league average must get blendedERA == rFIP exactly.
    This is the regression test for the 0.70+0.25=0.95 weight bug, which
    deflated every blendedERA by ~5% and inflated all cWAR values."""
    fip_only = _compute()
    pd.testing.assert_series_equal(
        fip_only["blendedERA"], fip_only["rFIP"], check_names=False
    )

    # Same must hold at full Statcast trust when xERA happens to equal rFIP.
    expected = pd.DataFrame(
        {"player_id": [1, 2, 3], "xera": fip_only["rFIP"].tolist(), "bip": [500, 500, 500]}
    )
    full_trust = _compute(expected=expected)
    pd.testing.assert_series_equal(
        full_trust["blendedERA"], full_trust["rFIP"], check_names=False
    )


def test_xera_moves_blend_toward_contact_quality():
    fip = _compute()
    blended = _compute(expected=pd.DataFrame({"player_id": [1], "xera": [2.00], "bip": [500]}))
    # Pitcher 1's xERA is far better than his rFIP -> blended below rFIP...
    assert blended.loc[0, "blendedERA"] < blended.loc[0, "rFIP"]
    # ...but still FIP-dominated (contact weight caps at ~26%).
    assert blended.loc[0, "blendedERA"] > 2.00
    # Pitchers with no Statcast row are untouched.
    assert blended.loc[2, "blendedERA"] == pytest.approx(fip.loc[2, "blendedERA"])


def test_low_bip_shrinks_contact_weight():
    """The same great xERA should move blendedERA less on 10 tracked balls
    in play than on 500."""
    big = _compute(expected=pd.DataFrame({"player_id": [1], "xera": [2.0], "bip": [500]}))
    small = _compute(expected=pd.DataFrame({"player_id": [1], "xera": [2.0], "bip": [10]}))
    assert small.loc[0, "blendedERA"] > big.loc[0, "blendedERA"]


def test_groundball_tendency_earns_credit():
    """With no Statcast coverage, the batted-ball mix is the only contact
    signal there is, so a heavy groundballer gets full credit for it."""
    pitching = _pitching_df()
    pitching.loc[1, "GB/FB"] = 0.60  # heavy groundballer vs. league ~0.45
    out = _compute(pitching=pitching)
    assert out.loc[0, "blendedERA"] < out.loc[0, "rFIP"]


def test_batted_ball_adjustment_fades_as_xera_takes_over():
    """xERA already encodes batted-ball mix (it's built from exit velocity
    and launch angle), so applying the mix adjustment on top of a
    full-strength xERA double-counts. The runtime scales it by
    (1 - bip_reliability); at full trust it must vanish, and at partial
    trust part of it must survive."""
    pitching = _pitching_df()
    pitching.loc[1, "GB/FB"] = 0.60  # extreme groundballer

    # Full Statcast trust: xERA carries the contact signal, mix adds nothing.
    xera_value = 3.50
    full = _compute(pitching=pitching,
                    expected=pd.DataFrame({"player_id": [1], "xera": [xera_value], "bip": [500]}))
    contact_w = (cwar.CONTACT_WEIGHT_BASE
                 / (cwar.FIP_WEIGHT_BASE + cwar.CONTACT_WEIGHT_BASE))
    pure_blend = (1 - contact_w) * full.loc[0, "rFIP"] + contact_w * xera_value
    assert full.loc[0, "blendedERA"] == pytest.approx(pure_blend, abs=0.001)

    # Half trust: half the mix adjustment survives, so the groundballer's
    # blend still sits below the pure two-term mix.
    half = _compute(pitching=pitching,
                    expected=pd.DataFrame({"player_id": [1], "xera": [xera_value], "bip": [50]}))
    half_contact_w = contact_w * 0.5
    half_pure = (1 - half_contact_w) * half.loc[0, "rFIP"] + half_contact_w * xera_value
    assert half.loc[0, "blendedERA"] < half_pure


def test_missing_batted_ball_data_is_neutral():
    pitching = _pitching_df()
    pitching.loc[1, ["GB/FB", "LD", "PU"]] = None
    out = _compute(pitching=pitching)
    # Falls back to league average -> adjustment is a no-op, not NaN. With no
    # FB% available the HR regression also no-ops, so rFIP == FIP here.
    assert out.loc[0, "blendedERA"] == pytest.approx(out.loc[0, "FIP"], abs=0.02)
    assert out.loc[0, "rFIP"] == pytest.approx(out.loc[0, "FIP"])
    assert out["cWAR"].notna().all()


def test_park_adjustment_credits_hitter_park():
    """Regression test for the pre-park-adjustment cWAR: two pitchers with
    identical lines, one in a Coors-like park (PF 1.12), must not get the
    same cWAR -- the hitter-park pitcher's inflated rFIP is discounted."""
    pitching = _pitching_df()
    bwar_pitch = _bwar_pitch_df()
    bwar_pitch.loc[0, "team_ID"] = "COL"
    bwar_pitch.loc[0, "PPF"] = 112
    out = _compute(pitching=pitching, bwar_pitch=bwar_pitch)
    neutral = _compute(pitching=pitching)
    assert out.loc[0, "PF"] == pytest.approx(1.12)
    assert out.loc[0, "cWAR"] > neutral.loc[0, "cWAR"]


def test_park_adjustment_applies_to_rfip_only_not_xera():
    """xERA is EV/LA-derived and essentially park-neutral, so it must NOT be
    divided by the park factor -- only the outcome-based rFIP component is.
    A Coors pitcher with a fixed xERA should therefore gain less than he
    would if the whole blend were park-adjusted."""
    pitching = _pitching_df()
    bwar_pitch = _bwar_pitch_df()
    bwar_pitch.loc[0, "team_ID"] = "COL"
    bwar_pitch.loc[0, "PPF"] = 112
    # Full Statcast trust so the contact component carries real weight.
    expected = pd.DataFrame({"player_id": [1], "xera": [4.00], "bip": [500]})

    coors = _compute(pitching=pitching, expected=expected, bwar_pitch=bwar_pitch)
    neutral = _compute(pitching=pitching, expected=expected)

    pf = coors.loc[0, "PF"]
    assert pf == pytest.approx(1.12)
    # The blend must equal (park-adjusted rFIP) and (raw xERA) mixed -- the
    # xERA term is identical in both parks.
    contact_w = (cwar.CONTACT_WEIGHT_BASE
                 / (cwar.FIP_WEIGHT_BASE + cwar.CONTACT_WEIGHT_BASE))
    expected_blend = (1 - contact_w) * (coors.loc[0, "rFIP"] / pf) + contact_w * 4.00
    assert coors.loc[0, "blendedERA"] == pytest.approx(expected_blend, abs=0.01)

    # Sanity: had xERA been park-adjusted too, the blend would be lower still.
    fully_adjusted = ((1 - contact_w) * coors.loc[0, "rFIP"] + contact_w * 4.00) / pf
    assert coors.loc[0, "blendedERA"] > fully_adjusted
    # And the Coors pitcher still beats his neutral-park twin.
    assert coors.loc[0, "cWAR"] > neutral.loc[0, "cWAR"]


def test_rfip_pulls_hr_outlier_toward_league_rate():
    """Two pitchers with identical everything except HR total: the one whose
    HRs far exceed what his fly-ball count predicts gets rFIP pulled down
    toward league expectation (and vice versa)."""
    pitching = _pitching_df()
    pitching.loc[1, "HR"] = 35  # HR-unlucky relative to his FB count
    out = _compute(pitching=pitching)
    # Classic FIP charges him the full amount; rFIP gives some back.
    assert out.loc[0, "rFIP"] < out.loc[0, "FIP"]

    pitching2 = _pitching_df()
    pitching2.loc[1, "HR"] = 3  # HR-lucky
    out2 = _compute(pitching=pitching2)
    assert out2.loc[0, "rFIP"] > out2.loc[0, "FIP"]


def test_rfip_matches_fip_when_hr_total_meets_expectation():
    """A pitcher whose HR total is exactly league HR/FB times his own fly
    balls has nothing to regress -- rFIP should equal FIP."""
    pitching = _pitching_df()
    df = pitching.reset_index(drop=True).copy()
    for col in ("IP", "SO", "BB", "HBP", "HR", "ER", "BF"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    _, fb_pct = cwar._batted_ball_rates(df)
    bip = df["BF"] - df["SO"] - df["BB"] - df["HBP"] - df["HR"]
    fb_count = fb_pct * bip
    league_rate = df["HR"].sum() / fb_count.sum()

    # Give everyone exactly their expected HR total.
    pitching["HR"] = (fb_count * league_rate).round(6).values
    out = _compute(pitching=pitching)
    for i in range(3):
        assert out.loc[i, "rFIP"] == pytest.approx(out.loc[i, "FIP"], abs=0.03)


def _numeric(pitching):
    df = pitching.reset_index(drop=True).copy()
    for col in ("IP", "SO", "BB", "HBP", "HR", "ER", "BF"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def test_rfip_is_league_total_preserving():
    """The regression moves home runs between pitchers, it doesn't add or
    remove them league-wide -- so IP-weighted league rFIP must equal league
    FIP exactly. Regression test: with a plain sum(HR)/sum(FB) league rate
    this only held approximately, because each pitcher's shift is
    (1 - r_i) * (expected_i - HR_i) and reliability r_i varies; an earlier
    version of this test used abs=0.05, loose enough to hide that."""
    pitching = _pitching_df()
    pitching.loc[1, "HR"] = 35
    pitching.loc[2, "HR"] = 5
    df = _numeric(pitching)
    rfip_s, fip_s, _ = cwar.rfip(df)
    ip = df["IP"]
    lg_rfip = (rfip_s * ip).sum() / ip.sum()
    lg_fip = (fip_s * ip).sum() / ip.sum()
    assert lg_rfip == pytest.approx(lg_fip, abs=1e-9)


def test_rfip_preserves_totals_with_missing_batted_ball_data():
    """A pitcher with no FB data keeps his own HR (reliability 1). His HRs
    must not leak into the league HR/FB rate the others are regressed
    toward -- they used to, via a numerator that summed everyone's HR over
    a denominator that skipped his missing fly-ball count."""
    pitching = _pitching_df()
    pitching.loc[1, "HR"] = 35
    pitching.loc[3, ["GB/FB", "LD", "PU"]] = None
    pitching.loc[3, "HR"] = 25  # lots of HR, but no FB count to regress him by
    df = _numeric(pitching)
    rfip_s, fip_s, _ = cwar.rfip(df)
    assert rfip_s.iloc[2] == pytest.approx(fip_s.iloc[2])  # untouched
    ip = df["IP"]
    assert (rfip_s * ip).sum() == pytest.approx((fip_s * ip).sum(), abs=1e-9)


def test_rfip_clips_negative_fly_ball_count():
    """bref's rounded rates can sum past 1 on a tiny sample, making the
    derived FB% negative. That must not produce negative reliability and an
    extrapolated HR count outside both actual and expected."""
    pitching = _pitching_df()
    pitching.loc[3, ["GB/FB", "LD", "PU"]] = [0.60, 0.35, 0.10]  # sums to 1.05
    df = _numeric(pitching)
    rfip_s, fip_s, _ = cwar.rfip(df)
    # FB count clips to 0 -> reliability 0 -> fully regressed to expected
    # HR of 0 (no fly balls), i.e. rFIP equals FIP with zero home runs.
    no_hr_fip = fip_s.iloc[2] - 13 * df.loc[2, "HR"] / df.loc[2, "IP"]
    assert rfip_s.iloc[2] == pytest.approx(no_hr_fip)
    assert rfip_s.notna().all()


def test_rfip_regresses_small_samples_harder():
    """The same HR overage should move a low-fly-ball reliever further from
    his own FIP than a high-fly-ball starter, since he has less sample to
    back his own rate."""
    pitching = _pitching_df()
    pitching.loc[1, "HR"] = 30   # starter, ~200 FB
    pitching.loc[3, "HR"] = 20   # reliever, ~70 FB
    out = _compute(pitching=pitching)
    starter_shift = abs(out.loc[0, "rFIP"] - out.loc[0, "FIP"])
    reliever_shift = abs(out.loc[2, "rFIP"] - out.loc[2, "FIP"])
    # Per-unit-of-FIP-distance, the reliever is regressed more aggressively.
    assert reliever_shift > starter_shift


def test_uses_shared_chassis_replacement_levels():
    """A starter and a reliever with the same (league-average) blended rate
    should differ in cWAR by roughly the .12/.03 replacement-level gap per
    9 IP -- i.e. cWAR now uses the same starter/reliever split as fWAR/bWAR
    instead of the old flat 1.13x factor that shortchanged starters."""
    out = _compute()
    starter, reliever = out.loc[0], out.loc[2]
    assert starter["GS"] == 30 and reliever["GS"] == 0
    # Both should be positive: replacement level sits below average.
    assert starter["cWAR"] > 0
    # Starter's per-9-IP replacement credit should be well above the reliever's.
    assert starter["cWAR"] / starter["IP"] > reliever["cWAR"] / reliever["IP"]
