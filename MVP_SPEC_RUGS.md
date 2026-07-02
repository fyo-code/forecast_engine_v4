# MVP Spec — Rug Replenishment Copilot (working name: Stockly)

Date: 2026-07-02. Phase 1 deliverable. Defined by working backwards from the 10-minute V demo.
Audience: first V (built the internal module's prototype), then the rug product manager (its daily user).

## 0. The one-liner

> The rug PM's reorder module, with a real engine under it: it sees the season ahead,
> tells you **how much** to order, tells you what to **stop** ordering — and it's proven
> against 4 years of your own history, including against what the store actually did.

Positioning vs the internal module: an **upgrade of V's idea**, not a rival. The module
answers "what's low on average-rate?" — this answers "what will I need, how much, and
what is quietly dying?"

---

## 1. The 10-minute V demo (the MVP is whatever this needs — nothing more)

1. **Framing (1 min).** "You built the urgency module. Here's what it looks like with a
   real engine: season-aware, quantity-aware, plus the thing no cover-metric can see —
   the kill-list. Tested on 4 years of real Constanta history."
2. **Panel 1 — REPLENISH (3 min).** This week's reorder list for Constanta, ranked by
   stockout risk. Click a SKU → the "because" breakdown + sales/stock history chart.
   Beat: *"the module flags on average rate; this flags on what November actually does to rugs."*
3. **Panel 2 — KILL-LIST (2 min).** Designs with stock but dead/dying demand, ranked by
   trapped lei. Beat: *"months-of-cover divides by sales rate — a rug with stock and ZERO
   sales has infinite cover and disappears from that metric. These are exactly the ones
   costing money. There are N of them holding X lei."*
4. **Panel 3 — PROOF (3 min).** 2023–2025 replay, three-way: our policy vs the module's
   run-rate logic vs what the store actually did (reconstructed receipts). Headlines:
   fewer stockout-months at same-or-less stock; warnings N weeks earlier than run-rate
   before seasonal ramps; dead re-orders avoided (units bought for designs that then died).
5. **The ask (1 min).** Two facts: real rug lead times, and does the module suggest
   quantities? One intro: 15 minutes with the rug PM. One export: fresh stock snapshot.

**Demo success = V says "the PM should see this."** Not "it's perfect."

---

## 2. Panel specs

### Panel 1 — Replenish (per store; Constanta first)

Scope: rug SKUs with store-stock flow at the selected store (the ~77% of sales that move
through store stock; the ordered-in 23% is shown as context, not governed).

Columns per SKU:
- product (code + `DENUMIRE ARTICOL`), current stock (latest EOM snapshot; staleness shown),
- predicted demand over the protection window: **P50 and P90**,
- **stockout risk** = P(demand over lead time > current stock) — season/trend-aware,
- **suggested order** = order-up-to(P-service-level) − stock − on-order(unknown → 0, flagged),
- urgency tier: `critical / urgent / watch / ok`,
- the **"because" line** (transparency, RELEX rule): plain language, e.g.
  *"sells ~3/wk recently; November lifts rugs ~+40%; trend stable; 4 in stock; 4-wk lead
  → 90% chance you need ≥9 → order 8."*

Interactive knobs (demo moments, not settings pages): **lead time** (unknown → slider,
default 4 wks; "the tool takes the real value as input — that's a question for the PM")
and **service level** (default 90%).

Every number must decompose as: `base rate × seasonal factor × trend (± family prior for
sparse SKUs) → distribution → quantile → minus stock → order`. No black box.

### Panel 2 — Kill-list (dead & dying stock)

- Metric: months-of-cover **with rate=0 handled** — zero-sellers rank at the TOP (by stock
  value), not excluded. Plus: weeks-since-last-sale, demand trend (decaying/dead/slowing).
- Columns: product, stock units, est. stock value (unit value from sales history), cover
  (∞ shown as "no sales in N months"), trend, suggested action (`stop reordering` /
  `clearance candidate`), trapped lei.
- Header: **total trapped lei** across the list. This is the CFO number.

### Panel 3 — Proof (the three-way backtest)

- Replay 2023–2025 at **monthly decision cadence** (stock data is monthly EOM; stated
  honestly — the live tool runs weekly on fresher snapshots, the backtest proves the
  decision logic at monthly grain).
- Three policies per SKU×store×month: **(a) ours** (calibrated order-up-to), **(b) the
  module's logic** (run-rate days-of-stock urgency, reconstructed from its described
  rules — flagged as an approximation to confirm with V), **(c) actual** (reconstructed
  receipts = Δstock + sales, validated in Phase 0: 2% noise, receipts/sales = 1.02).
- KPIs:
  1. stockout-months on store-stocked movers at matched average stock,
  2. inventory units & lei at matched availability,
  3. **early-warning lead**: weeks before a demand ramp each policy would have flagged,
  4. **dead re-orders avoided**: units actually received for SKUs whose demand then died
     (sold <k in following 6 months) that our policy would not have ordered.
- All three stores replayed; Constanta is the demo story, Iasi/Oradea the robustness proof.

---

## 3. Explicitly OUT of the MVP

Live ERP/CrosWeb integration; auth/multi-user; automation of any order; other categories;
markdown/price optimization; promo-calendar input (until Mobexpert provides one);
mobile; alerts/notifications. The MVP is a **local demo app on real data**.

## 4. Non-blocking questions for V (asked AT the demo, not before)

1. Real rug lead times (per supplier or typical)? — the tool takes it as input.
2. Does the internal module suggest quantities, or only urgency?
3. How often does the rug PM actually place orders (weekly? monthly? ad hoc)?
4. Who decides clearance/markdown for dying designs?

## 5. Engine deltas this spec demands (input to Phase 2)

1. Stock-aware reorder: order-up-to minus **real on-hand** (EOM snapshots).
2. Occurrence-aware demand model for sparse SKU×store series (rugs sell 1–2/design/store)
   + **design-family pooling** (parse `DENUMIRE ARTICOL`: design name repeats across sizes).
3. Kill-list metrics: cover with rate=0 → ∞ handling, decay/trend classification, trapped value.
4. Module-logic replica (run-rate urgency) as a first-class baseline in the backtest.
5. Three-way monthly-cadence backtest harness on reconstructed receipts.
6. Every output carries its decomposed "because" fields (the interface only renders them).

## 6. Success gate for the whole MVP (from the Phase 3 plan)

Calibrated (coverage ±5pp); beats module-logic on early warning without more false
urgency; matches/beats actual on inventory-at-availability; robust across the 3 stores
and rolling windows; 20-SKU manual sanity check passes. Then STOP polishing and demo.
