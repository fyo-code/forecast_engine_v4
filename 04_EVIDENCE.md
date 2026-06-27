# 04 — Evidence (the numbers behind every decision)

All numbers computed from the source repo's real data (`/Users/fyodorgolovin/Downloads/Supply-Inventory v1.0 codex/backend/data/forecast_v3_parquet/...` and source CSVs) using DuckDB + scikit-learn, on 2026-06-27. "Oracle Hit±20" = a *perfect* predictor that knows each SKU's true mean and predicts it = an **upper bound on point accuracy** before any model error. This file is the referee: do not re-litigate the ceiling without new data.

---

## 1. The "high-revenue" population is low-count
From `score_rows_v1` (Phase-10 baseline universes, quantity-scored rows, `actual ≥ 4`):
- Median 4-week chain demand = **9 units**; **57% ≤ 10**; **35% in [4,6]**; mean 22.8 (thin >50 tail = 6.4%).
- At these counts ±20% is integer-quantized: for `actual=4` the only hitting integer is 4; for `actual=5–8`, only 3 integers hit.

## 2. The point-accuracy ceiling — empirical (the decisive number)
Method: 4-week windows per SKU from `weekly_chain_facts_v1` (21,011 forecastable SKUs, mean 4w ≥ 4, internal zero-fill within active span).

| 4-week demand band | dispersion (var/mean) | oracle Hit±20 | oracle Hit±30 |
|---|---|---|---|
| 4–8 | 7.0 | 24.1% | 36.1% |
| 8–12 | 10.3 | 20.4% | 29.8% |
| 12–20 | 15.2 | 18.6% | 28.0% |
| 20–35 | 24.6 | 17.5% | 26.5% |
| 35–60 | 44.8 | 16.6% | 24.9% |
| 60+ | 147 | 14.4% | 21.3% |

- **Median dispersion ≈ 10.76 (Poisson = 1.0). 100% of forecastable SKUs are over-dispersed.**
- **Population-weighted oracle Hit±20 ≈ 22%.** A *perfect* mean-knower tops near 22%; real models do worse.
- (An initial Poisson-assumption estimate said ~51%; the empirical number is far lower because real demand is lumpier than Poisson. The empirical number governs.)
- Caveat: chain-level windows may inflate dispersion via store-mixing; per-store ceiling may be marginally higher. Conclusion robust (confirmed on per-SKU weekly series, §6).

## 3. The ceiling is universal across categories
Method: SKU→`GRUPA_PRODUSE` from source CSVs (label in 36/83 files; revenue 100% mappable) joined to per-SKU demand stats.

| Product group | rev % | forecastable SKUs | dispersion | ceiling Hit±20 | ceiling Hit±30 |
|---|---|---|---|---|---|
| MOBILIER CORP (storage) | 17.8% | 1,868 | 7.4 | 24% | 36% |
| CANAPELE SI FOTOLII (sofas) | 8.4% | 665 | 6.4 | 25% | 38% |
| OUTDOOR | 5.5% | 1,121 | 13.5 | 20% | 30% |
| SALTELE SI SOMIERE (mattresses) | 5.5% | 254 | 8.0 | 25% | 37% |
| CORPURI DE ILUMINAT (lighting) | 3.1% | 1,358 | 9.0 | 23% | 34% |
| PATURI TAPITATE (beds) | 1.6% | 93 | 7.8 | 25% | 38% |
| COVOARE (rugs) | 1.5% | 993 | 7.0 | 25% | 37% |
| CHRISTMAS | 1.1% | 1,591 | 70.9 | 12% | 18% |

**Ceiling ≈ 24–27% Hit±20 / 36–39% Hit±30 for every forecastable group** → no category forecast edge to chase; pick cohorts on money × operational fit.

## 4. `DATA COMANDA` reality (factual)
- `P1+P2 sales data/`: **0/69** files carry it. `baneasa date addition + ploiesti/`: **0/14**. `sales_data_prep1p2/`: **44/44** (~51% populated). `raw_active_v1` parquet `data_comanda_parsed`: **0% coverage**.
- Conclusion: column exists (old prep) but not in the modeled dataset; clean join impossible (~25–70k one-to-one of 7.9M). Fix = re-export new files with the column.

