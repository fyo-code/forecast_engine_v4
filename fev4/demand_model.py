"""Forecast V4 — Phase A: probabilistic demand engine (quantile model + calibration).

Trains gradient-boosted QUANTILE regressors on leakage-safe features (see
features.py) to predict the DISTRIBUTION of weekly chain demand (P10/P50/P90/P95).
Evaluates on a temporal holdout: quantile calibration (does P90 cover ~90%?),
pinball loss, a point thermometer (Hit±30, never a gate), vs a naive baseline,
and a pooling ablation (does borrowing family signal help sparse SKUs?).

Run: python -m fev4.demand_model
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from . import config, features


def _clean_X(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return df[cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)


def fit_quantile_models(X: pd.DataFrame, y: np.ndarray, quantiles=config.QUANTILES) -> dict:
    models = {}
    for q in quantiles:
        m = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, max_iter=300, learning_rate=0.05,
            max_leaf_nodes=31, min_samples_leaf=30, l2_regularization=0.1, random_state=0,
        )
        m.fit(X, y)
        models[q] = m
    return models


def predict_quantiles(models: dict, X: pd.DataFrame) -> pd.DataFrame:
    preds = {f"p{int(q*100)}": np.clip(m.predict(X), 0.0, None) for q, m in models.items()}
    out = pd.DataFrame(preds, index=X.index)
    # enforce non-crossing quantiles (sort across columns row-wise)
    ordered = np.sort(out.to_numpy(), axis=1)
    return pd.DataFrame(ordered, columns=sorted(out.columns, key=lambda c: int(c[1:])), index=X.index)


def _pinball(y: np.ndarray, pred: np.ndarray, q: float) -> float:
    d = y - pred
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def evaluate(y: np.ndarray, qpreds: pd.DataFrame, quantiles=config.QUANTILES) -> dict:
    res = {"n": int(len(y))}
    for q in quantiles:
        col = f"p{int(q*100)}"
        res[f"coverage_{col}"] = round(float(np.mean(y <= qpreds[col].to_numpy())), 3)
        res[f"pinball_{col}"] = round(_pinball(y, qpreds[col].to_numpy(), q), 3)
    p50 = qpreds["p50"].to_numpy()
    res["mae_p50"] = round(float(np.mean(np.abs(y - p50))), 3)
    scored = y >= 4
    if scored.sum():
        res["hit30_p50"] = round(float(np.mean(np.abs(p50[scored] - y[scored]) / y[scored] <= 0.30)), 3)
    return res


def run() -> dict:
    df, cutoff = features.build_modeling_frame()
    panel = df[["sku_id", "demand_week_start", "gross_units"]].drop_duplicates()

    # cohorts as-of the cutoff (leakage-safe)
    fast = set(features.fastmover_cohort(panel, cutoff))
    model_cohort = set(features.fastmover_cohort(panel, cutoff, min_active=config.MODEL_MIN_ACTIVE_WEEKS, min_units=20))
    sparse = model_cohort - fast

    work = df[df["sku_id"].isin(model_cohort)].copy()
    train = work[work["is_train"]]
    test = work[work["is_test"]]
    y_train = train["gross_units"].to_numpy(float)

    full_cols = features.FEATURE_COLUMNS
    no_pool_cols = [c for c in full_cols if c not in features.POOLED_FEATURES]

    # main model (with pooling)
    models = fit_quantile_models(_clean_X(train, full_cols), y_train)
    test_pred = predict_quantiles(models, _clean_X(test, full_cols))

    def subset_eval(skus: set) -> dict:
        m = test["sku_id"].isin(skus).to_numpy()
        return evaluate(test["gross_units"].to_numpy(float)[m], test_pred[m])

    metrics = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "train_cutoff": str(cutoff.date()),
        "test_weeks": config.TEST_WEEKS,
        "n_fastmovers": len(fast),
        "n_model_cohort": len(model_cohort),
        "n_sparse": len(sparse),
        "fastmovers": subset_eval(fast),
        "sparse": subset_eval(sparse),
    }

    # pooling ablation on sparse SKUs (point error via P50 pinball / MAE)
    models_np = fit_quantile_models(_clean_X(train, no_pool_cols), y_train, quantiles=(0.5,))
    sp_mask = test["sku_id"].isin(sparse).to_numpy()
    y_sp = test["gross_units"].to_numpy(float)[sp_mask]
    p50_pool = test_pred["p50"].to_numpy()[sp_mask]
    p50_nopool = np.clip(models_np[0.5].predict(_clean_X(test[sp_mask], no_pool_cols)), 0, None)
    metrics["pooling_ablation_sparse"] = {
        "mae_with_pooling": round(float(np.mean(np.abs(y_sp - p50_pool))), 3),
        "mae_without_pooling": round(float(np.mean(np.abs(y_sp - p50_nopool))), 3),
    }

    # naive baseline (last-4 rolling mean as point) on fast-movers, for context
    fm_mask = test["sku_id"].isin(fast).to_numpy()
    y_fm = test["gross_units"].to_numpy(float)[fm_mask]
    naive_fm = _clean_X(test[fm_mask], ["roll_mean_4"])["roll_mean_4"].to_numpy()
    metrics["fastmovers"]["naive_mae"] = round(float(np.mean(np.abs(y_fm - naive_fm))), 3)

    # persist chain forecast for fast-movers (for Phase B/C)
    out = test[test["sku_id"].isin(fast)][["sku_id", "demand_week_start", "gross_units"]].copy()
    out = pd.concat([out.reset_index(drop=True),
                     test_pred[fm_mask].reset_index(drop=True)], axis=1)
    out.to_parquet(config.CHAIN_FORECAST_OUT, index=False)
    features.store_allocation_shares(cutoff).to_parquet(config.STORE_SHARES_OUT, index=False)
    config.DEMAND_METRICS_OUT.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main() -> None:
    m = run()
    print("Forecast V4 — Phase A: demand engine")
    print(f"  train cutoff {m['train_cutoff']} | test {m['test_weeks']}w | "
          f"fastmovers {m['n_fastmovers']} | sparse {m['n_sparse']}")
    for grp in ("fastmovers", "sparse"):
        e = m[grp]
        print(f"\n  [{grp}]  n={e['n']}")
        print(f"    calibration: P50 {e['coverage_p50']:.0%} (target 50%)  "
              f"P90 {e['coverage_p90']:.0%} (target 90%)  P95 {e['coverage_p95']:.0%} (target 95%)")
        print(f"    pinball P50 {e['pinball_p50']} P90 {e['pinball_p90']} | "
              f"MAE(P50) {e['mae_p50']} | Hit±30(P50) {e.get('hit30_p50','-')}")
    print(f"    naive MAE (fastmovers) {m['fastmovers']['naive_mae']}  vs model MAE {m['fastmovers']['mae_p50']}")
    ab = m["pooling_ablation_sparse"]
    print(f"\n  pooling ablation (sparse SKUs): MAE with {ab['mae_with_pooling']} vs without {ab['mae_without_pooling']}")
    print(f"  outputs: {config.CHAIN_FORECAST_OUT.name}, {config.STORE_SHARES_OUT.name}, {config.DEMAND_METRICS_OUT.name}")


if __name__ == "__main__":
    main()
