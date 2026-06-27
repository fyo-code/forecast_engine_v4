# 03 — Data Specification & Source Map

What the data means, where it lives, and the caveats. This file wins on data interpretation. (Distilled from the source repo's `FORECAST_V2_DATA_DICTIONARY_AND_BUSINESS_RULES.md`, `FORECAST_V3_DATA_SOURCE_GUIDE.md`, and `forecast_data/csv_spec.md`.)

---

## 1. Where the data lives (source repo — read-only)

Root: `/Users/fyodorgolovin/Downloads/Supply-Inventory v1.0 codex`

| Folder | Role for V4 |
|---|---|
| `P1+P2 sales data/` (69 CSVs) | **Active main sales export.** Per store/year, split P1 (channel/montage/supplier/order#) and P2 (catalog/current-snapshot/campaign/dimensions). Primary demand source. |
| `baneasa date addition + ploiesti/` (14 CSVs) | **Active patch** for Baneasa 2022–2025 and Ploiesti P2. |
| `sales_data_prep1p2/` (44 CSVs) | **Old prep** — the ONLY place with `DATA COMANDA` (~51% populated). Use for order-date recovery via confidence joins only. |
| `P1 + P2 archive/` | **DO NOT USE.** Duplicates, misleading replacements, superseded files. |
| `backend/data/forecast_v3_parquet/` | V3 processed layers (useful for reference/evidence, but the lake dropped key columns — see §5). |

Convenient pre-computed reference: the V3 parquet `weekly_chain_facts_v1`, `score_rows_v1`, `cohort_membership_v1` (saltele text candidates), `raw_active_v1`. Useful for analysis but **re-ingest from CSVs for V4 modeling** to keep all columns.

Data is **not copied into V4 yet.** Pull the mattress slice when building v1.

## 2. Non-negotiable business semantics (confirmed by Fyo)

- **Stock ≠ sellability.** Mobexpert can factory-order any `ACTIV` SKU even at 0 stock. → **Sales history is true, un-censored demand** (no stockout-censoring to correct). Dead-stock/decay = `ACTIV` + sales-trend, never stock level.
- **`ACTIV` / `ACTIV ONLINE` / `VECHIME IN COLECTIE` = current snapshots, not historical.** Live operational gates only; **never** historical features (leakage).
- **`DATA COMANDA` = order/demand-intent date** (preferred). **`DATA` = invoice/fulfillment/fallback date.** Lag is short for accessories/in-stock (incl. mattresses), long (~2 months) for custom orders.
- **Demand target = gross positive `CANTITATE FACTURATA`.** Negative = returns/refunds (separate context, never netted away). `gross_units=max(qty,0)`, `returned_units=abs(min(qty,0))`, `net=gross-returned`.
- **`GRUPA MEDIU VANZARE` = primary channel** (`ONLINE` / `OFFLINE` / `OUTLET`). `RAION` = product-area context, not a channel substitute (`RAION=ONLINE` usually aligns with online).
- **`Reducere %`** = discount; parse `discount_pct = abs(value)` (`-0.2` → 20%, `-0.0` → none). Sanitize non-finite / impossible values.
- Filter non-product/service rows (transport, livrare, montaj, servicii) from the demand target.

## 3. Column dictionary (the ones that matter for V4)

| Column | Meaning / use |
|---|---|
| `COD ARTICOL` | SKU code (primary product id). |
| `MAGAZIN` | Store (normalize to canonical id — see §6). |
| `DATA` / `DATA COMANDA` | invoice/fulfillment date / order date. Keep both + lag where available. |
| `CANTITATE FACTURATA` | line quantity; negative = return. |
| `VALOARE FACTURATA` | final line value paid (incl. discount + VAT); negative on some returns. |
| `Reducere %` | discount depth (abs). **Strong demand-shift signal.** |
| `GRUPA MEDIU VANZARE` | channel/route (online/offline/outlet). |
| `CAMPANIE`, `CAMPANIE BF`, `CAMPANIE SELECTATA` | campaign labels; `CAMPANIE BF` is the authoritative Black Friday flag with dates. Guard: `CAMPANIE` mixes promos with product-program labels. |
| `GRUPA_PRODUSE` | product group — **best-covered category proxy (36/83 files)**; use for cohort/family. |
| `CATEGORIE` / `CLASA` / `SUBCLASA` | product hierarchy (sparse coverage: 2/4/11 files). |
| `DENUMIRE ARTICOL` | product name/text (mattress identification, dims, family). |
| `DIMENSIUNI` | size (size buckets, bulky-item behavior). |
| `NECESITA MONTAJ` | `NMU`=no montage, `NMD`=montage required (friction/lead signal). |
| `FURNIZOR` / `FURNIZOR EXT` / `ID FURNIZOR` | supplier (lead-time / family context). |
| `ID FACTURA` / `ID COMANDA` / `NR COMANDA` / `ID CLIENT` | identity & order structure; `ID CLIENT` enables B2B/contract-order detection. Use for identity/merge, not blind features. |
| `ACTIV` / `ACTIV ONLINE` / `VECHIME IN COLECTIE` | current-snapshot live gates only (leakage if used historically). |

## 4. Merge-confidence tiers (don't blindly merge)

Tier A: invoice-line identity (`ID FACTURA` + SKU/store/measures). Tier B: exact commercial fingerprint (one-to-one only). Tier C: order-bridge / SKU-level aggregate context. Tier X: no merge — keep `unknown`. **For V4 v1, prefer attaching static product attributes at SKU level** (a SKU's category/dimensions/montage/family are constant), which sidesteps risky row-level P1↔P2 joins.

## 5. Known data caveats (verified — see `04_EVIDENCE.md`)

- **`DATA COMANDA` absent in active exports** (0/69 P1+P2, 0/14 patch; present 44/44 in old prep). Re-export new files **with `DATA COMANDA`** to upgrade demand timing. Not blocking for mattresses (short lag → `DATA ≈ DATA COMANDA`).
- **The V3 lake (`raw_active_v1`, 41 cols) dropped:** `CAMPANIE*`, `DIMENSIUNI`, `CATEGORIE/CLASA/SUBCLASA`, `STIL/SUBSTIL`, `GRUPA/GRUPA_PRODUSE`, `NECESITA MONTAJ`. They exist in the CSVs — V4 must re-ingest keeping them.
- **Field coverage in the lake:** `DATA` 95%, discount 99%, channel 38%, `id_comanda` 10%, `id_client` 31%, `denumire` 8% (P1/P2 split → fragmented; fix via SKU-level attribute attachment).
- **Category columns are sparse at file level:** `GRUPA_PRODUSE` (36/83) is the workhorse; `CATEGORIE` only 2/83.

## 6. Store normalization

`M & D RETAIL <CITY> SRL` / `MOBEXPERT BANEASA SRL` → canonical ids: `constanta, brasov, pipera, pantelemon, baneasa, sibiu, oradea, ploiesti, iasi, timisoara`. Store types: hyperstore (Constanta, Brasov, Pipera, Pantelemon, Baneasa, Sibiu), hybrid (Iasi), smaller (Oradea, Ploiesti, Timisoara).

## 7. Data to get (raises the ceiling)

1. **Forward promo/campaign calendar** (turns discount from explanatory to predictive — highest leverage).
2. **`DATA COMANDA` re-exported** on new P1/P2 files.
3. **Current stock-on-hand + supplier lead time** (unlocks full target-vs-stock reorder, dynamic par-levels, dead-stock).
4. **B2B/contract-order data** (via `ID CLIENT`).
