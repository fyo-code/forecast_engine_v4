/* Stockly UI v2 — app logic: tabs, filter bars, Tabulator tables, drilldown, i18n. */
"use strict";

// ---------- state ----------
let LANG = localStorage.getItem("stockly_lang") || "ro";
let TAB = "action";
const P = { lt: 30, ss: 7, moq: 1, conf: "p90", stores: null }; // shared engine params
let META = null, ALL_STORES = [];
let DASH = null, DEAD = null;          // cached last fetches
let table = null, charts = [];
const stockFilter = { status: new Set(), action: new Set(), search: "" };
let deadCardFilter = "all";
const STATUSES = ["critical", "urgent", "attention", "ok", "overstock"];

// ---------- i18n ----------
function t(k, params) {
  let s = (I18N[LANG] && I18N[LANG][k]) ?? I18N.en[k] ?? k;
  if (params) for (const [key, val] of Object.entries(params)) s = s.replaceAll(`{${key}}`, val);
  return s;
}
const nf = () => new Intl.NumberFormat(LANG === "ro" ? "ro-RO" : "en-US");
const fmtInt = (n) => (n == null ? "—" : nf().format(Math.round(n)));
const fmtLei = (n) => (n == null ? "—" : nf().format(Math.round(n)) + " lei");

// ---------- fetch ----------
function qs() {
  const p = new URLSearchParams({ lt: P.lt, ss: P.ss, moq: P.moq, conf: P.conf });
  if (P.stores && P.stores.length && P.stores.length < ALL_STORES.length) p.set("stores", P.stores.join(","));
  return p.toString();
}
async function getJSON(url) { const r = await fetch(url); return r.json(); }

// ---------- badge/format helpers ----------
function segBadge(s) {
  return `<span class="badge seg-${s}"><span class="dot"></span>${t("s_" + s)}</span>`;
}
function actCell(a, rocket) {
  const r = rocket && (a === "reorder_accel") ? "" : "";
  return `<span class="act act-${a}">${t("a_" + a)}</span>`;
}
function confCell(c) { return `<span class="cf-${c}">${t("cf_" + c)}</span>`; }
function coverCell(v) { return v == null ? '<span class="muted">∞</span>' : `<span class="num">${Math.round(v)}</span>`; }
function composeWhy(w) {
  const bits = [];
  bits.push(w.rate_mo > 0.02 ? t("why_sells", { n: w.rate_mo.toFixed(1) }) : t("why_nodemand"));
  if (Math.abs(w.season_pct) >= 10) bits.push(t("why_season", { n: (w.season_pct > 0 ? "+" : "") + w.season_pct }));
  if (w.rocket) bits.push("🚀 " + t("why_rocket"));
  else if (w.trend === "up") bits.push(t("why_trend_up"));
  else if (w.trend === "down") bits.push(t("why_trend_down"));
  bits.push(t("why_onhand", { n: w.stock }));
  if (w.order > 0) bits.push(t("why_order", { n: w.order }));
  return bits.join(" · ");
}
function csv(rows, cols) {
  const head = cols.map(c => c.title).join(",");
  const body = rows.map(r => cols.map(c => {
    let v = c.val(r); if (v == null) v = "";
    v = String(v).replace(/"/g, '""'); return /[",\n]/.test(v) ? `"${v}"` : v;
  }).join(",")).join("\n");
  return head + "\n" + body;
}
function download(name, text) {
  const b = new Blob([text], { type: "text/csv" }); const u = URL.createObjectURL(b);
  const a = document.createElement("a"); a.href = u; a.download = name; a.click(); URL.revokeObjectURL(u);
}

