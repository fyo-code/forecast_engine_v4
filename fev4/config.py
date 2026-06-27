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

# Demand timing: mattresses are short-lag, so DATA ≈ DATA COMANDA (see 03_DATA_SPEC.md).
DEMAND_DATE_SOURCE = "DATA (invoice/fulfillment; short-lag for mattresses)"
KNOWN_ROUTES = ("offline", "online", "outlet")
