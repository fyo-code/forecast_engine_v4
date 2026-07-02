# Engine Audit — V4 v1 (pre-Phase-2)

Date: 2026-07-02. Scope: the engine as built for mattresses (Phases A–D) audited top-down
before adapting it to the rug MVP. Each finding: severity for the rug MVP, and the fix.
This audit is self-critique — the v1 was deliberately thin; the point is to decide which
shortcuts must die now and which are still fine.

---

## Level 0 — System architecture

**A0.1 (CRITICAL) — No live-inference path exists.** The engine only backtests: train on
early weeks, score on late weeks. There is no "train on everything up to today, emit next
week's recommendation list." The demo's Panel 1 *is* that path. → Build `live_recommend.py`
as a first-class stage (train ≤ now → predict next window → join latest stock → decide).

**A0.2 (CRITICAL) — Chain grain is baked into the panel builder.** `features.load_weekly_chain`
aggregates stores away. Mattress pragmatism (stores too sparse); wrong for rugs where the
decision and the stock are per-store. → New store-grain panel builder; chain stays available
as a pooling level, not the decision grain.

**A0.3 (HIGH) — Stages are fused.** `demand_model.run()` trains, predicts, evaluates, and
writes artifacts in one function; `backtest.py` re-trains internally. → Separate: panel →
features → model (interface) → calibrate → decide → evaluate. Each stage a function with
data in/out; runners compose them. (Not V3-style ceremony — just clean seams.)

**A0.4 (MEDIUM) — No run history.** Metrics jsons overwrite each other; Phase 3's
"cycles until the gate" needs cycle-over-cycle comparison. → Append-only `data/runs/`
with timestamped metrics + a one-line index. (The V2 lesson: no markdown-as-database;
the V3 lesson: no 9-table contract ceremony. Middle ground.)

**A0.5 (LOW) — Config knobs read at multiple call sites** (service level, window). →
Single `PolicyConfig` dataclass threaded through decide/simulate.

## Level 1 — Statistical architecture

**S1.1 (CRITICAL, decision) — The GBM cannot natively produce the "because" line.** The
spec's transparency rule (every number decomposes: rate × season × trend − stock) is not
explainable *out* of a GBM honestly. Decision taken (within the "ML is my call" mandate):
**build an interpretable decomposed model as PRIMARY** — trailing base rate × leak-safe
family-month seasonal index × trend ratio, with a Negative-Binomial distribution around
the mean (matches measured over-dispersion) → quantiles. **Keep the quantile GBM as the
CHALLENGER** in the backtest; the gate picks the winner per universe. If the GBM wins
materially, its predictions ship with the decomposition shown as *diagnostic context*
(labeled as such, never fake attribution). Rationale: at rug sparsity (0–2 units/wk/store)
a well-calibrated parametric model is likely competitive (v1 GBM beat naive by only ~10%
MAE), and transparency is a hard product requirement (RELEX lesson).

**S1.2 (HIGH) — Family pooling is crude and half-used.** v1: supplier/size target-encoding
with fixed shrinkage 20; ablation showed ~nothing on mattresses. For rugs, the natural
family is the **design name** (`DENUMIRE ARTICOL` repeats across size variants). → New
`families.py`: parse design tokens; family becomes the seasonal-index level and the
sparse-SKU fallback rate. This is where pooling actually earns its keep.

**S1.3 (HIGH) — Calibration is measured, never applied.** v1 reports coverage; nothing
corrects it. The buffer IS the product; the gate demands ±5pp. → `calibration.py`: fit a
per-model dispersion/quantile scaling on a validation slice (conformal-style), apply to
served quantiles, re-verify on test.

**S1.4 (MEDIUM) — Occurrence modeling.** Spec §5 lists an occurrence model for sparse
series. Audit judgment: NB quantiles at low λ already put P50=0/P90=1–2 correctly; a
separate occurrence stage adds complexity V3-style without proven need. → Defer: implement
only if calibration fails on sparse tiers (recorded as a conditional, not dropped).

**S1.5 (MEDIUM) — Returns ignored in stock flow.** Demand target is gross-positive
(correct); but returns re-enter store stock (~2–6%), absorbed silently by reconstructed
receipts. → Document as known approximation in the backtest; no code change for MVP.

**S1.6 (LOW) — Seasonal features are calendar dummies only** (month, Q4, Nov). Family
seasonal indices (S1.1) supersede this for the primary model; GBM keeps the dummies.

## Level 2 — Decision layer

**D2.1 (HIGH) — Fractional orders.** The simulator orders fractional units; rugs are
integers and order-up-to at level 1–3 makes rounding material. → Integer policy
(round-half-up on order qty; never below 0), and the backtest respects it.

**D2.2 (HIGH) — Service level fixed at 95% is wrong for rugs.** Mobexpert's costs are
asymmetric (understock cheap — 23% of rug sales happen without store stock; overstock
expensive — dead designs). → Default 90% for rugs, exposed as the demo slider; per-tier
targets later. The right frame is availability at minimum stock, not max fill.

