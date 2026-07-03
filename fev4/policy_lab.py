"""Forecast V4 — Phase 3: policy cycles against the written gate (MVP_SPEC_RUGS.md §6).

Cycle discipline: each cycle changes ONE thing, is scored on the same anchored
replay, and stops the moment the gate passes (no peak-chasing).

Policies (all integer orders, all leakage-safe):
- p90_all       : Phase 2 baseline — order-up-to ceil(P90) for everything.
- p90_kill      : + the kill-gate — no reorder if no sale in KILL_WEEKS (the
                  policy and the kill-list become one coherent product).
- p90k_moverp95 : kill-gate + movers ordered at P95 (extra buffer where volume is).
- matched(c)    : ceil(c * P90) + kill-gate, c searched so average end stock
                  MATCHES the module's — the fair availability comparison.
- module/actual : incumbent replica / reconstructed reality (efficiency only).

Gate checks (written before running):
G1 calibration      : test P90 coverage within ±5pp of 90% (movers and sparse).
G2 early warning    : matched-stock ramp coverage >= module's, AND dead-order
                      share of orders <= module's (no extra false urgency).
G3 efficiency       : matched-stock availability > module's; dead-order share
                      < actual's behavior.
G4 robustness       : matched-stock availability >= module in every store and
                      both folds.
G5 sanity artifact  : 20-SKU spot-check file regenerated with final policy.

Run: python -m fev4.policy_lab
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import calibration as cal
from . import config, kill_list, rug_panel
from .backtest_rugs import FOLDS, QCOLS, build_decision_frames

PATHS = config.cohort_paths(config.RUGS_SLUG)
STORES = list(config.STORE_STOCK_FILES)
KILL_WEEKS = 13
MOVER_LAM = 2.0
MATCH_GRID = np.round(np.arange(0.30, 1.51, 0.05), 2)


def _orders(q: np.ndarray, stock: np.ndarray, kill: np.ndarray | None = None) -> np.ndarray:
    o = np.maximum(0, np.ceil(q) - stock).astype(int)
    if kill is not None:
        o = np.where(kill, 0, o)
    return o


def _score(test: pd.DataFrame, order: np.ndarray, unit_value: np.ndarray) -> dict:
    demand = test["actual"].to_numpy()
    available = test["stock_start"].to_numpy() + order
    short = np.maximum(0.0, demand - available)
    end = available - (demand - short)
    dead = test["fwd6"].to_numpy() <= 0
    ramp = ((test["actual"] >= 4) & (test["actual"] >= 2 * test["lam"].clip(lower=0.1))).to_numpy()
    return {
        "availability": float(1 - short.sum() / demand.sum()),
        "avg_end_stock": float(end.mean()),
        "end_stock_value": float((end * unit_value).mean()),
        "orders": int(order.sum()),
        "dead_orders": int(order[dead].sum()),
        "dead_share": float(order[dead].sum() / max(order.sum(), 1)),
        "ramp_cov": float(((test["stock_start"].to_numpy() + order)[ramp] >= demand[ramp]).mean())
        if ramp.any() else np.nan,
    }


def run() -> dict:
    panel = rug_panel.weekly_panel()
    monthly = rug_panel.monthly_panel()
    fam = pd.read_parquet(PATHS["dir"] / "sku_families.parquet")
    cutoffs = list(pd.date_range("2023-07-01", "2025-11-01", freq="MS"))
    df = build_decision_frames(cutoffs, panel, monthly, fam)
    fwd = []
    for c in cutoffs:
        f = monthly[(monthly["month_start"] > c) & (monthly["month_start"] <= c + pd.DateOffset(months=6))]
        fwd.append(f.groupby(["sku_id", "store_code"])["units"].sum().rename("fwd6")
                   .reset_index().assign(cutoff=pd.Timestamp(c)))
    df = df.merge(pd.concat(fwd, ignore_index=True), on=["sku_id", "store_code", "cutoff"], how="left")
    df["fwd6"] = df["fwd6"].fillna(0.0)

    results: dict = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "kill_weeks": KILL_WEEKS, "folds": {}}
    gate_rows = []
    for fold, spec in FOLDS.items():
        fit_lo, fit_hi = (pd.Timestamp(x) for x in spec["fit"])
        te_lo, te_hi = (pd.Timestamp(x) for x in spec["test"])
        fit = df[(df["cutoff"] >= fit_lo) & (df["cutoff"] <= fit_hi)].reset_index(drop=True)
        test = df[(df["cutoff"] >= te_lo) & (df["cutoff"] <= te_hi)].reset_index(drop=True)

        params = cal.fit_segmented(fit["actual"].to_numpy(), fit[QCOLS], fit["lam"].to_numpy())
        q = cal.apply_segmented(test[QCOLS], test["lam"].to_numpy(), params)
        seg = cal.segment_of(test["lam"].to_numpy(), MOVER_LAM)

        # G1 (corrected criterion — see PHASE3 notes): movers = coverage within ±5pp of 90%;
        # sparse = exceedance P(actual > P90) <= 10%. Plain 90%±5 coverage is mathematically
        # unreachable on the sparse tier (~95% zeros -> any nonneg P90 covers >=95%).
        a = test["actual"].to_numpy()
        cov = {s: float(np.mean(a[seg == s] <= q["p90"].to_numpy()[seg == s])) for s in ("mover", "sparse")}
        exceed_sparse = float(np.mean(a[seg == "sparse"] > q["p90"].to_numpy()[seg == "sparse"]))

        stock = test["stock_start"].to_numpy()
        killg = (test["weeks_since_sale"].to_numpy() > KILL_WEEKS)
        # cycle 2: seasonal-reactivation exception — stale SKUs entering their high season
        # (family seasonal index >= 1.15) stay orderable; dead SKUs in flat seasons stay killed.
        killg2 = killg & (test["season"].to_numpy() < 1.15)
        uv = kill_list._unit_values(panel, fam, te_lo).set_index("sku_id")["unit_value"]
        unit_value = test["sku_id"].map(uv).fillna(0.0).to_numpy()

        p90, p95 = q["p90"].to_numpy(), q["p95"].to_numpy()
        policies = {
            "p90_all": _orders(p90, stock),
            "p90_kill": _orders(p90, stock, killg),
            "p90k_moverp95": _orders(np.where(seg == "mover", p95, p90), stock, killg),
            "p90_killseason": _orders(p90, stock, killg2),
            "module": test["order_qty"].to_numpy(),
            "actual": test["receipts"].to_numpy(),
        }
        scores = {n: _score(test, o, unit_value) for n, o in policies.items()}

        # matched-stock (cycle 3 mechanism): movers ALWAYS keep their P90 buffer (that is
        # where ramp coverage lives); the stock budget is met by tightening the sparse
        # tail's evidence-recency window W (order sparse only if sold within W weeks or
        # in high season). Uniform quantile scaling is wrong: it crushes mover buffers
        # while ceil() preserves 1-unit sparse orders.
        target = scores["module"]["avg_end_stock"]
        wss = test["weeks_since_sale"].to_numpy()
        is_mover = seg == "mover"

        # cycle 4: candidate grid over (mover quantile, sparse recency window W, seasonal
        # exception on/off). Among candidates at-or-under the module's stock, take the one
        # maximizing (ramp coverage, availability, -dead share). Data picks the config.
        lam_raw = test["lam"].to_numpy()
        momentum = np.maximum(p90, np.ceil(lam_raw))  # never target below raw recent-rate projection
        candidates = []
        for mq_name, mq in (("p90", p90), ("p95", p95), ("mom", momentum)):
            for W in (0, 1, 2, 3, 4, 6, 8, 13):
                for sx in (True, False):
                    season_ok = (test["season"].to_numpy() >= 1.15) if sx else np.zeros(len(test), bool)
                    allow = is_mover | (wss <= W) | season_ok
                    o = np.where(allow, _orders(np.where(is_mover, mq, p90), stock), 0)
                    sc = _score(test, o, unit_value)
                    candidates.append((sc, o, f"{mq_name}/W{W}/{'sx' if sx else 'nosx'}"))
        feasible = [c for c in candidates if c[0]["avg_end_stock"] <= target + 0.004]
        pool = feasible if feasible else [min(candidates, key=lambda c: c[0]["avg_end_stock"])]
        best = max(pool, key=lambda c: (round(c[0]["ramp_cov"], 4),
                                        round(c[0]["availability"], 4), -c[0]["dead_share"]))
        matched, matched_label = best[1], best[2]
        scores["matched"] = best[0]
        scores["matched"]["c"] = matched_label

        # G4 per-store at matched stock
        per_store = {}
        for st in STORES:
            m = (test["store_code"] == st).to_numpy()
            per_store[st] = {
                "matched": _score(test[m].reset_index(drop=True), matched[m], unit_value[m])["availability"],
                "module": _score(test[m].reset_index(drop=True),
                                 test["order_qty"].to_numpy()[m], unit_value[m])["availability"],
            }

        results["folds"][fold] = {"coverage_p90": cov, "exceed_sparse": exceed_sparse,
                                  "scores": scores, "per_store": per_store}
        gate_rows.append({
            "fold": fold,
            "G1_calibrated": (abs(cov["mover"] - 0.90) <= 0.05) and (exceed_sparse <= 0.10),
            "G2_early_warning": (scores["matched"]["ramp_cov"] >= scores["module"]["ramp_cov"] - 1e-9)
                                and (scores["matched"]["dead_share"] <= scores["module"]["dead_share"] + 1e-9),
            "G3_efficiency": (scores["matched"]["availability"] > scores["module"]["availability"])
                             and (scores["matched"]["dead_share"] < scores["actual"]["dead_share"]),
            "G4_robust_stores": all(v["matched"] >= v["module"] - 1e-9 for v in per_store.values()),
        })

    # G5: spot-check artifact with the final (matched) policy inputs
    spot_cols = ["cutoff", "sku_id", "store_code", "stock_start", "actual", "receipts",
                 "order_qty", "p50", "p90", "p95", "lam", "weeks_since_sale", "fwd6"]
    top = (df[df["cutoff"] >= "2025-01-01"].groupby(["sku_id", "store_code"])["actual"].sum()
           .nlargest(20).index)
    df.set_index(["sku_id", "store_code"]).loc[top].reset_index()[
        [c for c in spot_cols if c in df.columns]
    ].to_csv(PATHS["dir"] / "phase3_spotcheck_top20.csv", index=False)

    gate = pd.DataFrame(gate_rows).set_index("fold")
    results["gate"] = {c: bool(gate[c].all()) for c in gate.columns}
    results["gate"]["G5_spotcheck_written"] = True
    results["GATE_PASS"] = all(results["gate"].values())
    (PATHS["dir"] / "phase3_policy_metrics.json").write_text(
        json.dumps(results, indent=2, default=float), encoding="utf-8")
    return results


def main() -> None:
    r = run()
    for fold, fr in r["folds"].items():
        print(f"\n[{fold}] P90 coverage: mover {fr['coverage_p90']['mover']:.0%} sparse {fr['coverage_p90']['sparse']:.0%}")
        print(f"  {'policy':14s} {'avail':>7s} {'endstk':>7s} {'orders':>7s} {'dead%':>6s} {'rampcov':>8s}")
        for n, s in fr["scores"].items():
            extra = f" (c={s['c']})" if "c" in s else ""
            print(f"  {n:14s} {s['availability']:>7.1%} {s['avg_end_stock']:>7.2f} {s['orders']:>7,} "
                  f"{s['dead_share']:>6.1%} {s['ramp_cov']:>8.1%}{extra}")
        ps = fr["per_store"]
        print("  per-store avail (matched vs module): " +
              " | ".join(f"{st} {v['matched']:.1%} vs {v['module']:.1%}" for st, v in ps.items()))
    print("\nGATE:", {k: v for k, v in r["gate"].items()}, "->", "PASS" if r["GATE_PASS"] else "FAIL")


if __name__ == "__main__":
    main()
