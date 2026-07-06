"""Paths and constants for Forecast Engine V4.

Source data lives in the read-only V2/V3 repo. V4 reads from it and writes
clean, derived outputs into this project's (gitignored) ``data/`` dir.
"""

from __future__ import annotations

from pathlib import Path

# --- Source (read-only reference repo) ---
SOURCE_REPO = Path("/Users/fyodorgolovin/Downloads/Supply-Inventory v1.0 codex")
V3_PARQUET = SOURCE_REPO / "backend" / "data" / "forecast_v3_parquet"

# Verified, de-duplicated demand layer (P1/P2 double-count already resolved by V3,
# conservation-checked). We source DEMAND from here on purpose — see 02_ENGINE_ARCHITECTURE.md.
WEEKLY_STORE_ROUTE_GLOB = str(V3_PARQUET / "weekly_store_route_facts_v1" / "*.parquet")

# Active source CSVs (for the static product attributes the V3 lake dropped).
ACTIVE_CSV_GLOBS = [
    str(SOURCE_REPO / "P1+P2 sales data" / "*.csv"),
    str(SOURCE_REPO / "baneasa date addition + ploiesti" / "*.csv"),
]

# --- Cohort ---
COHORT_GROUP = "SALTELE SI SOMIERE"  # GRUPA_PRODUSE value identifying mattresses + bed bases

# Static, leakage-safe product attributes (constant per SKU). Source CSV column -> output name.
ATTR_COLUMNS: dict[str, str] = {
    "GRUPA_PRODUSE": "grupa_produse",
    "CATEGORIE": "categorie",
    "CLASA": "clasa",
    "SUBCLASA": "subclasa",
    "DIMENSIUNI": "dimensiuni",
    "NECESITA MONTAJ": "necesita_montaj",
    "FURNIZOR": "furnizor",
    "STIL": "stil",
    "SUBSTIL": "substil",
    "DENUMIRE ARTICOL": "denumire_articol",
}

# --- V4 outputs ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
MATTRESS_DIR = DATA_DIR / "mattress_v1"
SKU_ATTR_OUT = MATTRESS_DIR / "mattress_sku_attributes.parquet"
WEEKLY_FACTS_OUT = MATTRESS_DIR / "mattress_weekly_facts.parquet"
MANIFEST_OUT = MATTRESS_DIR / "build_manifest.json"


def cohort_paths(slug: str) -> dict:
    """Output paths for a category cohort slice (e.g. slug='rugs_v1')."""
    d = DATA_DIR / slug
    return {
        "dir": d,
        "sku_attr": d / "sku_attributes.parquet",
        "weekly": d / "weekly_facts.parquet",
        "manifest": d / "build_manifest.json",
    }


# --- Rugs (COVOARE) cohort ---
RUGS_GROUP = "COVOARE"
RUGS_SLUG = "rugs_v1"

# Monthly store-warehouse stock snapshots (wide CSVs, one per store).
# First three: original May-2026 export (through Dec 2025). Last four: fresh
# July-2026 export dropped into this repo (through June 2026).
STORE_STOCK_FILES = {
    "CONSTANTA": SOURCE_REPO / "new_stock_data_20may" / "const_magazin_stock.csv",
    "IASI": SOURCE_REPO / "new_stock_data_20may" / "iasi_magazin_stock.csv",
    "ORADEA": SOURCE_REPO / "new_stock_data_20may" / "oradea_magazin_stock.csv",
    "BANEASA": PROJECT_ROOT / "stock_magazin_baneasa.csv",
    "PIPERA": PROJECT_ROOT / "stock_magazin_pipera.csv",
    "SIBIU": PROJECT_ROOT / "stock_magazin_sibiu.csv",
    "BRASOV": PROJECT_ROOT / "stock_magazin_brasov.csv",
}
RO_MONTHS = {
    "IANUARIE": 1, "FEBRUARIE": 2, "MARTIE": 3, "APRILIE": 4, "MAI": 5, "IUNIE": 6,
    "IULIE": 7, "AUGUST": 8, "SEPTEMBRIE": 9, "OCTOMBRIE": 10, "NOIEMBRIE": 11, "DECEMBRIE": 12,
}

# Demand timing: mattresses are short-lag, so DATA ≈ DATA COMANDA (see 03_DATA_SPEC.md).
DEMAND_DATE_SOURCE = "DATA (invoice/fulfillment; short-lag for mattresses)"
KNOWN_ROUTES = ("offline", "online", "outlet")

# --- Phase A: demand engine ---
# Modeling cohort (chain fast-movers); thresholds are as-of the train cutoff (leakage-safe).
FASTMOVER_MIN_ACTIVE_WEEKS = 26
FASTMOVER_MIN_UNITS = 52
FASTMOVER_LOOKBACK_WEEKS = 52
# Wider eval cohort (to demonstrate pooling helps the sparse tail).
MODEL_MIN_ACTIVE_WEEKS = 13

LAGS = (1, 2, 4, 8, 13)
ROLLING = (4, 8, 13)
QUANTILES = (0.1, 0.5, 0.9, 0.95)

# Temporal holdout: last N weeks are the test period (forecasts use only earlier data).
TEST_WEEKS = 26

# --- Phase B: reorder policy (confirmed defaults; all overridable) ---
PROTECTION_WINDOW_WEEKS = 2     # weekly review + ~1wk lead (lead time unknown -> default)
SERVICE_LEVEL = 0.95

MODEL_OUT_DIR = MATTRESS_DIR
CHAIN_FORECAST_OUT = MATTRESS_DIR / "chain_weekly_forecast.parquet"
STORE_SHARES_OUT = MATTRESS_DIR / "store_allocation_shares.parquet"
DEMAND_METRICS_OUT = MATTRESS_DIR / "demand_model_metrics.json"