// ---------- shared filter bar ----------
function filterBarHTML(includeStores = true) {
  const storePills = ALL_STORES.map(s => {
    const on = !P.stores || P.stores.includes(s);
    return `<span class="pill store-pill ${on ? "on" : ""}" data-store="${s}">${s}</span>`;
  }).join("");
  return `<div class="filterbar">
    <div class="field"><label>${t("f_lead")}</label><input type="number" id="f-lt" min="5" max="120" value="${P.lt}" title="${t("f_lead_h")}"></div>
    <div class="field"><label>${t("f_safety")}</label><input type="number" id="f-ss" min="0" max="60" value="${P.ss}" title="${t("f_safety_h")}"></div>
    <div class="field"><label>${t("f_moq")}</label><input type="number" id="f-moq" min="1" max="50" value="${P.moq}" title="${t("f_moq_h")}"></div>
    <div class="field"><label>${t("f_conf")}</label><select id="f-conf" title="${t("f_conf_h")}">
      <option value="p50" ${P.conf === "p50" ? "selected" : ""}>${t("conf_p50")}</option>
      <option value="p90" ${P.conf === "p90" ? "selected" : ""}>${t("conf_p90")}</option>
      <option value="p95" ${P.conf === "p95" ? "selected" : ""}>${t("conf_p95")}</option>
    </select></div>
    ${includeStores ? `<div class="field" style="flex:1"><label>${t("f_stores")}</label><div class="pills" id="store-pills">${storePills}</div></div>` : ""}
  </div>`;
}
function wireFilterBar(onEngineChange) {
  const bind = (id, key, num) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => { P[key] = num ? +el.value : el.value; onEngineChange(); });
  };
  bind("f-lt", "lt", true); bind("f-ss", "ss", true); bind("f-moq", "moq", true); bind("f-conf", "conf", false);
  document.querySelectorAll(".store-pill").forEach(p => p.addEventListener("click", () => {
    const s = p.dataset.store;
    const cur = P.stores ? [...P.stores] : [...ALL_STORES];
    const i = cur.indexOf(s);
    if (i >= 0) { if (cur.length > 1) cur.splice(i, 1); } else cur.push(s);
    P.stores = cur.length === ALL_STORES.length ? null : cur;
    onEngineChange();
  }));
}

// ---------- table builders ----------
function destroyTable() { if (table) { table.destroy(); table = null; } }

const mainColumns = () => [
  { title: t("c_status"), field: "status", width: 118, formatter: (c) => segBadge(c.getValue()),
    sorter: (a, b) => STATUSES.indexOf(a) - STATUSES.indexOf(b) },
  { title: t("c_product"), field: "name", minWidth: 200, widthGrow: 3, formatter: (c) =>
    `${c.getValue()} <span class="muted" style="font-size:11px">${c.getRow().getData().store}</span>` },
  { title: t("c_action"), field: "action", width: 150, formatter: (c) => actCell(c.getValue(), c.getRow().getData().rocket) },
  { title: t("c_order"), field: "order", width: 108, hozAlign: "center", headerTooltip: t("c_order_h"),
    formatter: (c) => c.getValue() > 0 ? `<span class="big-order">${c.getValue()}</span>` : '<span class="muted">0</span>' },
  { title: t("c_conf"), field: "confidence", width: 112, hozAlign: "center", headerTooltip: t("c_conf_h"),
    formatter: (c) => confCell(c.getValue()) },
  { title: t("c_onhand"), field: "stock", width: 82, hozAlign: "center", headerTooltip: t("c_onhand_h"),
    formatter: (c) => `<span class="num">${c.getValue()}</span>` },
  { title: t("c_forecast"), field: "sells_mo", width: 100, hozAlign: "center", headerTooltip: t("c_forecast_h"),
    formatter: (c) => `<span class="num">${c.getValue().toFixed(2)}</span>` },
  { title: t("c_cover"), field: "days_cover", width: 104, hozAlign: "center", headerTooltip: t("c_cover_h"),
    formatter: (c) => coverCell(c.getValue()) },
  { title: t("c_why"), field: "whytext", minWidth: 240, widthGrow: 4, headerSort: false,
    cssClass: "wrap", formatter: (c) => c.getValue() },
];

function buildMainTable(rows, height) {
  rows.forEach(r => r.whytext = composeWhy(r.why));
  destroyTable();
  table = new Tabulator("#tbl", {
    data: rows, columns: mainColumns(), layout: "fitColumns", height,
    placeholder: "—", rowHeight: false, reactiveData: false,
  });
  table.on("rowClick", (e, row) => openDrill(row.getData().store, row.getData().sku));
}

