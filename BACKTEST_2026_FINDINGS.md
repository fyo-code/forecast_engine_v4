# 2026 Out-of-Sample Backtest & Data Audit — Findings

Date: 2026-07-07. Data: clean de-duplicated demand (2022–2025) + real Jan–Jun 2026
final-client sales, 7 dashboard stores. Brutally honest; no numbers invented.
Reproduce: `python -m fev4.ingest_rugs && python -m fev4.backtest_2026 && python -m fev4.backtest_2026_value`.

## 0. The finding that reframes everything: the demand was double-counted 2–3×

The V3 layer the engine trained on **summed the P1 and P2 exports, which are the
same transactions** with different columns (const 25: P1 = P2 = 4,106 units, to
the unit). P2 lacks the channel field, so its duplicate rows became
`route_group='unknown'` — 60% of all "demand." Duplication was **inconsistent by
store**: Baneasa existed as 5 copies (~3×), most stores 2×, Iasi/Craiova had only
the duplicate survive — so no scalar fix worked. Rebuilt from one authoritative
copy per store-year (`fev4/ingest_rugs.py`).

- Old (double-counted) full-year 2025 rug units: **79,994**. Clean: **38,072** → engine was training on **2.1× inflated demand**.
- This alone drove: inflated sell-rates → artificially low days-of-cover → the "too many critical" flags → over-ordering.
- It also faked a "-76% YoY" that is really **-23%** once de-duplicated — matching the ~20% firm-wide 2026 decline Fyo flagged. The market context checked out; the data was lying.

## 1. Market drift (the number to hold everything against)

Real rug demand, H1-2026 vs H1-2025, same 7 stores: **−23.3%**. So a 2025-trained
engine *should* over-predict ~**+30%** from drift alone, before any model error.
Do not "cap" the model for over-prediction that is really the market shrinking.

## 2. Is the forecast any good? (frozen at 2025, pure out-of-sample)

Predicting each 2026 month from the **Dec-2025 model only** (no 2026 feedback):

| metric | value | read |
|---|---|---|
| aggregate bias | **+28.0%** | over-predicts... |
| bias after removing −23% market drift | **−1.8%** | ...but market-neutral it is **essentially unbiased** |
| P90 coverage (unseen) | **92%** (target 90%) | calibration **holds out-of-sample** |
| P95 coverage | 96% | slightly conservative, fine |
| mover bias (market-neutral) | ~+15% | strong 2025 sellers cooled more than the model expected |
| sparse bias (market-neutral) | ~−5% | fine |

**Verdict:** the engine is well-calibrated and, net of the market, aggregate-unbiased.
Fyo's warning was correct — the raw over-prediction is the market decline, not a
broken model. The one real bias: **movers over-predict ~15%** (the model doesn't
know the market shrank; see §5).

## 3. Where it is weak (honest limits)

- **Ranking is barely better than chance.** Spearman(pred, actual) at SKU×month ≈ 0.
  Concentration is the fairer read: the **top-decile of predicted rows holds ~15%
  of actual demand** (random = 10%). So the model concentrates demand only weakly —
  the lumpy-demand ceiling, exactly as the V4 thesis predicted. It gets the
  *distribution* and *aggregate* right; it cannot reliably say *which* specific rug
  outsells which.
- **24% of 2026 demand came from SKUs the model marked "inactive"** at the cutoff
  (no sale in the trailing 2 weeks). The 2-week active window is too tight for
  demand this sporadic → a real, fixable blind spot (§5).
- **Rolling (live app) under-predicts −21%** and decays through the year (Jun −64%),
  because the trailing rate chases a falling market *and* the tight active window
  misses reactivations. Good news for "too many critical" (live rates run low, not
  high); bad news for using it to size orders late in a decline.

## 4. Value tests on unseen 2026

**Dead-stock false-kill:** of 5,614 SKU×stores called "dead" at end-2025, **25%
(1,428) sold something in 2026** — but only **2,299 units total** (~1.6 units each
over 6 months). So "dead" is directionally right on *trapped capital* but too
absolute as a label. Defensible number: the **~3.65M lei that sold nothing** is
truly trapped; the ~2.03M on "revived" SKUs is barely-moving, not dead. (2026 is a
down year, so 25% is a floor.)

**Decision head-to-head (Dec-2025 vantage → actual Jan-2026 demand of 2,408 units):**

| policy | ordered | service | overstock (vs window) | unmet |
|---|---|---|---|---|
| engine (P90 order-up-to) | 7,206 u | 79% | 9,191 u | 459 |
| module (flat-rate) | 3,376 u | 73% | 4,314 u | 623 |

**Uncomfortable but important:** the engine's P90 buffer buys +6pp service for ~2×
the inventory. On this business **stockout ≠ lost sale** (sells at 0 stock), so that
+6pp service is nearly worthless while the extra overstock is real trapped capital.
**The P90 order-up-to policy optimizes the wrong thing for rugs.** The forecast is
good; the *decision rule bolted on top* should minimize trapped capital, not
maximize service.

## 5. What to change (evidence-justified; no blind capping)

1. **Reframe "critical" (fixes the complaint honestly).** All 94 criticals have 0
   store stock and sell ~0.22 u/mo. "0 store stock = emergency" is false for rugs.
   Gate critical/urgent on *material forward demand*; a 0-stock SKU selling once a
   quarter is a slow reorder candidate, not a fire.
2. **Lean out the order policy.** Default the order target to a service level that
   reflects stockout≠lost-sale (not P90). Keep it a knob; show the trapped-capital
   tradeoff so the PM chooses with eyes open.
3. **Widen the "active" window** (2w → ~13w) so sporadic sellers get a forecast —
   recovers a chunk of the 24% blind spot.
4. **Soften "dead"** to "sold ≤ N units in 6 months," report truly-dead capital
   (0 sales) separately from barely-moving.
5. **Market self-corrects on refresh.** With 2026 integrated, the rolling rate
   already tracks the −23% decline (critical count fell 274 → 94). No hard drift
   factor needed today; revisit if a store's stock lags the sales edge.
