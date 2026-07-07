"""Forecast V4 — the rug replenishment copilot (demo app).

The PM's requested framework (procurement_tool_V.md) — Days-of-Cover segments,
Lead Time / Safety Stock / MOQ, Rocket alerts, Order Builder, dual calendars —
powered by the V4 calibrated demand engine instead of the flat 120-day average.

Design goal (2026-07 rework): a PM opens this and immediately sees WHAT TO DO,
WHAT THE STOCK IS, and WHY — in one legible, fully-visible table, not a wall of
numbers. Functionality over polish; nothing hidden.

Run: streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

DATA = Path(__file__).resolve().parents[1] / "data" / "rugs_v1" / "demo"
BT = Path(__file__).resolve().parents[1] / "data" / "rugs_v1"
SEG_EMOJI = {"critical": "🔴", "urgent": "🟠", "attention": "🟡", "ok": "🟢", "overstock": "🟣"}
SEG_ORDER = ["critical", "urgent", "attention", "ok", "overstock"]
SEG_LABEL = {"critical": "🔴 Critical", "urgent": "🟠 Urgent", "attention": "🟡 Attention",
             "ok": "🟢 OK", "overstock": "🟣 Overstock"}
MIN_MATERIAL_MONTHLY = 0.5  # must match fev4/demo_data.py

st.set_page_config(page_title="Stockly — Rug Replenishment Copilot", layout="wide")


@st.cache_data
def load():
    d = pd.read_parquet(DATA / "dashboard.parquet")
    wk = pd.read_parquet(DATA / "weekly_history.parquet")
    ms = pd.read_parquet(DATA / "monthly_stock.parquet")
    meta = json.loads((DATA / "meta.json").read_text())
    dis = pd.read_parquet(DATA / "disagreements_sample.parquet")
    bt = json.loads((BT / "backtest_2026_metrics.json").read_text()) if (BT / "backtest_2026_metrics.json").exists() else None
    btv = json.loads((BT / "backtest_2026_value.json").read_text()) if (BT / "backtest_2026_value.json").exists() else None
    return d, wk, ms, meta, dis, bt, btv


d0, WK, MS, META, DIS, BT2026, BTVAL = load()
d0 = d0.copy()
d0["name"] = d0["denumire_articol"].fillna(d0["sku_id"])  # never show a blank product

# ---------------- sidebar: navigation + PM-configurable parameters --------------
st.sidebar.title("📦 Stockly")
st.sidebar.caption(f"Rug replenishment · {d0['store_code'].nunique()} stores · sales through {META['as_of']}")
page = st.sidebar.radio("View", [
    "🎯 Action center", "📋 All stock (full table)", "🗑️ Dead stock & trapped cash",
    "✅ Proof it works", "🔍 How it works",
])
st.sidebar.divider()
st.sidebar.subheader("Settings")
LT = st.sidebar.number_input("Lead time (days)", 5, 120, META["lead_time_days"],
                             help="How long a reorder takes to arrive. Drives the urgency thresholds.")
SS = st.sidebar.number_input("Safety stock (days)", 0, 60, META["safety_days"],
                             help="Extra buffer days on top of lead time.")
MOQ = st.sidebar.number_input("Min order qty (MOQ)", 1, 50, 1,
                              help="Smallest quantity a supplier will ship. Orders below it get bumped up.")
SERVICE = st.sidebar.select_slider(
    "Order confidence", options=["p50", "p90", "p95"], value="p90",
    help="How much buffer to order. p50 = lean (order the expected amount, less cash tied up). "
         "p90 = balanced. p95 = safe (rarely short, more cash tied up). Rugs sell even at 0 store "
         "stock, so higher isn't automatically better — it just traps more cash.")
stores = st.sidebar.multiselect("Stores", sorted(d0["store_code"].unique()),
                                sorted(d0["store_code"].unique()))
st.sidebar.divider()
st.sidebar.caption("Transit stock assumed 0 (no export yet). Product-state filter "
                   "(ACU/RPD/COM/OUC/WWW) disabled — field not in current data.")


def recompute(df: pd.DataFrame) -> pd.DataFrame:
    """Live recompute with the sidebar settings — segments (with the materiality
    gate), order quantities, and the plain-language action. Keeps the app in
    lock-step with fev4/demo_data.py so nothing shown here can drift from it."""
    df = df[df["store_code"].isin(stores)].copy()
    for who in ("ours", "theirs"):
        rate = df[f"rate_{who}"].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            cover = np.where(rate > 0, df["stock"].to_numpy() / rate, np.inf)
        df[f"days_cover_{who}"] = np.round(cover, 1)
        seg = np.select([cover < LT, cover < LT + SS, cover < LT + SS + 14, cover <= 90],
                        ["critical", "urgent", "attention", "ok"], "overstock")
        # a 0-stock trickle-seller is a slow reorder, not a fire (stock != sellability)
        immaterial = (rate * 30.0) < MIN_MATERIAL_MONTHLY
        df[f"segment_{who}"] = np.where(immaterial & np.isin(seg, ["critical", "urgent"]),
                                        "attention", seg)
    q = df[SERVICE].to_numpy()
    order = np.maximum(0, np.ceil(q) - df["stock"].to_numpy())
    order = np.where((order > 0) & (order < MOQ), MOQ, order).astype(int)
    df["order_qty"] = order
    seg = df["segment_ours"]
    df["action"] = np.select(
        [(seg.isin(["critical", "urgent"])) & (order > 0),
         df["rocket"] & (order > 0),
         df["klass"] == "dead", df["klass"] == "dying",
         order > 0, (seg == "overstock") & (df["stock"] > 0)],
        ["Reorder now", "Reorder — accelerating", "Stop & clear", "Stop reordering",
         "Reorder", "Overstocked — hold"], default="OK — no action")
    df["sells_per_mo"] = (df["rate_ours"] * 30).round(2)
    return df


D = recompute(d0)

# columns shown in the main tables + how to render them legibly
MAIN_COLS = ["action", "name", "store_code", "stock", "sells_per_mo",
             "days_cover_ours", "segment_ours", "order_qty", "months_since_sale", "because"]
COLCFG = {
    "action": st.column_config.TextColumn("Action", width="medium",
              help="What the engine recommends you do."),
    "name": st.column_config.TextColumn("Product", width="large"),
    "store_code": st.column_config.TextColumn("Store", width="small"),
    "stock": st.column_config.NumberColumn("On hand", format="%d", width="small",
             help="Units physically in the store now (monthly snapshot)."),
    "sells_per_mo": st.column_config.NumberColumn("Forecast /mo", format="%.2f", width="small",
                    help="Engine's forward monthly sell rate (season + trend + family-pooled)."),
    "days_cover_ours": st.column_config.NumberColumn("Days cover", format="%d", width="small",
                       help="How many days the on-hand stock lasts at the forecast rate. ∞ = not selling."),
    "segment_ours": st.column_config.TextColumn("Status", width="small",
                    help="🔴 Critical <lead time · 🟠 Urgent · 🟡 Attention · 🟢 OK · 🟣 Overstock >90d"),
    "order_qty": st.column_config.NumberColumn("Order", format="%d", width="small",
                 help="Suggested reorder quantity at the chosen order-confidence level."),
    "months_since_sale": st.column_config.NumberColumn("Idle (mo)", format="%d", width="small",
                         help="Months since this SKU last sold in this store."),
    "because": st.column_config.TextColumn("Why", width="large",
               help="Plain-language reason behind the recommendation."),
}


def seg_pretty(s):
    return s.map(SEG_LABEL).fillna(s)


def drilldown(sku: str, store: str):
    row = D[(D["sku_id"] == sku) & (D["store_code"] == store)]
    if row.empty:
        return
    row = row.iloc[0]
    st.markdown(f"#### {row['name']}  ·  {store}  ·  `{sku}`")
    k = st.columns(5)
    k[0].metric("On hand", int(row["stock"]))
    k[1].metric("Forecast /mo", f"{row['sells_per_mo']:.2f}")
    k[2].metric("Days cover", "∞" if not np.isfinite(row["days_cover_ours"]) else int(row["days_cover_ours"]))
    k[3].metric("Suggested order", int(row["order_qty"]))
    k[4].metric("Stockout risk", f"{row['stockout_risk_pct']:.0f}%")
    left, right = st.columns([3, 2])
    with left:
        w = WK[(WK["sku_id"] == sku) & (WK["store_code"] == store)].sort_values("week_start")
        if len(w):
            st.caption("Weekly units sold — the raw demand the engine learns from")
            st.line_chart(w.set_index("week_start")["gross_units"], height=170)
            wy = w.copy(); wy["year"] = wy["week_start"].dt.year
            wy["woy"] = wy["week_start"].dt.isocalendar().week.astype(int)
            years = sorted(wy["year"].unique())[-2:]
            if len(years) == 2:
                piv = wy[wy["year"].isin(years)].pivot_table(
                    index="woy", columns="year", values="gross_units", aggfunc="sum")
                st.caption(f"Same weeks, {years[0]} vs {years[1]} — is this SKU speeding up or slowing down?")
                st.line_chart(piv, height=150)
        m = MS[(MS["sku_id"] == sku) & (MS["store_code"] == store)].sort_values("month_start")
        if len(m):
            st.caption("Stock on hand, end of each month")
            st.bar_chart(m.set_index("month_start")["stock_eom"], height=130)
    with right:
        horizon = LT + SS
        st.caption("How the engine got to this number (nothing hidden)")
        st.markdown(
            f"- Recent sell rate (13-wk): **{row['roll13']:.2f}/wk**\n"
            f"- After pooling with its design family: **{row['pooled_rate']:.2f}/wk**\n"
            f"- Seasonal adjustment (this family, this month): **×{row['season']:.2f}**\n"
            f"- Trend (4-wk vs 13-wk, damped): **×{row['trend']:.2f}**\n"
            f"- → Forecast demand over {horizon}d (lead+safety): **{row['rate_ours'] * horizon:.1f} units**\n"
            f"- With {SERVICE.upper()} buffer, order up to: **{row[SERVICE]:.0f}**  →  minus **{int(row['stock'])}** on hand\n"
            f"- **Suggested order: {int(row['order_qty'])} units**")
        st.caption(f"Old module rate (flat sales/120d): {row['rate_theirs']:.3f}/day → "
                   f"cover {row['days_cover_theirs']}d → "
                   f"{SEG_LABEL.get(row['segment_theirs'], row['segment_theirs'])}")


# ============================ 🎯 ACTION CENTER =================================
if page.startswith("🎯"):
    st.title("🎯 Action center — what to do today")
    st.caption("The engine forecasts each rug's forward demand, compares it to what's on the "
               "shelf, and tells you where to act. Start at the top of the table.")

    reorder = D[D["order_qty"] > 0]
    dead = D[D["klass"].isin(["dead", "dying"])]
    k = st.columns(4)
    k[0].metric("🛒 SKUs to reorder", f"{len(reorder):,}", help="Selling faster than stock covers.")
    k[1].metric("📦 Units to order", f"{int(reorder['order_qty'].sum()):,}")
    k[2].metric("🚀 Accelerating", f"{int((D['rocket'] & (D['stock'] > 0)).sum()):,}",
                help="Selling ≥20% above their recent average — watch these.")
    k[3].metric("🧊 Cash in dead/dying stock", f"{dead['trapped_value'].fillna(0).sum():,.0f} lei",
                help="Stock that stopped or is fading — see the Dead stock tab.")

    st.subheader("Reorder list")
    st.caption("Sorted by urgency. **Order** is the suggested quantity; **Why** explains it. "
               "Nothing here has enough stock for its forecast demand.")
    if len(reorder):
        view = reorder.sort_values(["priority", "sells_per_mo"], ascending=[True, False]).copy()
        view["segment_ours"] = seg_pretty(view["segment_ours"])
        st.dataframe(view[MAIN_COLS], column_config=COLCFG, height=460, hide_index=True,
                     width="stretch")
        st.download_button("⬇️ Export reorder list (CSV)",
                           view[["sku_id", "name", "store_code", "stock", "sells_per_mo",
                                 "order_qty", "action", "because"]].to_csv(index=False),
                           "rug_reorder_list.csv", "text/csv")
    else:
        st.success("Nothing needs reordering under the current settings.")

    rockets = D[D["rocket"] & (D["stock"] > 0)]
    if len(rockets):
        with st.expander(f"🚀 Accelerating SKUs ({len(rockets)}) — selling faster than usual"):
            rv = rockets.sort_values("trend", ascending=False)
            rv["segment_ours"] = seg_pretty(rv["segment_ours"])
            st.dataframe(rv[MAIN_COLS], column_config=COLCFG, height=260, hide_index=True,
                         width="stretch")

    st.subheader("Look at one SKU")
    c1, c2 = st.columns(2)
    opts = (reorder if len(reorder) else D)
    sku = c1.selectbox("Product (SKU)", opts["sku_id"].unique()[:1000])
    stco = c2.selectbox("Store", D[D["sku_id"] == sku]["store_code"].unique())
    if sku:
        drilldown(sku, stco)

# ============================ 📋 ALL STOCK ====================================
elif page.startswith("📋"):
    st.title("📋 All stock — the full table")
    st.caption("Every product × store the engine tracks. Search, sort any column, filter by "
               "status. This is the complete picture — the Action center is just the top of it.")

    c1, c2, c3 = st.columns([2, 2, 1])
    seg_pick = c1.multiselect("Status", SEG_ORDER, default=SEG_ORDER,
                              format_func=lambda s: SEG_LABEL[s])
    act_pick = c2.multiselect("Action", sorted(D["action"].unique()), default=sorted(D["action"].unique()))
    search = c3.text_input("Search product / SKU")

    view = D[D["segment_ours"].isin(seg_pick) & D["action"].isin(act_pick)].copy()
    if search:
        m = view["name"].str.contains(search, case=False, na=False) | \
            view["sku_id"].str.contains(search, case=False, na=False)
        view = view[m]
    view = view.sort_values(["priority", "sells_per_mo"], ascending=[True, False])
    st.caption(f"Showing **{len(view):,}** of {len(D):,} positions.")
    view_disp = view.copy(); view_disp["segment_ours"] = seg_pretty(view_disp["segment_ours"])
    st.dataframe(view_disp[MAIN_COLS], column_config=COLCFG, height=620, hide_index=True,
                 width="stretch")
    st.download_button("⬇️ Export this view (CSV)", view[MAIN_COLS].to_csv(index=False),
                       "rug_stock_view.csv", "text/csv")

# ============================ 🗑️ DEAD STOCK ===================================
elif page.startswith("🗑️"):
    st.title("🗑️ Dead stock & trapped cash")
    st.caption("Cash sitting in rugs that barely sell. The days-of-cover formula can't see "
               "these — a rug with sales = 0 has infinite cover and hides. Here they rank first, "
               "biggest cash pile on top, with the reason.")
    kl = D[(D["klass"] != "active") | (D["segment_ours"] == "overstock")].copy()
    kl = kl[kl["stock"] > 0]
    kl["idle"] = np.where(kl["months_since_sale"] >= 12,
                          kl["months_since_sale"].astype(int).astype(str) + " mo (>1yr)",
                          kl["months_since_sale"].astype(int).astype(str) + " mo")
    kl["root_cause"] = np.select(
        [kl["klass"] == "dead", kl["klass"] == "dying", kl["season"] < 0.9, kl["trend"] < 0.9],
        ["Dead — no sales ≥6 mo", "Dying — sales collapsing", "Out of season", "Trend down"],
        default="Slow mover")

    truly_dead = kl[(kl["klass"] == "dead") & (kl["months_since_sale"] >= 12)]["trapped_value"].fillna(0).sum()
    total_trapped = kl["trapped_value"].fillna(0).sum()
    c = st.columns(4)
    c[0].metric("Total cash in slow/dead stock", f"{total_trapped:,.0f} lei")
    c[1].metric("Confirmed dead (>1 yr idle)", f"{truly_dead:,.0f} lei",
                help="No sale in over a year — the most defensible 'trapped' number.")
    c[2].metric("Dying (fading fast)", f"{kl[kl.klass=='dying']['trapped_value'].fillna(0).sum():,.0f} lei")
    c[3].metric("Positions", f"{len(kl):,}")
    st.caption("Honest note: ~25% of 'dead' SKUs sold a unit or two in 2026 (a down year), so "
               "'confirmed dead' above is the safe figure to quote; the rest is slow, not zero.")

    kl["segment_ours"] = seg_pretty(kl["segment_ours"])
    st.dataframe(
        kl.sort_values("trapped_value", ascending=False)[
            ["name", "store_code", "stock", "idle", "root_cause", "trapped_value"]],
        column_config={
            "name": st.column_config.TextColumn("Product", width="large"),
            "store_code": st.column_config.TextColumn("Store", width="small"),
            "stock": st.column_config.NumberColumn("On hand", format="%d", width="small"),
            "idle": st.column_config.TextColumn("Idle", width="small", help="Time since last sale."),
            "root_cause": st.column_config.TextColumn("Why it's stuck", width="medium"),
            "trapped_value": st.column_config.NumberColumn("Cash trapped (lei)", format="%.0f"),
        }, height=560, hide_index=True, width="stretch")
    st.download_button("⬇️ Export dead-stock list (CSV)", kl.to_csv(index=False),
                       "rug_dead_stock.csv", "text/csv")

# ============================ ✅ PROOF ========================================
elif page.startswith("✅"):
    st.title("✅ Proof it works — tested on 2026 you hadn't seen")
    st.caption("The engine was trained only on data up to end-2025, then asked to predict "
               "Jan–Jun 2026. We compared its predictions to what actually sold. No cherry-picking.")

    if BT2026:
        drift = BT2026["market_drift_rug_yoy_h1"]
        fz = BT2026["frozen_2025"]
        st.subheader("Was the forecast accurate?")
        m = st.columns(4)
        m[0].metric("Rug market in 2026", f"{drift:+.0%}", help="Real year-on-year change, same 7 stores. The whole market shrank.")
        m[1].metric("Forecast bias, market-adjusted", f"{BT2026['frozen_bias_after_removing_market_drift_pct']:+.0f}%",
                    help="After accounting for the market drop, the engine's total forecast was this close to reality.")
        m[2].metric("P90 accuracy (unseen)", f"{fz['P90_coverage']:.0%}",
                    help="How often actual demand landed within the engine's 'safe' estimate. Target 90%.")
        m[3].metric("Demand it explains (top 10%)", f"{fz['actual_demand_captured_by_top10pct_pred']:.0%}",
                    help="Of all demand, how much landed in the SKUs the engine ranked highest. 10% = random.")
        st.info(f"**Read this honestly.** The engine over-predicted the raw total by "
                f"{fz['bias_pct']:+.0f}% — but the market fell {drift:.0%}, and once you remove that, "
                f"it was within **{BT2026['frozen_bias_after_removing_market_drift_pct']:+.0f}%**. "
                "It nails the *total* and the *safe buffer* (calibration). It is weak at guessing "
                "*which individual rug* outsells which (top-10% capture ~15% vs 10% random) — "
                "furniture demand is genuinely lumpy, so we grade on decisions, not point accuracy.")

    if BTVAL:
        st.subheader("Was the advice worth money?")
        dk = BTVAL["dead_stock_falsekill"]
        c = st.columns(2)
        c[0].metric("Dead-stock calls that were safe",
                    f"{1 - dk['false_kill_rate']:.0%}",
                    help=f"Of {dk['dead_flagged']:,} SKUs flagged 'dead', this share truly stayed dead in 2026.")
        c[1].metric("Cash correctly flagged as trapped", f"{dk['trapped_lei_correctly_flagged']:,.0f} lei")
        st.caption("The wrongly-flagged 'dead' SKUs sold ~1–2 units in six months — technically "
                   "alive, practically not worth restocking.")

    st.divider()
    st.subheader("Also replayed across 2024–2025 (warning quality)")
    wq = META["warning_quality"]
    st.caption(f"{wq['months']} months · {wq['rows']:,} decisions · {wq['should_warn_rows']:,} real "
               "shortfalls. Same framework and stock — only the demand rate differs.")
    a, b = st.columns(2)
    a.metric("Engine — shortfalls caught", f"{wq['ours']['recall_of_real_shortfalls']:.0%}",
             f"precision {wq['ours']['precision_of_warnings']:.0%}")
    b.metric("Old flat rate — shortfalls caught", f"{wq['theirs']['recall_of_real_shortfalls']:.0%}",
             f"precision {wq['theirs']['precision_of_warnings']:.0%}")
    st.caption("Engine catches more real shortfalls at roughly double the precision — fewer false "
               "alarms, which is what makes an alert worth reading.")

# ============================ 🔍 HOW IT WORKS =================================
else:
    st.title("🔍 How it works — every rule, in the open")
    st.caption("No black box. These are the live values, read from the engine's own fitted files.")
    p = META["calibration_params"]
    st.subheader("1 · The forecast")
    st.markdown(
        "```\nforward demand = recent sell-rate × days × seasonal factor × trend\n```\n"
        f"- **recent sell-rate**: this SKU's last-13-week pace, blended with its design family's "
        "pace so rare designs still get a sensible number.\n"
        "- **seasonal factor**: how this family's month compares to its own average (only past data).\n"
        "- **trend**: last 4 weeks vs last 13, damped so one hot week doesn't overreact.\n"
        f"- **spread**: Negative Binomial (φ={META.get('phi', 3.0)}) because rug demand is lumpy, not smooth.")
    st.subheader("2 · Calibration (measured on past data, before the test)")
    st.markdown(
        f"- Fast movers: center ×**{p['mover'][0]}**, spread ×**{p['mover'][1]}**.\n"
        f"- Slow tail: center ×**{p['sparse'][0]}**, spread ×**{p['sparse'][1]}**.\n"
        "- Verified on 2026 (unseen): the P90 buffer covered 92% of actual demand.")
    st.subheader("3 · Status thresholds (your framework, kept exactly)")
    st.markdown(
        f"```\nDays of Cover = stock ÷ daily forecast rate\n"
        f"🔴 Critical  cover < {LT}d       🟠 Urgent  {LT}–{LT+SS}d\n"
        f"🟡 Attention {LT+SS}–{LT+SS+14}d   🟢 OK  {LT+SS+14}–90d   🟣 Overstock >90d\n```\n"
        f"**One deliberate change:** a rug at 0 store stock still sells (central / to-order), so a "
        f"0-stock SKU selling less than {MIN_MATERIAL_MONTHLY}/month is **not** flagged critical — "
        "it's a slow reorder, not a fire. That's why the red list is short and trustworthy.")
    st.subheader("4 · Order rule")
    st.markdown(
        f"```\norder = order-up-to({SERVICE.upper()} of demand over {LT+SS}d) − stock on hand\n```\n"
        f"You choose the confidence (**{SERVICE.upper()}** now) in the sidebar. Because a stockout "
        "isn't a lost sale here, leaner (p50) ties up less cash — the backtest showed p90 buys ~2× "
        "the stock for little extra coverage.")
    st.subheader("5 · Dead-stock rules")
    st.markdown(
        "- **Dead**: no sale in ≥26 weeks.  **Dying**: last-13-week rate < 35% of the last-52-week rate.\n"
        "- **Cash trapped** = stock × its recent average selling price.")
    st.subheader("6 · Data & honesty")
    st.markdown(
        "- Sales 2022–2026 (de-duplicated — the old P1/P2 files double-counted every sale 2–3×; "
        "that's now fixed). All 12 stores feed the seasonal patterns.\n"
        "- Stock: monthly end-of-month snapshots. Constanta / Iasi / Oradea currently lag to Dec-2025.\n"
        "- **Limitations:** " + " · ".join(META["notes"]))