// ---------- pages ----------
function renderTabs() {
  document.getElementById("tabs").innerHTML = [
    ["action", "nav_action"], ["stock", "nav_stock"], ["dead", "nav_dead"],
    ["proof", "nav_proof"], ["how", "nav_how"],
  ].map(([id, k]) => `<button class="tab ${TAB === id ? "active" : ""}" data-tab="${id}">${t(k)}</button>`).join("");
  document.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => { TAB = b.dataset.tab; render(); }));
}

function chrome() {
  document.getElementById("subtitle").textContent =
    `${t("tagline")} · ${ALL_STORES.length} ${t("stores_n")} · ${t("sales_through")} ${META.as_of}`;
  document.getElementById("footer-note").textContent = t("footer_note");
  document.getElementById("lang-label").textContent = t("lang_label");
  document.querySelectorAll(".lang-switch button").forEach(b =>
    b.classList.toggle("on", b.dataset.lang === LANG));
}

async function pageAction() {
  const main = document.getElementById("main");
  main.innerHTML = `<div class="page-title">${t("nav_action")} — ${t("ac_title")}</div>
    <div class="page-sub">${t("ac_sub")}</div>${filterBarHTML()}
    <div class="kpis" id="kpis"></div>
    <div style="font-weight:700;font-size:15px;margin:6px 0 2px">${t("ac_reorder_title")}</div>
    <div class="tbl-meta"><span>${t("ac_reorder_sub")} ${t("ac_drill")}</span><button class="btn" id="exp">${t("export_csv")}</button></div>
    <div id="tbl"></div>`;
  wireFilterBar(() => pageAction());
  const d = await getJSON("/api/dashboard?" + qs()); DASH = d;
  const k = d.kpis;
  document.getElementById("kpis").innerHTML = `
    ${kpiHTML("🛒", t("k_reorder_skus"), fmtInt(k.reorder_skus), "", t("k_reorder_skus_h"))}
    ${kpiHTML("📦", t("k_reorder_units"), fmtInt(k.reorder_units), "")}
    ${kpiHTML("🚀", t("k_accel"), fmtInt(k.accelerating), "", t("k_accel_h"))}
    ${kpiHTML("🧊", t("k_dead_cash"), fmtLei(k.dead_cash), "")}`;
  const reorder = d.rows.filter(r => r.order > 0);
  buildMainTable(reorder, 520);
  document.getElementById("exp").addEventListener("click", () => download("reorder_list.csv",
    csv(reorder, [{ title: "sku", val: r => r.sku }, { title: "product", val: r => r.name },
      { title: "store", val: r => r.store }, { title: "action", val: r => t("a_" + r.action) },
      { title: "order", val: r => r.order }, { title: "confidence", val: r => t("cf_" + r.confidence) },
      { title: "on_hand", val: r => r.stock }, { title: "forecast_mo", val: r => r.sells_mo }])));
}

function kpiHTML(icon, label, value, unit, help = "", extra = "") {
  return `<div class="kpi ${extra}">
    <div class="kpi-label" ${help ? `title="${help}"` : ""}>${icon} ${label}${help ? " ⓘ" : ""}</div>
    <div class="kpi-value">${value} <span class="kpi-unit">${unit}</span></div></div>`;
}

