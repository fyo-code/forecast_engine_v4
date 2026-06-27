# 01 — Product Direction

The strategic source of truth for V4: what we build, for whom, why, and the trajectory. Evidence behind the numbers is in `04_EVIDENCE.md`; the audit that motivated the pivot is in `05_HISTORY_AND_AUDIT.md`.

---

## 1. The reframe (from forecast score to inventory decision)

The whole pivot is one move: **stop optimizing a forecast accuracy number; start optimizing an inventory decision.**

There are two different "predictions" people conflate:
- **The rate** (the average): *"this mattress sells ~10/wk, ~16 on promo."* — **predictable.**
- **The exact count in a short window** — **irreducibly random** (chunky arrivals: one customer buys 3, a hotel buys 8).

`Hit±20` grades the exact count. Reordering only needs the rate **plus a buffer** sized to the swing. More data improves the rate and catches shifts; it cannot remove the randomness in the realized count. That is why the point-accuracy ceiling (~25%) holds even with rich features — and why we don't chase it.

## 2. KPIs — grade on these, never on Hit±20

- **Service level / fill rate** at minimum inventory (in stock X% of the time while holding Y% less stock).
- **Stockouts avoided**, especially on change-weeks (promo/season/trend).
- **Overstock units avoided.**
- **Hours saved** vs the manual after-close process.
- **Calibration** — predicted P90 actually covers ~90% of realized demand.
- **Change-week skill** — beat the human's reactive reorder when demand shifts.
- Diagnostic only (never a gate): `Hit±30 ≈ 45–50%` is reachable; `Hit±20` is not, and is irrelevant.

## 3. The wedge (v1)

A **demand-aware daily reorder assistant for recurring fast-mover mattresses**, replacing the manual after-close list.
- One pain, one user (store staff / product managers), one cohort, one data source you already have (sales).
- Confirmed context: **no system does this today** (fully manual); reorder uses target levels and is mostly "replace what sold" for fast-movers → **buildable on sales alone for v1** (no live-stock dependency).
- Pitch (honest, defensible): *"Your team reorders for yesterday. We reorder for what's about to happen — the promo, the season, the trend — so you stop the stockouts and overstock that hit exactly when demand shifts. And it's automatic."* We do **not** claim to be more accurate every day (nobody can be on flat weeks); we win on **change-weeks + automation + consistency.**

## 4. The cohort decision — mattresses first

Because forecastability is **flat across the catalog** (~24–27% ceiling everywhere — `04_EVIDENCE.md` §3), cohort choice is driven by **money × operational fit** (short lead, standardized, daily-recurring), not by a forecast edge.

- **`SALTELE SI SOMIERE` (mattresses + bed bases) — v1.** Cleanest operational loop: standardized, short lead, `DATA ≈ DATA COMANDA`, genuinely daily-recurring, coherent demo. Smaller money (5.5% of revenue) is an acceptable trade for a clean first proof.
- **Money-expansion (v2): `MOBILIER CORP` (17.8%) and `CANAPELE`/sofas (8.4%).** Forecast as well or better, but carry a custom-config / longer-lead tail that muddies a first reorder loop.
- **Avoid:** seasonal junk (`CHRISTMAS` dispersion 71, `OUTDOOR` 13.5).

## 5. The core (one thing)

> **A self-adjusting reorder level per SKU that keeps the item in stock at minimum inventory — sized to the demand's swing, and raised in advance when the engine sees a shift coming.**

That single number (the order-up-to level) **is** the product. The capability behind it is **calibration** ("this level covers demand ~95% of the time") + **change-detection**. Everything else (UI, automation, dead-stock, markdown) hangs off it. North-star metric: **service level at minimum stock.**

## 6. Positioning

> **The autonomous inventory brain for furniture retail — it learns your demand, makes the daily reorder decisions, and progressively takes them off your plate.**

Copilot today, autopilot tomorrow. Value beyond reorder numbers (same demand brain): dead-stock/overstock (capital freed), markdown/clearance pricing, per-store assortment, new-product cold-start, working-capital/cashflow planning (basket-level demand is ~88% predictable → a CFO-grade tool).

## 7. The data flywheel (honest version)

- **Mostly a myth here:** "more sales history → higher accuracy." The ceiling is structural; past ~2 years of history, more raw sales barely moves the point forecast. Do not sell this as the moat.
- **Real and defensible — the closed decision loop:** (a) **override feedback** (accept/edit logs = proprietary, compounding), (b) **outcome feedback** (was the buffer right? stockout/overstock → recalibration), (c) **catalog/family priors** (new SKUs get better cold-starts as the hierarchy fills), (d) **promo-response library**, (e) long-term **cross-client product priors** (a mattress behaves similarly across retailers — the big moat at multi-client scale).
- **Key connection:** the closed-loop data is the evidence that proves "more reliable than the human on these SKUs" — i.e., it **earns the right to automate.** Flywheel and automation are the same thing viewed twice.

## 8. Roadmap / automation trajectory

`v1: demand-aware mattress reorder (sales only, copilot)`
→ `v2: dynamic target/par-level optimization (needs periodic stock) + sofas/MOBILIER CORP`
→ `v3: dead-stock & overstock (same engine, inverted)`
→ `markdown / clearance pricing`
→ `autopilot (auto-reorder proven-safe SKUs, guardrails)`
→ `inventory brain across the decision surface`.

Automation is **earned SKU-by-SKU**: copilot → track accept/override + outcomes → autopilot-with-oversight → full auto for stable categories. Honest risks to manage: (a) auto-ordering moves real money → hard guardrails (caps, anomaly halts, human escalation); (b) organizational resistance → gradual, transparent, oversight-first rollout. Automation is a *trust* product, not just a tech product.

## 9. Decision log (each with its "why")

- **D1.** Stop optimizing `Hit±20`; grade on service level / stockouts / overstock / hours + calibration. *Why:* empirical oracle ceiling ~22–27% everywhere — the metric measures irreducible lumpiness, not skill.
- **D2.** Forecast = calibrated distribution → reorder decision, not a point. *Why:* over-dispersion (var/mean ≈ 8–11) makes point accuracy impossible but distribution/decision tractable (basket aggregation ~88%).
- **D3.** Wedge = daily mattress reorder assistant. *Why:* daily painful manual task, no system today, sales-only buildable, strategic pillar, ceiling-robust.
- **D4.** Cohort = `SALTELE SI SOMIERE` first; sofas/`MOBILIER CORP` next. *Why:* forecastability flat → choose on operational cleanliness + money.
- **D5.** Engine = pooled, decomposed, negative-binomial/quantile, calibrated, with a reorder policy. *Why:* fixes the V2/V3 code-level failures; justified by the variance decomposition.
- **D6.** Rebuild foundation clean, reuse Codex patterns, start narrow. *Why:* horizontal-before-vertical + dropped-columns failure; foundation errors poison everything.
- **D7.** Moat = closed decision loop + cross-client priors, not raw sales volume. *Why:* sales data saturates against the ceiling.
- **D8.** Automation is the destination, earned via the loop, with guardrails.