**D2.3 (MEDIUM) — Two cadences must coexist.** Backtest decides monthly (stock is EOM);
the live tool decides weekly. → Policy functions take cadence/window as parameters; no
hardcoded "weekly".

**D2.4 (MEDIUM) — Kill-list logic doesn't exist yet.** Spec Panel 2. → `kill_list.py`:
cover with rate=0 → ∞ ranked top by stock value; decay classes (dead / dying / slowing)
from trailing demand; trapped value = stock × trailing unit value.

**D2.5 (LOW) — No MOQ/pack-size support.** Unknown for rugs; leave a hook, ask the PM.

## Level 3 — Evaluation

**E3.1 (CRITICAL, upgrade) — Free-running simulation drifts from reality; we can anchor
it.** v1 simulated a fictional inventory trajectory for 26 weeks. For rugs we HAVE the
actual monthly stock — so replay **anchored**: each month starts from the store's *actual*
EOM stock; compare our order vs the module-logic order vs actual receipts, given actual
demand. Single-period replay per month, no compounding drift, far more defensible. The
free-running sim stays as a secondary robustness check.

**E3.2 (HIGH) — Single split → rolling origin.** v1 used one 26-week holdout. Phase 3 gate
demands robustness across windows. → A window generator (rolling monthly cutoffs 2023–2025)
as shared infrastructure now, so both models and both backtests use identical windows.

**E3.3 (HIGH) — The three-way needs a module-logic replica.** Baseline (b) = run-rate
urgency (stock ÷ trailing avg rate, thresholds) + replace-what-sold quantities — built from
V's description, explicitly flagged as an approximation to confirm. → `module_replica.py`.

**E3.4 (MEDIUM) — Language: availability, not lost sales.** Stockout at store ≠ lost sale
(stock ≠ sellability). All metrics/report wording: availability-months, inventory at
matched availability, dead re-orders avoided.

**E3.5 (MEDIUM) — No human-inspectable sanity artifact.** The gate includes a 20-SKU manual
check. → The backtest emits a spot-check file (per-SKU history + decisions) for review.

## Level 4 — Engineering

**C4.1 (HIGH) — Per-SKU Python loops won't scale to rug grain.** `load_weekly_chain`
reindexes per SKU in a loop; `_weeks_since` is a per-row Python loop. Mattress chain =
2.3k series; rugs at store grain ≈ up to ~28k series × 200 weeks. → Vectorize (MultiIndex
reindex / groupby-cumcount tricks or DuckDB), or the iteration loop dies of slowness.

**C4.2 (HIGH) — Zero tests.** V4 has no pytest coverage at all (V3's one unambiguous
virtue was its 57 tests). → Minimal focused suite (~12): leakage shift-test (features at
cutoff unchanged when future rows are dropped), window-target math, receipts formula,
NB quantile monotonicity, calibration scaling, cover ∞ handling, integer policy, replica
rules. The leakage shift-test is the single most important one (the 86.6% trap, mechanized).

**C4.3 (MEDIUM) — Fragile spots:** `backtest._changeweek_stockouts` aligns spike flags by
`[-len(flags):]` slicing (implicit warmup coupling); the equal-stock `k` search re-simulates
the cohort 25×; `features.py` has a dead `disc_num` placeholder column. → All three go away
with the anchored-replay redesign + panel rewrite; don't patch, replace.

**C4.4 (LOW) — Naming drift:** `ingest_mattress.py` is now category-generic; summary field
`n_mattress_skus` etc. → Rename module to `ingest_category.py` with a thin alias; field to
`n_cohort_skus` (manifest consumers: none yet).

---

## Decisions taken (no user input required — within existing mandates)

1. **Interpretable decomposed model primary, GBM challenger** (S1.1) — transparency is a
   hard requirement; gate picks by results.
2. **SKU×store direct grain with family pooling** (A0.2, S1.2).
3. **Anchored monthly replay as the primary backtest** (E3.1) — enabled by real stock data.
4. **Service level default 90% for rugs + slider** (D2.2) — per the corrected cost model.
5. **Occurrence model deferred** unless sparse-tier calibration fails (S1.4).

## Phase 2 build order (each step runs on real data before the next)

1. `families.py` — design-family extraction from `DENUMIRE ARTICOL` (+ report of family sizes).
2. `rug_panel.py` — vectorized SKU×store weekly panel + monthly aggregation + window generator.
3. `interpretable_model.py` — rate × season × trend → NB quantiles, with "because" fields.
4. `calibration.py` — validation-slice scaling, applied + verified.
5. `gbm_challenger.py` — v1 quantile GBM adapted to the rug panel (same interface).
6. `kill_list.py` — cover/decay/trapped-value classification.
7. `module_replica.py` — run-rate urgency + replace-what-sold baseline.
8. `backtest_rugs.py` — anchored three-way monthly replay, rolling windows, spot-check artifact.
9. `live_recommend.py` — the live path (train ≤ now → this week's list, because fields, stock join).
10. `tests/` — the ~12 focused tests (leakage shift-test first).
