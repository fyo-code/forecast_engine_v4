"""Focused engine tests (audit C4.2). The leakage shift-test is the important one:
predictions at a cutoff must be identical when all data after the cutoff is deleted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fev4 import calibration as cal
from fev4 import config, families, interpretable_model as im, kill_list, module_replica, rug_panel


# --------------------------------------------------------------------------- #
# families
# --------------------------------------------------------------------------- #
def test_parse_family_standard_name():
    fam, outdoor, status = families.parse_family("COVOR KAVYA 080x300cm RED")
    assert (fam, outdoor, status) == ("KAVYA", False, "ok")


def test_parse_family_outdoor_and_ldim():
    fam, outdoor, status = families.parse_family("COVOR DE EXTERIOR MENZY 200x290cm 5001 BROWN")
    assert (fam, outdoor, status) == ("MENZY", True, "ok")
    fam2, _, _ = families.parse_family("COVOR CRYSTAL L.170 l.120 Beige")
    assert fam2 == "CRYSTAL"


def test_code_prefix_strips_trailing_size():
    assert families.code_prefix("DKTABOOANTH120") == "DKTABOOANTH"
    assert families.code_prefix("DKNAZARE200R") == "DKNAZARE"
    assert families.code_prefix("ABC") is None  # too short after strip / no digits


# --------------------------------------------------------------------------- #
# calibration
# --------------------------------------------------------------------------- #
def _qframe(n=400, seed=0):
    rng = np.random.default_rng(seed)
    p50 = rng.uniform(1, 5, n)
    return pd.DataFrame({"p50": p50, "p90": p50 * 2, "p95": p50 * 2.5})


def test_apply_spread_keeps_center_and_nonnegative():
    qf = _qframe()
    out = cal.apply_spread(qf, 0.5)
    assert np.allclose(out["p50"], qf["p50"])
    assert (out["p90"] < qf["p90"]).all() and (out.values >= 0).all()


def test_fit_spread_moves_coverage_toward_target():
    qf = _qframe()
    rng = np.random.default_rng(1)
    actual = qf["p50"].to_numpy() * rng.lognormal(0, 0.8, len(qf))  # too-wide truth
    s = cal.fit_spread(actual, qf, target_col="p90", target=0.90)
    cov = cal.coverage(actual, cal.apply_spread(qf, s)["p90"].to_numpy())
    assert abs(cov - 0.90) <= abs(cal.coverage(actual, qf["p90"].to_numpy()) - 0.90) + 1e-9


def test_fit_segmented_small_segment_defaults_to_identity():
    qf = _qframe(60)
    lam = np.full(60, 5.0)  # all movers -> sparse segment has <50 rows
    params = cal.fit_segmented(qf["p50"].to_numpy(), qf, lam)
    assert params["sparse"] == (1.0, 1.0)


# --------------------------------------------------------------------------- #
# module replica — the blind spot is reproduced on purpose
# --------------------------------------------------------------------------- #
def _monthly_toy():
    months = pd.date_range("2024-01-01", "2024-06-01", freq="MS")
    rows = []
    for mth in months:
        # SKU A: sells 5/month, low stock -> should be urgent; SKU B: stock, zero sales
        rows.append(dict(sku_id="A", store_code="S", month_start=mth, units=5.0,
                         stock_eom=2.0, stock_start=2.0, value=100.0))
        rows.append(dict(sku_id="B", store_code="S", month_start=mth, units=0.0,
                         stock_eom=9.0, stock_start=9.0, value=0.0))
    return pd.DataFrame(rows)


def test_replica_flags_low_stock_mover_and_misses_zero_seller():
    out = module_replica.decide(_monthly_toy(), pd.Timestamp("2024-06-01"))
    a = out[out["sku_id"] == "A"].iloc[0]
    b = out[out["sku_id"] == "B"].iloc[0]
    assert a["urgency"] in ("critical", "urgent") and a["order_qty"] >= 5
    assert np.isinf(b["months_of_stock"]) and b["urgency"] == "ok" and b["order_qty"] == 0


# --------------------------------------------------------------------------- #
# kill list — rate=0 handled, dead ranked by trapped value
# --------------------------------------------------------------------------- #
def test_kill_list_zero_seller_is_dead_with_infinite_cover():
    weeks = pd.date_range("2024-01-01", "2025-06-30", freq="W-MON")
    weekly = pd.DataFrame({
        "sku_id": "B", "store_code": "CONSTANTA", "week_start": weeks,
        "gross_units": 0.0, "gross_value": 0.0,
    })
    monthly = pd.DataFrame({
        "sku_id": "B", "store_code": "CONSTANTA",
        "month_start": [pd.Timestamp("2025-06-01")], "stock_eom": [7.0],
        "stock_start": [7.0], "units": [0.0], "value": [0.0],
    })
    fam = pd.DataFrame({"sku_id": ["B"], "family": ["F"]})
    kl = kill_list.build(weekly, monthly, fam, pd.Timestamp("2025-06-30"), ["CONSTANTA"])
    assert len(kl) == 1
    row = kl.iloc[0]
    assert row["klass"] == "dead" and np.isinf(row["cover_months"]) and row["stock"] == 7.0


# --------------------------------------------------------------------------- #
# panel + model on real data (integration; skipped if artifacts missing)
# --------------------------------------------------------------------------- #
needs_data = pytest.mark.skipif(
    not (config.cohort_paths(config.RUGS_SLUG)["weekly"]).exists(),
    reason="rug artifacts not built",
)


@pytest.fixture(scope="module")
def real_panel():
    return rug_panel.weekly_panel(["CONSTANTA"])


@needs_data
def test_grid_is_calendar_complete_within_span(real_panel):
    g = real_panel.groupby(["sku_id", "store_code"])["week_start"]
    span_weeks = ((g.max() - g.min()).dt.days // 7 + 1)
    assert (g.count() == span_weeks).all()


@needs_data
def test_quantiles_monotone_and_zero_lam_zero(real_panel):
    fam = pd.read_parquet(config.cohort_paths(config.RUGS_SLUG)["dir"] / "sku_families.parquet")
    pred = im.predict(real_panel, fam, pd.Timestamp("2025-06-02"), 4.0, ["CONSTANTA"])
    assert (pred["p50"] <= pred["p90"]).all() and (pred["p90"] <= pred["p95"]).all()
    zero = pred[pred["lam"] <= 1e-9]
    if len(zero):
        assert (zero[["p50", "p90", "p95"]].to_numpy() == 0).all()


@needs_data
def test_LEAKAGE_shift_predictions_unchanged_without_future(real_panel):
    """THE leakage test: deleting all data after the cutoff must not change predictions."""
    fam = pd.read_parquet(config.cohort_paths(config.RUGS_SLUG)["dir"] / "sku_families.parquet")
    cutoff = pd.Timestamp("2025-03-03")
    full = im.predict(real_panel, fam, cutoff, 4.0, ["CONSTANTA"])
    truncated_panel = real_panel[real_panel["week_start"] <= cutoff].copy()
    trunc = im.predict(truncated_panel, fam, cutoff, 4.0, ["CONSTANTA"])
    m = full.merge(trunc, on=["sku_id", "store_code"], suffixes=("_f", "_t"))
    assert len(m) == len(full) == len(trunc)
    for col in ("lam", "p50", "p90", "p95", "pooled_rate", "season", "trend"):
        assert np.allclose(m[f"{col}_f"], m[f"{col}_t"], atol=1e-9), f"leak via {col}"


# --------------------------------------------------------------------------- #
# order math
# --------------------------------------------------------------------------- #
def test_integer_orders_nonnegative():
    from fev4.backtest_rugs import _orders_from_quantiles
    qf = pd.DataFrame({"p90": [0.2, 3.4, 0.0], "p95": [1.0, 5.0, 0.0]})
    orders = _orders_from_quantiles(qf, np.array([5.0, 1.0, 0.0]))
    assert orders["p90"].tolist() == [0, 3, 0]
    assert orders["p95"].tolist() == [0, 4, 0]
    assert (orders["p90"] >= 0).all()