async function pageStock() {
  const main = document.getElementById("main");
  main.innerHTML = `<div class="page-title">${t("as_title")}</div>
    <div class="page-sub">${t("as_sub")}</div>${filterBarHTML()}
    <div class="filterbar" style="gap:20px">
      <div class="field" style="flex:1"><label>${t("f_status")}</label><div class="pills" id="status-pills">
        ${STATUSES.map(s => `<span class="pill seg-${s} ${stockFilter.status.has(s) || stockFilter.status.size === 0 ? "on" : ""}" data-s="${s}">${t("s_" + s)}</span>`).join("")}
      </div></div>
      <div class="field"><label>${t("f_search")}</label><input type="text" id="f-search" value="${stockFilter.search}" placeholder="${t("f_search")}"></div>
    </div>
    <div class="tbl-meta"><span id="showing"></span><button class="btn" id="exp">${t("export_csv")}</button></div>
    <div id="tbl"></div>`;
  wireFilterBar(() => pageStock());
  const d = DASH && sameParams() ? DASH : await getJSON("/api/dashboard?" + qs()); DASH = d;
  applyStockFilter();
  document.querySelectorAll("#status-pills .pill").forEach(p => p.addEventListener("click", () => {
    const s = p.dataset.s;
    if (stockFilter.status.has(s)) stockFilter.status.delete(s); else stockFilter.status.add(s);
    if (stockFilter.status.size === STATUSES.length) stockFilter.status.clear();
    p.classList.toggle("on"); applyStockFilter();
  }));
  document.getElementById("f-search").addEventListener("input", (e) => { stockFilter.search = e.target.value; applyStockFilter(); });
  document.getElementById("exp").addEventListener("click", () => {
    const rows = filteredStockRows();
    download("all_stock.csv", csv(rows, [{ title: "sku", val: r => r.sku }, { title: "product", val: r => r.name },
      { title: "store", val: r => r.store }, { title: "status", val: r => t("s_" + r.status) },
      { title: "action", val: r => t("a_" + r.action) }, { title: "order", val: r => r.order },
      { title: "on_hand", val: r => r.stock }, { title: "forecast_mo", val: r => r.sells_mo }]));
  });
}
function filteredStockRows() {
  let rows = DASH.rows;
  if (stockFilter.status.size) rows = rows.filter(r => stockFilter.status.has(r.status));
  if (stockFilter.search.trim()) {
    const q = stockFilter.search.toLowerCase();
    rows = rows.filter(r => r.name.toLowerCase().includes(q) || r.sku.toLowerCase().includes(q));
  }
  return rows;
}
function applyStockFilter() {
  const rows = filteredStockRows();
  document.getElementById("showing").textContent = t("showing", { a: fmtInt(rows.length), b: fmtInt(DASH.rows.length) });
  buildMainTable(rows, 620);
}

