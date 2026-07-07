# Demo Prep & Data Asks (post-Phase-4)

Date: 2026-07-06. State: app built and running on 7 stores; next phase = demo to V, then feedback.
This captures decisions/asks discussed but not yet elsewhere on disk.

## Run the app
`cd forecast_engine_v4 && .venv/bin/streamlit run app/streamlit_app.py` (default port 8501, or `--server.port 8601`).
Rebuild its data after any new export: `python -m fev4.stock_ingest && python -m fev4.demo_data`.

## Current headline numbers (7 stores, clean+2026 data, defaults LT30/SS7/P90) — updated 2026-07-08
- Segments (engine): **critical 8, attention 91, ok 30, overstock 8,310** (critical was 274 before the de-dup fix + materiality gate — see `BACKTEST_2026_FINDINGS.md`).
- Reorder book: **129 SKUs, ~201 units** (lean; 8 genuine criticals + 18 accelerating).
- Trapped capital: ~6.65M lei in dead/dying stock; **~3.65M is truly-dead** (0 sales in 2026 — the number to quote).
- 2026 out-of-sample: market −23%; forecast market-neutral bias −1.8%; P90 coverage 92% on unseen data.

## Data asks (in priority order)
1. ~~**2026 sales, same 7 stores**~~ **DELIVERED 2026-07-07** (`sales_2026/*.csv`, one file/store, final-client, exactly the requested columns). Integrated → gap closed; engine now runs to Jul 2026.
2. **Re-pull stock for Constanta/Iasi/Oradea to current month** (they stop at Dec 2025; the other 4 reach Jun 2026 — mixed as-of dates shown per row). Now the top open ask.
3. **Transit / on-order quantity** (Stoc Tranzit) — in the PM's own Days-of-Cover formula; currently assumed 0.
4. **Product-state codes** (ACU/RPD/COM/OUC/WWW) — enables the PM-spec state filter (disabled now).

## Decision: inter-store transfers
Transfers are recorded as "sales" (stores are separate legal entities, moved at acquisition cost). **Exclude them from the demand/sales export** — they are internal logistics, not consumer demand; including them creates fake spikes, wrong replenishment targets, and network double-count. Fyo already filters to final-client — correct, keep as-is.
- Where transfers WOULD help (separate, later, never mixed into demand): (a) reconciliation precision (explains part of the 2% receipt noise + "23% sold without store stock"); (b) **future "network rebalancing" feature** — recommend transfer A→B instead of buying, turning kill-list dead stock into supply for stockout stores. Genuinely valuable future feature; needs transfer data as a distinct tagged flow.

## Demo-to-V asks (to confirm, non-blocking)
- Confirm the internal module's actual rules (replica assumed: cover=stock/(sales/120d); critical<LT, urgent<LT+SS; replace-what-sold qty).
- Real rug lead times (per supplier/typical) — the tool takes it as input (slider).
- Does the module suggest quantities or only urgency?
- How often does the PM actually place orders?
- Intro to the rug PM for a real test.

## Known caveats to state at the demo
Monthly EOM stock (staleness shown); sales edge Dec 2025 for 2026-stock stores; module comparison is a replica; artificial-grass (`GALAGOSMP`) sits in the rug group — a data-semantics find worth surfacing.
