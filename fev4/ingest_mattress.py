"""Forecast V4 — Step 1: clean ingestion of the SALTELE SI SOMIERE (mattress) slice.

Design (see 02_ENGINE_ARCHITECTURE.md §6 and 03_DATA_SPEC.md):

- DEMAND is sourced from the V3 verified, de-duplicated weekly demand layer
  ``weekly_store_route_facts_v1`` to avoid the P1/P2 double-count trap (P1 and P2
  are column-splits of the same transactions). That layer is conservation-checked.
- The static product attributes the V3 lake dropped (group, category, dimensions,
  montage, supplier, style, name) are attached at SKU level from the source CSVs.
  These are constant per SKU, so SKU-level attachment is leakage-safe and avoids a
  fragile row-level P1<->P2 merge.

Outputs (into data/mattress_v1/):
- ``mattress_sku_attributes.parquet`` — one row per mattress SKU, static attributes.
- ``mattress_weekly_facts.parquet``   — SKU x store x week observed demand facts.
- ``build_manifest.json``             — counts, lineage, and conservation checks.

Run: ``python -m fev4.ingest_mattress``
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import duckdb
import pandas as pd

from . import config


# --------------------------------------------------------------------------- #
# Cohort + attributes
# --------------------------------------------------------------------------- #
def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    return con


def select_cohort_skus(con: duckdb.DuckDBPyConnection, group: str = config.COHORT_GROUP) -> list[str]:
    """Cohort SKUs = those whose modal GRUPA_PRODUSE is the requested group."""
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE sku_group AS
        WITH raw AS (
            SELECT "COD ARTICOL" AS sku, UPPER(TRIM("GRUPA_PRODUSE")) AS grp
            FROM read_csv({config.ACTIVE_CSV_GLOBS!r},
                          union_by_name=true, all_varchar=true, ignore_errors=true, header=true)
            WHERE "COD ARTICOL" IS NOT NULL
              AND "GRUPA_PRODUSE" IS NOT NULL AND TRIM("GRUPA_PRODUSE") <> ''
        ),
        ranked AS (
            SELECT sku, grp, ROW_NUMBER() OVER (PARTITION BY sku ORDER BY COUNT(*) DESC) rn
            FROM raw GROUP BY sku, grp
        )
        SELECT sku, grp FROM ranked WHERE rn = 1
        """
    )
    skus = con.execute(
        "SELECT sku FROM sku_group WHERE grp = ? ORDER BY sku", [group]
    ).df()["sku"].tolist()
    con.execute("CREATE OR REPLACE TEMP TABLE mat AS SELECT UNNEST(?) AS sku", [skus])
    return skus


# Backward-compatible alias (Phase A/B/C modules and tests referenced the old name).
select_mattress_skus = select_cohort_skus


def _mode(series: pd.Series) -> object:
    s = series.dropna()
    s = s[s.astype(str).str.strip() != ""]
    m = s.mode()
    return m.iloc[0] if not m.empty else None


