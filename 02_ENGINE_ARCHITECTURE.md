# 02 — Engine Architecture & v1 Build Plan

How the forecast engine (the core backend block that predicts demand) is designed, and the concrete plan for the first vertical slice. Evidence justifying these choices: `04_EVIDENCE.md`. Data meanings/locations: `03_DATA_SPEC.md`.

---

## 1. Design principles

- **Predict a distribution, not a point.** Demand is over-dispersed; output P50/P90, not a single number.
- **Decompose and pool.** Demand = base rate × season × promo × channel, with each SKU shrunk toward its product-family prior.
- **Calibrate.** The buffer is only as trustworthy as the quantiles. Calibration is a first-class component.
- **Decide.** The engine's job ends at an order-up-to level, not a forecast.
- **Leakage-safe by construction.** Every feature knowable before the decision; current-snapshot fields are live gates only.
- **Match the model to the data.** var/mean ≈ 8 → count model (negative binomial) / quantile loss, never squared-error mean.

## 2. The engine — layer by layer (each fixes a specific V2/V3 mistake)

1. **Signal layer.** Re-ingest the columns the V3 lake dropped (promo, dimensions, category, montage, channel). Features grouped by *what they explain*: base level, trend, seasonality (Q4 / holiday / **day-of-week**), promo/discount depth, channel, returns, lifecycle. *(V2/V3: autoregressive-only — the root failure.)*
2. **Hierarchical pooling.** Each SKU = its own history **shrunk toward its product-family prior**, so sparse/new mattresses borrow the family's shape. The single biggest upgrade. *(V2/V3: every SKU modeled alone.)*
3. **Decomposed demand.** expected = base rate × season × promo × channel, each estimated and interpretable. *(V2: one black box + hand-coded ×1.5/×1.8 lift hacks.)*
4. **Over-dispersed count distribution.** Model demand as **negative binomial / quantile regression** → full spread. *(V3: squared-error mean regression — chases spikes.)*
5. **Promo elasticity.** Learn demand response to discount depth (proven to drive spikes); feed **planned** promos as a forward input. *(V2/V3: discount as a lagged feature only.)*
6. **Calibration layer.** Conformal / quantile calibration so P90 really covers ~90%. *(New — V2/V3 had none.)*
7. **Reorder policy (the decision).** Order-up-to level = the demand quantile balancing stockout vs holding cost over the protection window (lead time + review period). Transparent formula on top of the distribution. *(V2/V3: no decision layer at all.)*
8. **Online update + decision evaluation.** New sales → update level, detect regime change, re-issue reorder. Grade on fill rate / stockouts / overstock, with V3's leakage discipline kept. *(V2/V3: gated on Hit±20.)*

## 3. Grain & horizon

- **Grain: SKU × store.** Not chain-level (that compresses away the store/channel behavior the decision needs).
- **Horizon: the reorder protection window** (lead time + review period), not a fixed 4-week abstract window.
- **Do not** forecast daily point counts (0/1 noise). **Do not** use weekly ÷ 7 (ignores the weekend skew). Forecast **cumulative protection-window demand**, shaped by a **day-of-week profile**.

## 4. Rebuild vs reuse (engineering approach)

Principle: **build the expensive-to-fix layers carefully from scratch; iterate the cheap ones fast.**

- **Rebuild clean (errors here poison everything):** ingestion (keep ALL columns), grain (SKU×store×time), leakage rules, row identity, target definition.
- **Reuse Codex's *patterns*, not its code:** source manifest, merge-confidence tiers (don't blindly merge — correct lesson), leakage quarantine, conservation checks, DuckDB+Parquet + SQLite control plane.
- **Throw away:** the V2/V3 `phase*` script sediment, chain-only grain, the `Hit±20` scorecard, the ×1.5/×1.8 policy multipliers, the dropped-column import.
- **Start narrow:** ingest the **mattress slice correctly** first, verify by hand, expand category-by-category.

## 5. Suggested stack (lean)

- **Data:** DuckDB + Parquet for the slice; a small SQLite/JSON control plane for run metadata. (Same architecture pattern V3 got right.)
- **Modeling:** Python; start with gradient-boosted **quantile** regressors or a negative-binomial GLM/GBM; add hierarchical pooling via family target-encoding/priors. Keep it explainable before fancy.
- **No premature infra.** No multi-phase contract framework until the slice proves out.

## 6. v1 build plan (the thin vertical slice)

Goal: mattresses end-to-end, proving the decision beats the manual process — on data we already have.

1. **Ingest the mattress slice (clean foundation).** Pull `SALTELE SI SOMIERE` rows from the source repo (see `03_DATA_SPEC.md` for folders/files), keeping **all** columns. Normalize to SKU × store × week. Tag leakage class per field. Run conservation checks (units/value reconcile). Use `DATA` as demand date (short-lag for mattresses).
2. **Build the demand engine.** Per SKU×store: decomposed, pooled, **quantile/negative-binomial** distribution over the protection window, with promo/season/channel/family features. Calibrate quantiles on a holdout.
3. **Build the reorder policy.** Order-up-to level from the chosen demand quantile (service-level target) + day-of-week shape. v1 = sales-driven "replace what sold + change-aware top-up" (no live stock needed).
4. **Shadow-backtest (the proof harness).** Replay history: our reorder list vs what staff did vs the ideal. Score **stockouts avoided, overstock units avoided, hours saved, calibration (P90 coverage), change-week skill**. Strict leakage checks (forward holdout only).
5. **Decision gate.** Promote only if it beats the human baseline on change-weeks and matches on stable weeks, with calibrated coverage. Then propose a one-store pilot.

## 7. Anti-checklist (don't repeat these)

- Don't grade or gate on `Hit±20`.
- Don't model each SKU in isolation (use pooling).
- Don't use squared-error for skewed counts.
- Don't add features computed from the target window (`nlines`, in-window aggregates) — that's the 86.6% leakage trap.
- Don't build horizontal infra before the slice works.
- Don't use stock as a sellability gate or current-snapshot fields as historical features.