async function pageDead() {
  const main = document.getElementById("main");
  main.innerHTML = `<div class="page-title">${t("ds_title")}</div>
    <div class="page-sub">${t("ds_sub")}</div>${filterBarHTML(true)}
    <div class="kpis" id="dkpis"></div>
    <div class="page-sub" style="font-size:12.5px">${t("ds_note")}</div>
    <div class="tbl-meta"><span id="showing"></span><button class="btn" id="exp">${t("export_csv")}</button></div>
    <div id="tbl"></div>`;
  wireFilterBar(() => pageDead());
  const d = await getJSON("/api/deadstock?" + qs()); DEAD = d;
  const k = d.kpis;
  const card = (id, icon, label, val, help) =>
    `<div class="kpi clickable ${deadCardFilter === id ? "active" : ""}" data-card="${id}">
      <div class="kpi-label" ${help ? `title="${help}"` : ""}>${icon} ${label}${help ? " ⓘ" : ""}</div>
      <div class="kpi-value" style="font-size:22px">${fmtLei(val)}</div></div>`;
  document.getElementById("dkpis").innerHTML =
    card("all", "🧮", t("ds_total"), k.total) +
    card("confirmed", "⚰️", t("ds_confirmed"), k.confirmed_dead, t("ds_confirmed_h")) +
    card("dying", "📉", t("ds_dying"), k.dying) +
    `<div class="kpi clickable ${deadCardFilter === "positions" ? "active" : ""}" data-card="positions">
       <div class="kpi-label">📦 ${t("ds_positions")}</div>
       <div class="kpi-value" style="font-size:22px">${fmtInt(k.positions)}</div></div>`;
  document.querySelectorAll("#dkpis .kpi").forEach(c => c.addEventListener("click", () => {
    deadCardFilter = c.dataset.card; document.querySelectorAll("#dkpis .kpi").forEach(x => x.classList.remove("active"));
    c.classList.add("active"); buildDeadTable();
  }));
  buildDeadTable();
  document.getElementById("exp").addEventListener("click", () => {
    const rows = deadRows();
    download("dead_stock.csv", csv(rows, [{ title: "sku", val: r => r.sku }, { title: "product", val: r => r.name },
      { title: "store", val: r => r.store }, { title: "on_hand", val: r => r.stock },
      { title: "last_sale", val: r => r.last_sale || "never" }, { title: "root_cause", val: r => t("r_" + r.root_cause) },
      { title: "unit_value", val: r => r.unit_value }, { title: "trapped_lei", val: r => r.trapped }]));
  });
}
function deadRows() {
  let rows = DEAD.rows;
  if (deadCardFilter === "confirmed") rows = rows.filter(r => r.klass === "dead" && r.idle_mo >= 12);
  else if (deadCardFilter === "dying") rows = rows.filter(r => r.klass === "dying");
  return rows;
}
function buildDeadTable() {
  const rows = deadRows();
  document.getElementById("showing").textContent = t("showing", { a: fmtInt(rows.length), b: fmtInt(DEAD.rows.length) });
  destroyTable();
  table = new Tabulator("#tbl", {
    data: rows, layout: "fitColumns", height: 560, initialSort: [{ column: "trapped", dir: "desc" }],
    columns: [
      { title: t("c_product"), field: "name", minWidth: 220, widthGrow: 3, formatter: (c) =>
        `${c.getValue()} <span class="muted" style="font-size:11px">${c.getRow().getData().store}</span>` },
      { title: t("c_onhand"), field: "stock", width: 90, hozAlign: "center", formatter: (c) => `<span class="num">${c.getValue()}</span>` },
      { title: t("c_lastsale"), field: "last_sale", width: 120, hozAlign: "center",
        formatter: (c) => c.getValue() || `<span class="muted">${t("never")}</span>` },
      { title: t("c_idle"), field: "idle_mo", width: 100, hozAlign: "center",
        formatter: (c) => c.getValue() < 0 ? `<span class="muted">${t("never")}</span>` : `<span class="num">${c.getValue()}</span>` },
      { title: t("c_root"), field: "root_cause", width: 190, formatter: (c) => t("r_" + c.getValue()) },
      { title: t("c_unitval"), field: "unit_value", width: 120, hozAlign: "right",
        formatter: (c) => `<span class="num">${fmtInt(c.getValue())}</span>` },
      { title: t("c_trapped"), field: "trapped", width: 150, hozAlign: "right",
        formatter: (c) => `<span class="num" style="font-weight:700">${fmtInt(c.getValue())}</span>` },
    ],
  });
}

async function pageProof() {
  const d = await getJSON("/api/proof");
  const bt = d.backtest_2026, v = d.backtest_value, wq = d.warning_quality;
  const main = document.getElementById("main");
  const fz = bt ? bt.frozen_2025 : null;
  const biasStr = bt ? (bt.frozen_bias_after_removing_market_drift_pct > 0 ? "+" : "") + bt.frozen_bias_after_removing_market_drift_pct + "%" : "—";
  main.innerHTML = `<div class="page-title">${t("pf_title")}</div><div class="page-sub">${t("pf_sub")}</div>
    <div class="card"><h3>${t("pf_acc_title")}</h3><div class="kpis">
      ${kpiHTML("📉", t("pf_market"), bt ? (bt.market_drift_rug_yoy_h1 * 100).toFixed(0) + "%" : "—", "", t("pf_market_h"))}
      ${kpiHTML("🎯", t("pf_bias"), biasStr, "", t("pf_bias_h"))}
      ${kpiHTML("✅", t("pf_p90"), fz ? (fz.P90_coverage * 100).toFixed(0) + "%" : "—", "", t("pf_p90_h"))}
      ${kpiHTML("🔝", t("pf_capture"), fz ? (fz.actual_demand_captured_by_top10pct_pred * 100).toFixed(0) + "%" : "—", "", t("pf_capture_h"))}
    </div><div class="callout">${t("pf_honest", { bias: biasStr })}</div></div>
    ${v ? `<div class="card"><h3>${t("pf_value_title")}</h3><div class="kpis">
      ${kpiHTML("🛡️", t("pf_safe_calls"), ((1 - v.dead_stock_falsekill.false_kill_rate) * 100).toFixed(0) + "%", "", t("pf_safe_calls_h"))}
      ${kpiHTML("💰", t("pf_correct_cash"), fmtLei(v.dead_stock_falsekill.trapped_lei_correctly_flagged), "")}
    </div></div>` : ""}
    <div class="card"><h3>${t("pf_wq_title")}</h3>
      <div class="page-sub">${t("pf_wq_sub", { m: wq.months, r: fmtInt(wq.rows), s: fmtInt(wq.should_warn_rows) })}</div>
      <div class="kpis">
        ${kpiHTML("🟢", t("pf_engine"), (wq.ours.recall_of_real_shortfalls * 100).toFixed(0) + "%", t("pf_precision") + " " + (wq.ours.precision_of_warnings * 100).toFixed(0) + "%")}
        ${kpiHTML("⚪", t("pf_flat"), (wq.theirs.recall_of_real_shortfalls * 100).toFixed(0) + "%", t("pf_precision") + " " + (wq.theirs.precision_of_warnings * 100).toFixed(0) + "%")}
      </div></div>`;
}