def build_sku_attributes(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Modal static attribute per mattress SKU, pulled from the source CSVs.

    All attributes here are constant per SKU and historically safe (no current-snapshot
    fields, no per-transaction values).
    """
    select_cols = ", ".join(f'"{src}"' for src in config.ATTR_COLUMNS)
    rows = con.execute(
        f"""
        SELECT "COD ARTICOL" AS sku, {select_cols}
        FROM read_csv({config.ACTIVE_CSV_GLOBS!r},
                      union_by_name=true, all_varchar=true, ignore_errors=true, header=true)
        WHERE "COD ARTICOL" IN (SELECT sku FROM mat)
        """
    ).df()
    rows = rows.rename(columns={src: dst for src, dst in config.ATTR_COLUMNS.items()})
    out_cols = list(config.ATTR_COLUMNS.values())
    attrs = rows.groupby("sku", as_index=False)[out_cols].agg(_mode)
    attrs["leakage_class"] = "historical_safe_static"
    return attrs.sort_values("sku").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Weekly demand facts (SKU x store x week)
# --------------------------------------------------------------------------- #
def build_weekly_facts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """SKU x store x week observed demand facts from the verified V3 demand layer."""
    off, onl, out = config.KNOWN_ROUTES
    df = con.execute(
        f"""
        WITH w AS (
            SELECT * FROM read_parquet('{config.WEEKLY_STORE_ROUTE_GLOB}')
            WHERE sku_id IN (SELECT sku FROM mat)
        )
        SELECT
            sku_id, store_code, demand_week_start,
            SUM(gross_positive_units)                                            AS gross_units,
            SUM(returned_units)                                                  AS returned_units,
            SUM(net_units)                                                       AS net_units,
            SUM(gross_positive_value)                                            AS gross_value,
            SUM(sales_line_count)                                                AS sales_lines,
            SUM(positive_line_count)                                             AS positive_lines,
            SUM(return_line_count)                                               AS return_lines,
            SUM(CASE WHEN route_group = '{off}' THEN gross_positive_units ELSE 0 END) AS gross_units_offline,
            SUM(CASE WHEN route_group = '{onl}' THEN gross_positive_units ELSE 0 END) AS gross_units_online,
            SUM(CASE WHEN route_group = '{out}' THEN gross_positive_units ELSE 0 END) AS gross_units_outlet,
            SUM(CASE WHEN route_group NOT IN ('{off}','{onl}','{out}') OR route_group IS NULL
                     THEN gross_positive_units ELSE 0 END)                       AS gross_units_other,
            SUM(avg_discount_pct * discount_observed_line_count)
                / NULLIF(SUM(discount_observed_line_count), 0)                   AS discount_avg_pct,
            SUM(discounted_line_count)                                           AS discounted_lines,
            SUM(discount_observed_line_count)                                    AS discount_observed_lines
        FROM w
        GROUP BY sku_id, store_code, demand_week_start
        ORDER BY sku_id, store_code, demand_week_start
        """
    ).df()
    df["demand_week_start"] = pd.to_datetime(df["demand_week_start"])
    df["gross_avg_unit_value"] = df["gross_value"] / df["gross_units"].where(df["gross_units"] > 0)
    return df


# --------------------------------------------------------------------------- #
# Conservation checks
# --------------------------------------------------------------------------- #
def conservation_checks(con: duckdb.DuckDBPyConnection, weekly: pd.DataFrame) -> dict:
    src = con.execute(
        f"""
        SELECT SUM(gross_positive_units) gross, SUM(returned_units) ret,
               COUNT(DISTINCT sku_id) skus, COUNT(DISTINCT store_code) stores
        FROM read_parquet('{config.WEEKLY_STORE_ROUTE_GLOB}')
        WHERE sku_id IN (SELECT sku FROM mat)
        """
    ).df().iloc[0]

    checks = {
        "gross_units_source": float(src["gross"]),
        "gross_units_facts": float(weekly["gross_units"].sum()),
        "returned_units_source": float(src["ret"]),
        "returned_units_facts": float(weekly["returned_units"].sum()),
        "route_split_reconciles": bool(
            (
                weekly[["gross_units_offline", "gross_units_online", "gross_units_outlet", "gross_units_other"]]
                .sum(axis=1)
                .round(6)
                == weekly["gross_units"].round(6)
            ).all()
        ),
        "no_negative_gross": bool((weekly["gross_units"] >= 0).all()),
    }
    checks["gross_conserved"] = abs(checks["gross_units_source"] - checks["gross_units_facts"]) < 1e-3
    checks["returns_conserved"] = abs(checks["returned_units_source"] - checks["returned_units_facts"]) < 1e-3
    checks["all_passed"] = all(
        checks[k] for k in ("gross_conserved", "returns_conserved", "route_split_reconciles", "no_negative_gross")
    )
    return checks


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclass
class BuildSummary:
    built_at: str
    cohort_group: str
    n_mattress_skus: int
    n_skus_with_demand: int
    n_stores: int
    n_weekly_rows: int
    week_start_min: str
    week_start_max: str
    demand_date_source: str
    checks: dict


def run(group: str = config.COHORT_GROUP, slug: str | None = None) -> BuildSummary:
    if slug is None:
        out_dir, attr_out, weekly_out, manifest_out = (
            config.MATTRESS_DIR, config.SKU_ATTR_OUT, config.WEEKLY_FACTS_OUT, config.MANIFEST_OUT,
        )
    else:
        paths = config.cohort_paths(slug)
        out_dir, attr_out, weekly_out, manifest_out = (
            paths["dir"], paths["sku_attr"], paths["weekly"], paths["manifest"],
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    con = _connect()

    skus = select_cohort_skus(con, group)
    attrs = build_sku_attributes(con)
    weekly = build_weekly_facts(con)
    checks = conservation_checks(con, weekly)

    attrs.to_parquet(attr_out, index=False)
    weekly.to_parquet(weekly_out, index=False)

    summary = BuildSummary(
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cohort_group=group,
        n_mattress_skus=len(skus),
        n_skus_with_demand=int(weekly["sku_id"].nunique()),
        n_stores=int(weekly["store_code"].nunique()),
        n_weekly_rows=int(len(weekly)),
        week_start_min=str(weekly["demand_week_start"].min().date()),
        week_start_max=str(weekly["demand_week_start"].max().date()),
        demand_date_source=config.DEMAND_DATE_SOURCE,
        checks=checks,
    )
    manifest_out.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")
    con.close()
    return summary


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest a category cohort slice (SKU x store x week).")
    parser.add_argument("--group", default=config.COHORT_GROUP, help="GRUPA_PRODUSE cohort value")
    parser.add_argument("--slug", default=None, help="output dir slug under data/ (default: mattress_v1)")
    args = parser.parse_args()
    s = run(group=args.group.upper(), slug=args.slug)
    print("Forecast V4 — mattress slice ingested")
    print(f"  cohort group:        {s.cohort_group}")
    print(f"  mattress SKUs:       {s.n_mattress_skus:,}  (with demand: {s.n_skus_with_demand:,})")
    print(f"  stores:              {s.n_stores}")
    print(f"  weekly fact rows:    {s.n_weekly_rows:,}  ({s.week_start_min} -> {s.week_start_max})")
    print(f"  demand date:         {s.demand_date_source}")
    print("  conservation checks:")
    for k, v in s.checks.items():
        print(f"    {k}: {v}")
    out_dir = config.MATTRESS_DIR if args.slug is None else config.cohort_paths(args.slug)["dir"]
    print(f"  outputs: {out_dir}")
    if not s.checks["all_passed"]:
        raise SystemExit("CONSERVATION CHECKS FAILED — do not proceed to modeling.")


if __name__ == "__main__":
    main()
