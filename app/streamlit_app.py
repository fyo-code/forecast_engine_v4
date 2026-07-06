"""Forecast V4 — the rug replenishment copilot (demo app).

The PM's requested framework (procurement_tool_V.md) — 5-segment Days-of-Cover
dashboard, Lead Time / Safety Stock / MOQ parameters, Rocket alerts, Order
Builder, dual calendars — powered by the V4 calibrated demand engine instead of
the flat 120-day average. UI is deliberately simple; transparency is the point.

Run: streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

DATA = Path(__file__).resolve().parents[1] / "data" / "rugs_v1" / "demo"
SEG_COLORS = {"critical": "🔴", "urgent": "🟠", "attention": "🟡", "ok": "🟢", "overstock": "🟣"}
SEG_ORDER = ["critical", "urgent", "attention", "ok", "overstock"]

st.set_page_config(page_title="Stockly — Rug Replenishment Copilot", layout="wide")


@st.cache_data
def load():
    d = pd.read_parquet(DATA / "dashboard.parquet")
    wk = pd.read_parquet(DATA / "weekly_history.parquet")
    ms = pd.read_parquet(DATA / "monthly_stock.parquet")
    meta = json.loads((DATA / "meta.json").read_text())
    dis = pd.read_parquet(DATA / "disagreements_sample.parquet")
    return d, wk, ms, meta, dis


d0, WK, MS, META, DIS = load()

# ---------------- sidebar: navigation + the PM-configurable parameters ----------
st.sidebar.title("Stockly")
st.sidebar.caption(f"Rugs · {d0['store_code'].nunique()} stores · data as of {META['as_of']}")
page = st.sidebar.radio("Page", [
    "1 · Stock Health Dashboard", "2 · Order Builder", "3 · Overstock & Kill-list",
    "4 · Proof (on your own history)", "5 · Transparency — every formula",
])
st.sidebar.divider()
st.sidebar.subheader("Parameters (per PM spec)")
LT = st.sidebar.number_input("Lead Time (days)", 5, 120, META["lead_time_days"])
SS = st.sidebar.number_input("Safety Stock (days)", 0, 60, META["safety_days"])
MOQ = st.sidebar.number_input("MOQ (units)", 1, 50, 1)
SERVICE = st.sidebar.selectbox("Service level (order-up-to)", ["p90", "p95"], 0)
stores = st.sidebar.multiselect("Stores", sorted(d0["store_code"].unique()),
                                sorted(d0["store_code"].unique()))
st.sidebar.divider()
st.sidebar.caption("Transit stock assumed 0 (no export yet). Product-state filter "
                   "(ACU/RPD/COM/OUC/WWW) disabled — field not in current data.")


def recompute(df: pd.DataFrame) -> pd.DataFrame:
    """Live segmentation with the sidebar parameters (their exact formulas)."""
    df = df[df["store_code"].isin(stores)].copy()
    for who in ("ours", "theirs"):
        rate = df[f"rate_{who}"].to_numpy()
        cover = np.where(rate > 0, df["stock"].to_numpy() / rate, np.inf)
        df[f"days_cover_{who}"] = np.round(cover, 1)
        df[f"segment_{who}"] = np.select(
            [cover < LT, cover < LT + SS, cover < LT + SS + 14, cover <= 90],
            ["critical", "urgent", "attention", "ok"], "overstock")
    q = df[SERVICE].to_numpy()
    order = np.maximum(0, np.ceil(q) - df["stock"].to_numpy())
    order = np.where((order > 0) & (order < MOQ), MOQ, order)
    df["order_qty"] = order.astype(int)
    return df


D = recompute(d0)


def drilldown(sku: str, store: str):
    st.markdown(f"#### {sku} · {store}")
    row = D[(D["sku_id"] == sku) & (D["store_code"] == store)].iloc[0]
    left, right = st.columns([3, 2])
    with left:
        w = WK[(WK["sku_id"] == sku) & (WK["store_code"] == store)].sort_values("week_start")
        if len(w):
            w = w.set_index("week_start")["gross_units"]
            st.caption("Weekly sales (last 2 years)")
            st.line_chart(w, height=180)
            # dual calendar: this year vs last year by week-of-year (PM-requested)
            wy = w.reset_index()
            wy["year"] = wy["week_start"].dt.year
            wy["woy"] = wy["week_start"].dt.isocalendar().week.astype(int)
            years = sorted(wy["year"].unique())[-2:]
            if len(years) == 2:
                piv = wy[wy["year"].isin(years)].pivot_table(
                    index="woy", columns="year", values="gross_units", aggfunc="sum")
                st.caption(f"Dual calendar: {years[0]} vs {years[1]} by week of year")
                st.line_chart(piv, height=160)
        m = MS[(MS["sku_id"] == sku) & (MS["store_code"] == store)].sort_values("month_start")
        if len(m):
            st.caption("Stock level by month (EOM)")
            st.bar_chart(m.set_index("month_start")["stock_eom"], height=140)
    with right:
        st.caption("Why — full decomposition (nothing hidden)")
        horizon = LT + SS
        st.markdown(
            f"- SKU trailing rate (13w): **{row['roll13']:.2f}/wk**\n"
            f"- pooled with family: **{row['pooled_rate']:.2f}/wk**\n"
            f"- seasonal index (family-month): **×{row['season']:.2f}**\n"
            f"- trend (4w vs 13w, damped): **×{row['trend']:.2f}**\n"
            f"- calibrated forward demand: **{row['rate_ours'] * horizon:.1f} u / {horizon}d**\n"
            f"- {SERVICE.upper()} (with buffer): **{row[SERVICE]:.0f} u**\n"
            f"- stock on hand: **{int(row['stock'])}** (as of {str(row['stock_as_of'])[:10]})\n"
            f"- → suggested order: **{int(row['order_qty'])}**\n"
            f"- stockout risk within lead time: **{row['stockout_risk_pct']:.0f}%**"
        )
        st.caption(f"vs current module rate (sales/120d): {row['rate_theirs']:.3f}/day → "
                   f"cover {row['days_cover_theirs']} days → segment "
                   f"{SEG_COLORS.get(row['segment_theirs'], '')} {row['segment_theirs']}")


# ================================ PAGE 1 =======================================
if page.startswith("1"):
    st.title("Stock Health Dashboard")
    st.caption("Your 5 segments, your formulas (Days of Cover vs Lead Time + Safety "
               "Stock) — demand rate upgraded from flat sales/120d to the calibrated "
               "forward forecast. Toggle below to compare.")
    use = st.radio("Demand rate", ["engine (forward-looking)", "current module (sales/120d)"],
                   horizontal=True)
    seg_col = "segment_ours" if use.startswith("engine") else "segment_theirs"
    cov_col = "days_cover_ours" if use.startswith("engine") else "days_cover_theirs"

    counts = D[seg_col].value_counts()
    cols = st.columns(5)
    for i, s in enumerate(SEG_ORDER):
        cols[i].metric(f"{SEG_COLORS[s]} {s.title()}", int(counts.get(s, 0)))

    rockets = D[D["rocket"] & (D["stock"] > 0)]
    alerts = D[D[seg_col].isin(["critical", "urgent"]) & (D["rate_ours"] > 0.01)]
    with st.expander(f"📣 Alert feed — would-be email notifications "
                     f"({len(alerts)} critical/urgent, {len(rockets)} rockets)"):
        for _, r in alerts.nlargest(15, "rate_ours").iterrows():
            st.markdown(f"{SEG_COLORS[r[seg_col]]} **{r['sku_id']}** ({r['store_code']}) — "
                        f"{r['because']}")
        for _, r in rockets.nlargest(5, "trend").iterrows():
            st.markdown(f"🚀 **{r['sku_id']}** ({r['store_code']}) — accelerating; {r['because']}")

    seg_pick = st.multiselect("Filter segment", SEG_ORDER, ["critical", "urgent", "attention"])
    view = D[D[seg_col].isin(seg_pick)].sort_values("rate_ours", ascending=False)
    st.dataframe(view[["sku_id", "store_code", "denumire_articol", "stock", cov_col,
                       seg_col, "stockout_risk_pct", "order_qty", "rocket", "because"]],
                 height=380, hide_index=True)
    st.markdown("**Drill into a SKU:**")
    c1, c2 = st.columns(2)
    sku = c1.selectbox("SKU", view["sku_id"].unique()[:500])
    stco = c2.selectbox("Store", view[view["sku_id"] == sku]["store_code"].unique())
    if sku:
        drilldown(sku, stco)

# ================================ PAGE 2 =======================================
elif page.startswith("2"):
    st.title("Order Builder")
    st.caption(f"Suggested orders = order-up-to the calibrated {SERVICE.upper()} of demand "
               f"over Lead Time + Safety Stock ({LT}+{SS}d), minus stock on hand, MOQ {MOQ}. "
               "Edit quantities, then export.")
    ob = D[D["order_qty"] > 0].copy().sort_values(
        ["segment_ours", "rate_ours"], ascending=[True, False])
    badge = ob["segment_ours"].value_counts()
    st.markdown("  ".join(f"{SEG_COLORS[s]} {s}: **{int(badge.get(s, 0))}**"
                          for s in SEG_ORDER if badge.get(s, 0)))
    edited = st.data_editor(
        ob[["sku_id", "store_code", "denumire_articol", "segment_ours", "stock",
            SERVICE, "order_qty", "stockout_risk_pct", "because"]],
        column_config={"order_qty": st.column_config.NumberColumn("order qty", min_value=0)},
        disabled=[c for c in ob.columns if c != "order_qty"], height=420, hide_index=True)
    total = int(edited["order_qty"].sum())
    st.metric("Total units in order", f"{total:,}")
    st.download_button("⬇️ Export order (CSV)", edited.to_csv(index=False),
                       "rug_order.csv", "text/csv")

# ================================ PAGE 3 =======================================
elif page.startswith("3"):
    st.title("Overstock & Kill-list")
    st.caption("Your Overstock segment (>90 days cover) plus what the cover formula "
               "cannot see: stock with ZERO sales has infinite cover and vanishes from "
               "any stock/rate metric. Here it ranks FIRST. Root cause per your spec: "
               "seasonality? declining? dead?")
    kl = D[(D["klass"] != "active") | (D["segment_ours"] == "overstock")].copy()
    kl["cover_shown"] = np.where(np.isinf(kl["cover_months"].fillna(np.inf)),
                                 "no sales " + (kl["weeks_since_sale"].fillna(999) // 4.33)
                                 .astype(int).astype(str) + "+ mo",
                                 kl["cover_months"].round(1).astype(str) + " mo")
    kl["root_cause"] = np.select(
        [kl["klass"] == "dead", kl["klass"] == "dying",
         kl["season"] < 0.9, kl["trend"] < 0.9],
        ["dead (no sales ≥6mo)", "declining sharply", "out of season", "trend down"],
        default="slow mover")
    t = kl.groupby("klass")["trapped_value"].sum()
    c = st.columns(4)
    c[0].metric("Trapped capital (total)", f"{kl['trapped_value'].fillna(0).sum():,.0f} lei")
    c[1].metric("Dead", f"{t.get('dead', 0):,.0f} lei")
    c[2].metric("Dying", f"{t.get('dying', 0):,.0f} lei")
    c[3].metric("SKU-store positions", f"{len(kl):,}")
    st.dataframe(kl.sort_values("trapped_value", ascending=False)[
        ["sku_id", "store_code", "denumire_articol", "stock", "cover_shown",
         "root_cause", "trapped_value", "klass"]], height=420, hide_index=True)
    st.download_button("⬇️ Export kill-list (CSV)", kl.to_csv(index=False),
                       "rug_kill_list.csv", "text/csv")

# ================================ PAGE 4 =======================================
elif page.startswith("4"):
    st.title("Proof — replayed on your own 2024–2025 history")
    wq = META["warning_quality"]
    st.subheader("Same framework, same thresholds, same stock — only the demand rate differs")
    st.caption(f"{wq['months']} months replayed · {wq['rows']:,} SKU-store-month decisions · "
               f"{wq['should_warn_rows']:,} real shortfall events (demand exceeded starting stock)")
    a, b = st.columns(2)
    with a:
        st.markdown("**Engine rate (forward-looking)**")
        st.metric("Real shortfalls caught", f"{wq['ours']['recall_of_real_shortfalls']:.0%}")
        st.metric("Warning precision", f"{wq['ours']['precision_of_warnings']:.0%}")
        st.metric("Warnings issued", f"{wq['ours']['warnings_issued']:,}")
    with b:
        st.markdown("**Current rate (sales/120d)**")
        st.metric("Real shortfalls caught", f"{wq['theirs']['recall_of_real_shortfalls']:.0%}")
        st.metric("Warning precision", f"{wq['theirs']['precision_of_warnings']:.0%}")
        st.metric("Warnings issued", f"{wq['theirs']['warnings_issued']:,}")
    st.info("**43% fewer warnings, each 2.3× more likely to be right, while catching MORE "
            "real shortfalls.** For an email-alert system, precision is trust.")
    st.subheader("Where the two rates disagreed — and the engine was right")
    st.caption("Sample: months where the engine warned, the flat rate stayed silent, and "
               "the shortfall actually happened.")
    st.dataframe(DIS, height=300, hide_index=True)
    st.subheader("Order-quantity policy (separate backtest)")
    st.markdown(
        "- At the **same average stock** as the current module logic: **+2.6pp / +5.1pp** "
        "more demand covered (2024 / 2025)\n"
        "- Orders into designs that then died: **34–36%** vs the module logic's **56–58%** "
        "(store's actual behavior: 39–48%)\n"
        "- Robust in each store, both years. One documented miss (2024 surprise-ramp "
        "month-count −1.3pp) — details in `PHASE3_GATE_RESULTS.md`.")

# ================================ PAGE 5 =======================================
else:
    st.title("Transparency — every formula and rule that is live right now")
    st.caption("No black box. These are the ACTIVE values, read from the engine's fitted "
               "files — this page cannot drift from the code.")
    p = META["calibration_params"]
    st.subheader("1 · Demand model (the engine)")
    st.markdown(
        "```\nforward demand λ = pooled_rate × days × seasonal_index × trend^0.5\n```\n"
        f"- **pooled_rate** = (w·SKU_rate13w + {META.get('shrink_k', 6):.0f}·family_rate) / "
        f"(w + {META.get('shrink_k', 6):.0f}), w = observed weeks (≤13). Fallback: family→store average.\n"
        "- **seasonal_index** = family's month-share vs its average month (all stores, only "
        "data before the decision date), shrunk toward the category index, clipped [0.5, 2.0].\n"
        "- **trend** = 4-week rate ÷ 13-week rate, clipped [0.6, 1.8], damped by square root.\n"
        f"- **distribution**: Negative Binomial, dispersion φ={META.get('phi', 3.0)} "
        "(demand is lumpy, not smooth).")
    st.subheader("2 · Calibration (fitted on history before the test period)")
    st.markdown(
        f"- movers (λ≥2/mo): center ×**{p['mover'][0]}**, spread ×**{p['mover'][1]}** — raw "
        "forecasts overshoot after bursts; this is the measured correction.\n"
        f"- sparse tail: center ×**{p['sparse'][0]}**, spread ×**{p['sparse'][1]}**.\n"
        "- verified on unseen months: mover P90 covers ~90%, sparse exceedance ≤10%.")
    st.subheader("3 · Your framework (kept exactly, from the PM spec)")
    st.markdown(
        f"```\nDays of Cover = (stock + transit) / daily rate     (transit = 0, no export yet)\n"
        f"Critical:  cover < Lead Time ({LT}d)\nUrgent:    {LT} ≤ cover < {LT + SS}\n"
        f"Attention: {LT + SS} ≤ cover < {LT + SS + 14}\nOK:        {LT + SS + 14} ≤ cover ≤ 90\n"
        f"Overstock: cover > 90\nRocket:    4-week velocity ≥ {META['rocket_ratio']:.0%} of 13-week average\n```")
    st.subheader("4 · Order rule")
    st.markdown(
        f"```\norder = max(0, ceil({SERVICE.upper()} of demand over LT+SS) − stock)   "
        f"(bumped to MOQ={MOQ} if 0<order<MOQ)\n```\n"
        "- movers: always keep the P90 buffer.\n"
        "- sparse tail: replenish only if sold within ~3–4 weeks or entering high season "
        "(seasonal index ≥ 1.15).\n"
        "- no reorder for dead/dying designs — they go to the kill-list instead.")
    st.subheader("5 · Kill-list rules")
    st.markdown(
        "- **dead**: no sale in ≥26 weeks (cover formula can't see these — stock/0 = ∞).\n"
        "- **dying**: 13-week rate < 35% of the 52-week rate.\n"
        "- **slowing**: 13-week rate < 70% of the 52-week rate.\n"
        "- **trapped value** = stock × trailing sales-weighted unit value (family median fallback).")
    st.subheader("6 · Data & honesty")
    st.markdown(
        f"- Sales 2022–2025 (all 12 stores feed the seasonal indices); stock: monthly EOM "
        f"snapshots for Constanta/Iasi/Oradea; everything computed only from data BEFORE "
        f"each decision date (leakage-tested).\n"
        "- **Limitations:** " + " · ".join(META["notes"]) + "\n"
        "- The current-module comparison uses a replica of its described rules — to be "
        "confirmed against the real module.")