function md(s) {
  return s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
}
function pageHow() {
  const p = META.calibration_params, cal = { mc: p.mover[0], ms: p.mover[1], sc: p.sparse[0], ss: p.sparse[1] };
  const lt = META.lead_time_days, ss = META.safety_days;
  const main = document.getElementById("main");
  const sec = (h, body) => `<div class="card"><h3>${t(h)}</h3><div class="prose">${md(body)}</div></div>`;
  main.innerHTML = `<div class="page-title">${t("hw_title")}</div><div class="page-sub">${t("hw_sub")}</div>
    ${sec("hw_1", t("hw_1_body", { phi: META.phi }))}
    ${sec("hw_2", t("hw_2_body", cal))}
    ${sec("hw_3", t("hw_3_body", { lt, lts: lt + ss, lta: lt + ss + 14, mm: META.min_material_monthly }))}
    ${sec("hw_4", t("hw_4_body"))}
    ${sec("hw_5", t("hw_5_body"))}
    ${sec("hw_6", t("hw_6_body", { notes: META.notes.join(" · ") }))}`;
}

// ---------- drilldown ----------
function killCharts() { charts.forEach(c => c.destroy()); charts = []; }
async function openDrill(store, sku) {
  const d = await getJSON(`/api/sku/${encodeURIComponent(store)}/${encodeURIComponent(sku)}?` + qs());
  if (d.error) return;
  const dr = document.getElementById("drawer");
  const mk = (l, v) => `<div class="mini-kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`;
  dr.innerHTML = `<div class="drawer-head"><div><h3>${d.name}</h3><div class="sku">${store} · ${sku}</div></div>
      <button class="drawer-close" id="dclose">×</button></div>
    <div class="drawer-body">
      <div class="mini-kpis">
        ${mk(t("c_onhand"), d.stock)}${mk(t("c_forecast"), d.sells_mo.toFixed(2))}
        ${mk(t("c_range"), (d.expected ?? "—") + " → " + (d.safe ?? "—"))}
        ${mk(t("c_order"), `<span style="color:var(--accent)">${d.order}</span>`)}
        ${mk(t("c_conf"), confCell(d.confidence))}</div>
      <div class="chart-block"><h4>${t("c_forecast")} — ${LANG === "ro" ? "vânzări săptămânale" : "weekly units sold"}</h4><div class="chart-wrap"><canvas id="c-weekly"></canvas></div></div>
      <div class="chart-block"><h4>${LANG === "ro" ? "An curent vs an trecut (pe săptămâni)" : "This year vs last year (by week)"}</h4><div class="chart-wrap"><canvas id="c-dual"></canvas></div></div>
      <div class="chart-block"><h4>${LANG === "ro" ? "Stoc la sfârșit de lună" : "Stock at end of month"}</h4><div class="chart-wrap"><canvas id="c-stock"></canvas></div></div>
      <div class="decomp">
        <div>${LANG === "ro" ? "Ritm recent (13s)" : "Recent rate (13w)"}: <b>${d.decomp.roll13.toFixed(2)}/${LANG === "ro" ? "săpt" : "wk"}</b></div>
        <div>${LANG === "ro" ? "După combinarea cu familia" : "After family pooling"}: <b>${d.decomp.pooled_rate.toFixed(2)}</b></div>
        <div>${LANG === "ro" ? "Sezon" : "Season"}: <b>×${d.decomp.season.toFixed(2)}</b> · ${LANG === "ro" ? "Tendință" : "Trend"}: <b>×${d.decomp.trend.toFixed(2)}</b></div>
        <div>${LANG === "ro" ? "Risc lipsă stoc (livrare)" : "Stockout risk (lead time)"}: <b>${d.stockout_risk != null ? d.stockout_risk.toFixed(0) + "%" : "—"}</b></div>
      </div></div>`;
  document.getElementById("scrim").classList.add("open");
  dr.classList.add("open"); dr.setAttribute("aria-hidden", "false");
  document.getElementById("dclose").addEventListener("click", closeDrill);
  killCharts();
  const axis = { grid: { color: "#eef1f5" }, ticks: { font: { size: 10 }, color: "#8a93a0" } };
  const base = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: axis, y: { ...axis, beginAtZero: true } } };
  charts.push(new Chart(document.getElementById("c-weekly"), { type: "line", data: {
    labels: d.weekly.map(w => w.week), datasets: [{ data: d.weekly.map(w => w.units),
      borderColor: "#2563eb", backgroundColor: "rgba(37,99,235,.08)", fill: true, pointRadius: 0, borderWidth: 1.6, tension: .25 }] }, options: base }));
  if (d.dual_calendar && d.dual_calendar.woy) {
    charts.push(new Chart(document.getElementById("c-dual"), { type: "line", data: {
      labels: d.dual_calendar.woy, datasets: [
        { label: "" + d.dual_calendar.prev.year, data: d.dual_calendar.prev.units, borderColor: "#94a3b8", pointRadius: 0, borderWidth: 1.4 },
        { label: "" + d.dual_calendar.curr.year, data: d.dual_calendar.curr.units, borderColor: "#2563eb", pointRadius: 0, borderWidth: 1.8 }] },
      options: { ...base, plugins: { legend: { display: true, labels: { font: { size: 10 }, boxWidth: 12 } } } } }));
  }
  charts.push(new Chart(document.getElementById("c-stock"), { type: "bar", data: {
    labels: d.monthly_stock.map(s => s.month), datasets: [{ data: d.monthly_stock.map(s => s.stock), backgroundColor: "#7c3aed88" }] }, options: base }));
}
function closeDrill() {
  document.getElementById("scrim").classList.remove("open");
  const dr = document.getElementById("drawer"); dr.classList.remove("open"); dr.setAttribute("aria-hidden", "true"); killCharts();
}

