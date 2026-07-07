"""Forecast V4 — Phase 4: build everything the demo app shows.

Direction (from the PM/V requested spec, `procurement_tool_V.md`): adopt THEIR
decision framework — the 5-segment Days-of-Cover dashboard (Critical/Urgent/
Attention/OK/Overstock), Lead-Time/Safety-Stock anchoring, Rocket velocity
alerts, Order Builder, dual calendars — and replace the demand rate feeding it.

Their rate:  average daily sales = last 4 months / 120 days   (flat, backward)
Our rate:    calibrated forward demand (season/trend/family-pooled, NB quantiles)

Everything else downstream (segments, alerts, order builder) keeps their exact
formulas, so the PM sees the tool he asked for — with a better brain.

Outputs to data/rugs_v1/demo/:
- dashboard.parquet        one row per SKU x store: both segmentations, risk %,
                           rocket flag, order suggestion, kill class, because.
- weekly_history.parquet   drill-down series (weekly sales, 2 stores of years).
- monthly_stock.parquet    drill-down stock series.
- warning_quality.json     the framework head-to-head: whose warnings were right.
- meta.json                as-of date, params, calibration values (transparency).

Run: python -m fev4.demo_data
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats

from . import calibration as cal
from . import config, interpretable_model as im, kill_list, rug_panel

PATHS = config.cohort_paths(config.RUGS_SLUG)
DEMO = PATHS["dir"] / "demo"
STORES = list(config.STORE_STOCK_FILES)
QCOLS = ["p50", "p90", "p95"]

# defaults from the PM spec example (Lead Time 30d, Safety Stock 7d); configurable in-app
LEAD_TIME_DAYS = 30
SAFETY_DAYS = 7
ROCKET_RATIO = 1.20         # their spec: >=20% above recent average
OVERSTOCK_DAYS = 90
# A 0-store-stock rug still sells (central/to-order; ACTIV=Da), so "0 days cover"
# is not an emergency by itself. Only flag critical/urgent when forward demand is
# material — otherwise the alert list fills with trickle-sellers. See FINDINGS §5.
MIN_MATERIAL_MONTHLY = 0.5  # >= ~6 units/year to qualify as a genuine shortage risk


# --------------------------------------------------------------------------- #
# Their framework (exact formulas from procurement_tool_V.md)
# --------------------------------------------------------------------------- #
def segment_days_cover(days_cover: np.ndarray, lt: float = LEAD_TIME_DAYS,
                       ss: float = SAFETY_DAYS) -> np.ndarray:
    return np.select(
        [days_cover < lt,
         days_cover < lt + ss,
         days_cover < lt + ss + 14,
         days_cover <= OVERSTOCK_DAYS],
        ["critical", "urgent", "attention", "ok"], default="overstock",
    )


def flat_daily_rate(panel: pd.DataFrame, cutoff: pd.Timestamp) -> pd.Series:
    """Their rate: last 4 months of sales / 120 days, per SKU x store."""
    win = panel[(panel["week_start"] < cutoff)
                & (panel["week_start"] >= cutoff - pd.Timedelta(days=120))]
    return win.groupby(["sku_id", "store_code"])["gross_units"].sum() / 120.0


# --------------------------------------------------------------------------- #
# Our engine wrapped for the framework
# --------------------------------------------------------------------------- #
def our_forward(panel, fam, cutoff, horizon_days, params) -> pd.DataFrame:
    """Calibrated forward demand over `horizon_days`, plus P(stockout | stock)."""
    wk = horizon_days / 7.0
    pred = im.predict(panel, fam, cutoff, wk, STORES)
    pred[QCOLS] = cal.apply_segmented(pred[QCOLS], pred["lam"].to_numpy(), params)
    # calibrated center for the rate: scale lam by the fitted center multiplier
    seg = cal.segment_of(pred["lam"].to_numpy())
    center = np.where(seg == "mover", params["mover"][0], params["sparse"][0])
    pred["lam_cal"] = pred["lam"] * center
    return pred


def stockout_risk(lam_cal: np.ndarray, stock: np.ndarray, phi: float = im.PHI) -> np.ndarray:
    """P(demand over lead time > stock) under the calibrated NB."""
    lam = np.maximum(lam_cal, 1e-9)
    n = lam / (phi - 1.0)
    p = 1.0 / phi
    risk = 1.0 - stats.nbinom.cdf(np.maximum(stock, 0.0), n, p)
    return np.where(lam_cal <= 1e-6, 0.0, risk)


# --------------------------------------------------------------------------- #
# Warning-quality head-to-head (their rate vs ours, inside THEIR framework)
# --------------------------------------------------------------------------- #
def warning_quality(panel, monthly, fam) -> dict:
    """Monthly replay 2024-2025: same framework, same thresholds, same stock —
    only the demand rate differs. Who warned right?

    should_warn = demand over the next LEAD_TIME window exceeds starting stock
    warned      = segment in (critical, urgent)
    """
    fit_end = pd.Timestamp("2023-12-01")
    fitc = pd.date_range("2023-01-01", fit_end, freq="MS")
    frames = []
    for c in fitc:
        wkw = (c + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1) - c).days / 7.0
        p = im.predict(panel, fam, c, wkw, STORES)
        a = im.actual_window_demand(panel, c, wkw, STORES)
        frames.append(p.merge(a, on=["sku_id", "store_code"], how="left").fillna({"actual": 0}))
    fit = pd.concat(frames, ignore_index=True)
    params = cal.fit_segmented(fit["actual"].to_numpy(), fit[QCOLS], fit["lam"].to_numpy())

    rows = []
    for c in pd.date_range("2024-01-01", "2025-11-01", freq="MS"):
        month = monthly[monthly["month_start"] == c].dropna(subset=["stock_start"])
        if month.empty:
            continue
        ours = our_forward(panel, fam, c, LEAD_TIME_DAYS + SAFETY_DAYS, params)
        ours["rate_ours"] = ours["lam_cal"] / (LEAD_TIME_DAYS + SAFETY_DAYS)
        theirs = flat_daily_rate(panel, c).rename("rate_theirs")
        m = month.merge(ours[["sku_id", "store_code", "rate_ours"]],
                        on=["sku_id", "store_code"], how="left")
        m = m.join(theirs, on=["sku_id", "store_code"])
        m[["rate_ours", "rate_theirs"]] = m[["rate_ours", "rate_theirs"]].fillna(0.0)
        # outcome: demand over next ~lead-time (next month at this grain) vs starting stock
        m["should_warn"] = m["units"] > m["stock_start"]
        for who in ("ours", "theirs"):
            rate = m[f"rate_{who}"].to_numpy()
            with np.errstate(divide="ignore", invalid="ignore"):
                cover = np.where(rate > 0, m["stock_start"].to_numpy() / rate, np.inf)
            m[f"warn_{who}"] = np.isin(segment_days_cover(cover), ["critical", "urgent"])
        m["cutoff"] = c
        rows.append(m)
    df = pd.concat(rows, ignore_index=True)

    out: dict = {"months": int(df["cutoff"].nunique()), "rows": int(len(df)),
                 "should_warn_rows": int(df["should_warn"].sum())}
    for who in ("ours", "theirs"):
        w, s = df[f"warn_{who}"], df["should_warn"]
        out[who] = {
            "warnings_issued": int(w.sum()),
            "recall_of_real_shortfalls": round(float((w & s).sum() / max(s.sum(), 1)), 3),
            "precision_of_warnings": round(float((w & s).sum() / max(w.sum(), 1)), 3),
        }
    # disagreements with outcomes (for the app's disagreement table)
    dis = df[df["warn_ours"] != df["warn_theirs"]].copy()
    out["disagreements"] = {
        "rows": int(len(dis)),
        "ours_warned_theirs_silent": int((dis["warn_ours"]).sum()),
        "ours_right_share_when_we_warned": round(float(
            dis.loc[dis["warn_ours"], "should_warn"].mean()), 3) if dis["warn_ours"].any() else None,
        "theirs_right_share_when_they_warned": round(float(
            dis.loc[dis["warn_theirs"], "should_warn"].mean()), 3) if dis["warn_theirs"].any() else None,
    }
    dis_sample = dis[dis["warn_ours"] & dis["should_warn"]].nlargest(30, "units")[
        ["cutoff", "sku_id", "store_code", "stock_start", "units", "rate_ours", "rate_theirs"]
    ]
    dis_sample.to_parquet(DEMO / "disagreements_sample.parquet", index=False)
    return out, params


# --------------------------------------------------------------------------- #
# The live dashboard build
# --------------------------------------------------------------------------- #
def build() -> dict:
    DEMO.mkdir(parents=True, exist_ok=True)
    panel = rug_panel.weekly_panel()
    monthly = rug_panel.monthly_panel()
    fam = pd.read_parquet(PATHS["dir"] / "sku_families.parquet")
    attrs = pd.read_parquet(PATHS["sku_attr"]).rename(columns={"sku": "sku_id"})
    now = panel["week_start"].max() + pd.Timedelta(weeks=1)

    wq, params = warning_quality(panel, monthly, fam)

    # live forward demand at the two horizons
    horizon = LEAD_TIME_DAYS + SAFETY_DAYS
    live = our_forward(panel, fam, now, horizon, params)
    lt_pred = our_forward(panel, fam, now, LEAD_TIME_DAYS, params)[
        ["sku_id", "store_code", "lam_cal"]
    ].rename(columns={"lam_cal": "lam_lt"})
    live = live.merge(lt_pred, on=["sku_id", "store_code"], how="left")

    stock_idx = monthly.groupby(["sku_id", "store_code"])["month_start"].idxmax()
    stock = monthly.loc[stock_idx, ["sku_id", "store_code", "month_start", "stock_eom"]]
    stock = stock.rename(columns={"stock_eom": "stock", "month_start": "stock_as_of"})
    # universe = actively-selling SKUs UNION everything still holding stock (the dead
    # stock rows are the Overstock/kill value — they must appear on the dashboard)
    d = live.merge(stock, on=["sku_id", "store_code"], how="outer")
    d = d[(d["lam"].fillna(0) > 0) | (d["stock"].fillna(0) > 0)].reset_index(drop=True)
    d["stock"] = d["stock"].fillna(0.0)
    for col in ("lam", "lam_cal", "lam_lt", "p50", "p90", "p95", "roll4", "roll13",
                "pooled_rate", "weeks_since_sale"):
        d[col] = d[col].fillna(0.0)
    d["season"] = d["season"].fillna(1.0)
    d["trend"] = d["trend"].fillna(1.0)

    # both rates & segmentations (their exact formulas; transit unknown -> 0, flagged)
    theirs = flat_daily_rate(panel, now).rename("rate_theirs")
    d = d.join(theirs, on=["sku_id", "store_code"])
    d["rate_theirs"] = d["rate_theirs"].fillna(0.0)
    d["rate_ours"] = d["lam_cal"] / horizon
    for who in ("ours", "theirs"):
        rate = d[f"rate_{who}"].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            cover = np.where(rate > 0, d["stock"].to_numpy() / rate, np.inf)
        d[f"days_cover_{who}"] = np.round(cover, 1)
        seg = segment_days_cover(cover)
        # materiality gate: a trickle-seller at 0 stock is a slow reorder, not a fire
        immaterial = (rate * 30.0) < MIN_MATERIAL_MONTHLY
        seg = np.where(immaterial & np.isin(seg, ["critical", "urgent"]), "attention", seg)
        d[f"segment_{who}"] = seg
    d["stockout_risk_pct"] = np.round(100 * stockout_risk(d["lam_lt"].fillna(0).to_numpy(),
                                                          d["stock"].to_numpy()), 1)
    # Rocket (their spec: velocity >= 20% above recent average)
    d["rocket"] = (d["roll13"] > 0.05) & ((d["roll4"] / d["roll13"].replace(0, np.nan)) >= ROCKET_RATIO)
    d["rocket"] = d["rocket"].fillna(False)

    # order suggestion (our engine; MOQ applied in-app)
    d["order_qty"] = np.maximum(0, np.ceil(d["p90"]) - d["stock"]).astype(int)

    # kill-list join (root cause for Overstock — their spec asks WHY)
    kl = kill_list.build(panel[panel["store_code"].isin(STORES)], monthly, fam, now, STORES)
    d = d.merge(kl[["sku_id", "store_code", "klass", "cover_months", "trapped_value",
                    "weeks_since_sale"]].rename(columns={"weeks_since_sale": "wss_kill"}),
                on=["sku_id", "store_code"], how="left")
    d["klass"] = d["klass"].fillna("active")

    d = d.merge(attrs[["sku_id", "denumire_articol", "dimensiuni", "furnizor"]],
                on="sku_id", how="left")
    d = d.merge(fam[["sku_id", "family"]].drop_duplicates("sku_id"),
                on="sku_id", how="left", suffixes=("", "_f"))

    def because(r) -> str:
        bits = []
        bits.append(f"sells ~{r['rate_ours'] * 30:.1f}/mo forward" if r["rate_ours"] > 0.003
                    else "no forward demand expected")
        if abs(r["season"] - 1) >= 0.10:
            bits.append(f"season {'+' if r['season'] > 1 else ''}{(r['season'] - 1) * 100:.0f}%")
        if r["rocket"]:
            bits.append("ROCKET: accelerating >=20%")
        elif abs(r["trend"] - 1) >= 0.08:
            bits.append(f"trend {'up' if r['trend'] > 1 else 'down'}")
        bits.append(f"{int(r['stock'])} on hand")
        if r["order_qty"] > 0:
            bits.append(f"P90 need {r['p90']:.0f} over {horizon}d -> order {int(r['order_qty'])}")
        return "; ".join(bits)

    d["because"] = d.apply(because, axis=1)

    # months since last sale (for honest dead-stock language: "no sale in N months")
    d["months_since_sale"] = (d["wss_kill"].fillna(d["weeks_since_sale"]).fillna(0) / 4.345).round(0)

    # one plain-language recommendation per row + a numeric priority for sorting,
    # so the PM sees WHAT TO DO without decoding five columns.
    seg = d["segment_ours"]; killc = d["klass"]; oq = d["order_qty"]
    action = np.select(
        [(seg.isin(["critical", "urgent"])) & (oq > 0),
         d["rocket"] & (oq > 0),
         killc == "dead",
         killc == "dying",
         oq > 0,
         (seg == "overstock") & (d["stock"] > 0)],
        ["Reorder now", "Reorder — accelerating", "Stop & clear", "Stop reordering",
         "Reorder", "Overstocked — hold"],
        default="OK — no action",
    )
    d["action"] = action
    d["order_action_qty"] = np.where(np.char.find(action.astype(str), "Reorder") >= 0, oq, 0)
    seg_rank = {"critical": 0, "urgent": 1, "attention": 2, "ok": 3, "overstock": 4}
    d["priority"] = (seg.map(seg_rank).fillna(5).astype(int) * 1_000_000
                     - d["trapped_value"].fillna(0).astype(int).clip(lower=0))
    d.to_parquet(DEMO / "dashboard.parquet", index=False)

    # drill-down series
    wk = panel[panel["store_code"].isin(STORES)][
        ["sku_id", "store_code", "week_start", "gross_units"]]
    wk[wk["week_start"] >= now - pd.Timedelta(weeks=110)].to_parquet(
        DEMO / "weekly_history.parquet", index=False)
    monthly[["sku_id", "store_code", "month_start", "stock_eom", "units"]].to_parquet(
        DEMO / "monthly_stock.parquet", index=False)

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": str(now.date()),
        "lead_time_days": LEAD_TIME_DAYS, "safety_days": SAFETY_DAYS,
        "rocket_ratio": ROCKET_RATIO, "overstock_days": OVERSTOCK_DAYS,
        "calibration_params": {k: list(v) for k, v in params.items()},
        "phi": im.PHI, "shrink_k": im.SHRINK_K,
        "warning_quality": wq,
        "segment_counts_ours": d["segment_ours"].value_counts().to_dict(),
        "segment_counts_theirs": d["segment_theirs"].value_counts().to_dict(),
        "kill_totals": {"sku_stores": int((d["klass"] != "active").sum()),
                        "trapped_lei": round(float(d["trapped_value"].fillna(0).sum()), 0)},
        "notes": [
            "transit stock (Stoc Tranzit) not in available data -> assumed 0; add when export exists",
            "product state codes (ACU/RPD/COM/OUC/WWW) not in available data -> filter disabled",
            "stock snapshot is monthly EOM -> staleness shown as stock_as_of",
            "IMPORTANT: new-store stock extends to Jun 2026 but SALES data ends Dec 2025 -> "
            "forward rates + kill-list recency are computed at the sales edge; a 2026 sales "
            "export is the next refresh that removes this gap",
        ],
    }
    (DEMO / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    meta = build()
    print("Forecast V4 — demo data built")
    print(f"  as of {meta['as_of']} | segments (ours): {meta['segment_counts_ours']}")
    print(f"  kill: {meta['kill_totals']}")
    wq = meta["warning_quality"]
    print(f"  warning quality ({wq['months']} months, {wq['rows']:,} rows, "
          f"{wq['should_warn_rows']:,} real shortfalls):")
    for who in ("ours", "theirs"):
        w = wq[who]
        print(f"    {who:7s} recall {w['recall_of_real_shortfalls']:.0%} | "
              f"precision {w['precision_of_warnings']:.0%} | issued {w['warnings_issued']:,}")
    print(f"  disagreements: {wq['disagreements']}")


if __name__ == "__main__":
    main()
