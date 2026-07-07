"""Forecast V4 — clean, de-duplicated rug demand ingest (replaces the V3 layer).

WHY THIS EXISTS
---------------
The V3 ``weekly_store_route_facts_v1`` layer that ``ingest_mattress`` reads from
DOUBLE-COUNTS rug sales. The old Mobexpert exports ship as P1/P2 file pairs that
are the *same transactions* with different column sets (proven: const 25 P1 units
== P2 units to the unit). V3 concatenated both; P2 has no channel column so its
duplicate rows became route_group='unknown' (~60% of all "demand"). The
duplication is *inconsistent per store* (Baneasa 2022 exists as 5 copies, most
stores 2x, Iasi/Craiova only-the-duplicate), so no scalar correction fixes it.

This module rebuilds weekly demand facts from ONE authoritative copy per
store-year of the RAW source CSVs, plus the fresh 2026 final-client export. Output
schema matches what ``rug_panel``/``kill_list`` consume:
    sku_id, store_code, demand_week_start, gross_units, returned_units,
    net_units, gross_value, gross_avg_unit_value

Run: python -m fev4.ingest_rugs
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import pandas as pd

from . import config

SRC = config.SOURCE_REPO / "P1+P2 sales data"
SRC_BANEASA = config.SOURCE_REPO / "baneasa date addition + ploiesti"
SALES_2026 = config.PROJECT_ROOT / "sales_2026"

# Files that live in the Baneasa/Ploiesti addition folder rather than P1+P2.
_ADDITION_FOLDER_FILES = {
    "ban 2022 p1 ( few missing columns).csv",
    "ban 24 p1 + date.csv",
    "ban 25 p1 + date.csv",
}

# --- Canonical file map: exactly ONE dated copy per (store, year) -------------
# by_grupa=True  -> file has GRUPA_PRODUSE, filter rugs by it
# by_grupa=False -> file lacks it, filter rugs by SKU-join to the rug universe
# All listed files carry a usable DATA column (verified). Baneasa 2023 is omitted
# on purpose: no copy of it carries any date (documented gap).
CANONICAL: list[dict] = [
    # store, file, by_grupa
    ("BANEASA",   "ban 2022 p1 ( few missing columns).csv", True),
    ("BANEASA",   "ban 24 p1 + date.csv",                   True),
    ("BANEASA",   "ban 25 p1 + date.csv",                   True),
    ("BRASOV",    "bra 22 full.csv",                         True),
    ("BRASOV",    "bras 23 p1 + date.csv",                   True),
    ("BRASOV",    "bras 24 p1 + date.csv",                   True),
    ("BRASOV",    "bras 25 p1 + date.csv",                   True),
    ("CONSTANTA", "const 22 p1.csv",                         True),
    ("CONSTANTA", "const 23 p1.csv",                         True),
    ("CONSTANTA", "const 24 p1.csv",                         True),
    ("CONSTANTA", "const 25 p1.csv",                         True),
    ("IASI",      "iasi 22 full.csv",                        True),
    ("IASI",      "iasi 23 p2 correct.csv",                  False),
    ("IASI",      "iasi 24 p2 correct.csv",                  False),
    ("IASI",      "iasi 25 p2 correct.csv",                  False),
    ("ORADEA",    "ora 22 p1.csv",                           True),
    ("ORADEA",    "ora 23 p1.csv",                           True),
    ("ORADEA",    "ora 24 p1.csv",                           True),
    ("ORADEA",    "ora 25 p1.csv",                           True),
    ("PIPERA",    "pip 22 p1.csv",                           True),
    ("PIPERA",    "pip 23 p1.csv",                           True),
    ("PIPERA",    "pip 24 p1.csv",                           True),
    ("PIPERA",    "pip 25 p1.csv",                           True),
    ("SIBIU",     "sib 22 p1.csv",                           True),
    ("SIBIU",     "sib 23 p1.csv",                           True),
    ("SIBIU",     "sib 24 p1.csv",                           True),
    ("SIBIU",     "sib 25 p1.csv",                           True),
    # non-dashboard stores: history only (no stock file) -> improve pooling/seasonality
    ("MILITARI",  "mil 22 p1.csv",                           False),
    ("MILITARI",  "mil 23 p1.csv",                           False),
    ("MILITARI",  "mil 24 p1.csv",                           False),
    ("MILITARI",  "mil 25 p1.csv",                           False),
    ("PANTELIMON", "pante 22 p1.csv",                        True),
    ("PANTELIMON", "pante p1 23.csv",                        True),
    ("PANTELIMON", "pante 24 p1.csv",                        True),
    ("PANTELIMON", "pante 25 p1.csv",                        True),
    ("PLOIESTI",  "ploiesti 22 p1.csv",                      True),
    ("PLOIESTI",  "plo 23 p1.csv",                           True),
    ("PLOIESTI",  "plo 24 25 p1.csv",                        True),
    ("TIMISOARA", "timi p1 22 23 24 25.csv",                 True),
    ("CRAIOVA",   "craiova 22-25 (1).csv",                   False),
]

# 2026 final-client export: one file per dashboard store (already de-duplicated).
STORE_2026 = {
    "BANEASA": "baneasa_sales_2026.csv", "BRASOV": "brasov_sales_2026.csv",
    "CONSTANTA": "constanta_sales_2026.csv", "IASI": "iasi_sales_2026.csv",
    "ORADEA": "oradea_sales_2026.csv", "PIPERA": "pipera_sales_2026.csv",
    "SIBIU": "sibiu_sales_2026.csv",
}

_DATE_FORMATS = "['%d.%m.%Y','%Y-%m-%d','%d.%m.%Y %H:%M:%S','%Y-%m-%d %H:%M:%S']"


def _rug_skus(con: duckdb.DuckDBPyConnection) -> None:
    """Register the rug SKU universe (for files lacking GRUPA_PRODUSE)."""
    skus = pd.read_parquet(config.cohort_paths(config.RUGS_SLUG)["sku_attr"])["sku"].astype(str).tolist()
    con.execute("CREATE OR REPLACE TEMP TABLE rugset AS SELECT UNNEST(?) AS cod", [skus])


def _one_file(con, path, store, by_grupa) -> pd.DataFrame:
    """Weekly SKU demand from a single source file, filtered to rugs."""
    where = ("lower(\"GRUPA_PRODUSE\") LIKE '%covoar%'" if by_grupa
             else "\"COD ARTICOL\" IN (SELECT cod FROM rugset)")
    q = f"""
    WITH r AS (
        SELECT "COD ARTICOL" AS sku_id,
               try_strptime(CAST("DATA" AS VARCHAR), {_DATE_FORMATS})::DATE AS d,
               CAST("CANTITATE FACTURATA" AS DOUBLE) AS qty,
               CAST("VALOARE FACTURATA" AS DOUBLE) AS val
        FROM read_csv_auto(?, header=true, union_by_name=true, all_varchar=true, ignore_errors=true)
        WHERE {where} AND "COD ARTICOL" IS NOT NULL
    )
    SELECT sku_id, date_trunc('week', d) AS demand_week_start,
           SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END)            AS gross_units,
           SUM(CASE WHEN qty < 0 THEN -qty ELSE 0 END)           AS returned_units,
           SUM(qty)                                              AS net_units,
           SUM(CASE WHEN qty > 0 THEN val ELSE 0 END)            AS gross_value
    FROM r WHERE d IS NOT NULL
    GROUP BY sku_id, date_trunc('week', d)
    """
    df = con.execute(q, [str(path)]).df()
    df["store_code"] = store
    return df


def build_weekly_facts() -> tuple[pd.DataFrame, dict]:
    con = duckdb.connect(); con.execute("PRAGMA threads=4")
    _rug_skus(con)
    frames, lineage = [], []
    for store, fname, by_grupa in CANONICAL:
        folder = SRC_BANEASA if fname in _ADDITION_FOLDER_FILES else SRC
        df = _one_file(con, folder / fname, store, by_grupa)
        frames.append(df)
        lineage.append({"store": store, "file": fname, "rows": int(len(df)),
                        "units": float(df["gross_units"].sum()),
                        "years": sorted(df["demand_week_start"].dropna().map(lambda x: x.year).unique().tolist())})
    for store, fname in STORE_2026.items():
        df = _one_file(con, SALES_2026 / fname, store, True)
        frames.append(df)
        lineage.append({"store": store, "file": f"sales_2026/{fname}", "rows": int(len(df)),
                        "units": float(df["gross_units"].sum()),
                        "years": sorted(df["demand_week_start"].dropna().map(lambda x: x.year).unique().tolist())})
    con.close()

    facts = pd.concat(frames, ignore_index=True)
    facts["demand_week_start"] = pd.to_datetime(facts["demand_week_start"])
    # a (sku, store, week) can appear in two canonical files only across a year
    # boundary; collapse defensively.
    facts = (facts.groupby(["sku_id", "store_code", "demand_week_start"], as_index=False)
             [["gross_units", "returned_units", "net_units", "gross_value"]].sum())
    facts["gross_avg_unit_value"] = facts["gross_value"] / facts["gross_units"].where(facts["gross_units"] > 0)
    facts = facts.sort_values(["sku_id", "store_code", "demand_week_start"]).reset_index(drop=True)
    return facts, {"canonical_files": len(CANONICAL), "lineage": lineage}


def extend_sku_attributes(facts: pd.DataFrame) -> int:
    """Add attribute rows for SKUs that appear only in the 2026 export."""
    paths = config.cohort_paths(config.RUGS_SLUG)
    attrs = pd.read_parquet(paths["sku_attr"])
    known = set(attrs["sku"].astype(str))
    new = sorted(set(facts["sku_id"].astype(str)) - known)
    if not new:
        return 0
    con = duckdb.connect()
    files = [str(SALES_2026 / f) for f in STORE_2026.values()]
    add = con.execute("""
        SELECT DISTINCT "COD ARTICOL" AS sku, "DENUMIRE ARTICOL" AS denumire_articol,
               "DIMENSIUNI" AS dimensiuni, "GRUPA_PRODUSE" AS grupa_produse
        FROM read_csv_auto(?, header=true, union_by_name=true, all_varchar=true, ignore_errors=true)
        WHERE "COD ARTICOL" IN (SELECT UNNEST(?))
    """, [files, new]).df()
    con.close()
    add = add.groupby("sku", as_index=False).first()
    for c in attrs.columns:
        if c not in add.columns:
            add[c] = None
    add["leakage_class"] = "historical_safe_static"
    attrs = pd.concat([attrs, add[attrs.columns]], ignore_index=True)
    attrs.to_parquet(paths["sku_attr"], index=False)
    return len(new)


def run() -> dict:
    paths = config.cohort_paths(config.RUGS_SLUG)
    facts, lin = build_weekly_facts()
    n_new_attr = extend_sku_attributes(facts)
    facts.to_parquet(paths["weekly"], index=False)

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "de-duplicated raw CSVs (one copy/store-year) + 2026 final-client export",
        "rows": int(len(facts)), "skus": int(facts["sku_id"].nunique()),
        "stores": int(facts["store_code"].nunique()),
        "week_min": str(facts["demand_week_start"].min().date()),
        "week_max": str(facts["demand_week_start"].max().date()),
        "gross_units_total": float(facts["gross_units"].sum()),
        "new_2026_skus_added_to_attrs": n_new_attr,
        **lin,
    }
    (paths["dir"] / "ingest_rugs_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    m = run()
    print("Forecast V4 — clean rug demand ingest")
    print(f"  {m['rows']:,} weekly rows | {m['skus']:,} SKUs | {m['stores']} stores")
    print(f"  span {m['week_min']} -> {m['week_max']} | gross units {m['gross_units_total']:,.0f}")
    print(f"  new 2026 SKUs added to attributes: {m['new_2026_skus_added_to_attrs']}")
    print("  per-store units (canonical + 2026):")
    by_store: dict = {}
    for l in m["lineage"]:
        by_store.setdefault(l["store"], 0.0)
        by_store[l["store"]] += l["units"]
    for s, u in sorted(by_store.items()):
        print(f"    {s:11s} {u:8,.0f}")


if __name__ == "__main__":
    main()