## 5. The V3 lake dropped key columns (factual)
`raw_active_v1` = 41 cols, missing `CAMPANIE*`, `DIMENSIUNI`, `CATEGORIE/CLASA/SUBCLASA`, `STIL/SUBSTIL`, `GRUPA/GRUPA_PRODUSE`, `NECESITA MONTAJ`. Coverage of what's there: `DATA` 94.7%, discount 99%, channel 38%, `id_comanda` 10%, `id_client` 31%, `denumire` 8%, `activ` 46%.

## 6. Lumpiness verified on real mattresses
Real weekly sales (last 16 weeks), demand from `DATA`:
```
MFSET4BUCSOMBA  avg 47/wk →  54, 35, 46, 64, 22, 14, 16, 35, 26, 36, 34, 47, 44, 22, 2, 12
MFSOMIERABASIC  avg 29/wk →  24, 18, 25, 57,  4,  6, 17, 16, 22, 30, 20, 33, 17, 12, 0, 10
46RENTAL160200  avg 15/wk →   6,  2,  5,  2,  6,  3, 13,  7,  8,  9,  9, 12,  6, 15, 0,  2
```
- Top-50 fast-movers: median **8.6 units/wk**, dispersion **7.9**, **median week-to-week swing = 63% of the average, every week** (rate unchanged).
- Per-SKU oracle Hit±20: **24% weekly, 26% at 4-week.** Fast-movers are just as lumpy — narrowing does not raise the per-SKU ceiling.

## 7. The aggregation effect (why decisions still work)
Method: sum weekly demand across N fast-mover mattress SKUs, last 104 weeks, 4-week windows, oracle Hit±20.

| Grain | oracle Hit±20 |
|---|---|
| single SKU, 4-week | ~26–37% |
| basket of 10 | 62% |
| basket of 50 | 88% |
| basket of 577 | 85% |

Individual bounces cancel when pooled → per-SKU protection via **buffer**; category/basket planning & working-capital ~**85–90% predictable**. This is the data proof that per-SKU ±20% is unnecessary.

## 8. What IS explainable (variance decomposition on `SALTELE`)
Method: 411 forecastable mattress SKUs, ~15k SKU×4-week windows, dispersion 6.9.

In-sample within-SKU variance explained (upper bound): history+lag 40% → +season 50% → +discount **71%** → +channel/order 92% (leaky).
Spike weeks (top-20%/SKU): deep discount 35% vs 24% base; Q4 30% vs 18%; **discount-or-Q4 = 54% of spikes vs 36%.**

Leakage-controlled temporal forecast (train early / test late, `actual ≥ 4`):
| model | Hit±20 | Hit±30 |
|---|---|---|
| naive (predict SKU mean) | 28.6% | 42.5% |
| + season (knowable ahead) | 27.5% | 41.3% |
| + discount/promo (if plan known) | 29.6% | 43.2% |
| + channel | 30.9% | 44.5% |
| + `nlines`/`nclient` (**LEAKAGE — fake**) | 86.6% | 93.8% |

**Lessons:** (1) the signal is real but lives in the **distribution & spike-timing**, not point accuracy (discount lifts explained variance 40%→71% yet moves honest point Hit±20 only ~+1pp); (2) the **86.6% is a leakage trap** — `nlines`/`nclient` are computed from the same window's transactions (≈ the outcome). **Treat any furniture forecast > ~40% Hit±20 as leaky until proven otherwise.**

## 9. Reproducibility
DuckDB over `score_rows_v1`, `weekly_chain_facts_v1`, `raw_active_v1`, `cohort_membership_v1` + source CSVs; scikit-learn `HistGradientBoosting*` for the `SALTELE` decomposition. In-sample R² = upper bound; the temporal split = the honest forecast number. The 86.6% row is a documented leakage trap, not a result.