// ---------- render dispatch ----------
let lastParams = "";
function sameParams() { return lastParams === qs(); }
function render() {
  renderTabs(); chrome(); killCharts(); destroyTable();
  document.getElementById("main").scrollTop = 0;
  if (TAB === "action") pageAction();
  else if (TAB === "stock") pageStock();
  else if (TAB === "dead") pageDead();
  else if (TAB === "proof") pageProof();
  else if (TAB === "how") pageHow();
  lastParams = qs();
}

async function init() {
  const params = new URLSearchParams(location.search);
  if (["en", "ro"].includes(params.get("lang"))) LANG = params.get("lang");
  if (["action", "stock", "dead", "proof", "how"].includes(params.get("tab"))) TAB = params.get("tab");
  META = await getJSON("/api/meta"); ALL_STORES = META.stores;
  P.lt = META.lead_time_days; P.ss = META.safety_days;
  document.documentElement.lang = LANG;
  document.getElementById("scrim").addEventListener("click", closeDrill);
  document.querySelectorAll(".lang-switch button").forEach(b => b.addEventListener("click", () => {
    LANG = b.dataset.lang; localStorage.setItem("stockly_lang", LANG); document.documentElement.lang = LANG; render();
  }));
  render();
  const drill = params.get("drill");  // "STORE|SKU" — deep-link straight to a SKU's detail
  if (drill && drill.includes("|")) { const [st, sk] = drill.split("|"); openDrill(st, sk); }
}
init();
