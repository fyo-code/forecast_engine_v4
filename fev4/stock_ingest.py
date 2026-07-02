"""Forecast V4 — Phase 0: monthly store-warehouse stock ingestion.

Reads the wide per-store stock snapshot CSVs (columns like "IANUARIE 2022/STOC")
and produces a long SKU x store x month table:

    data/rugs_v1/store_stock_monthly.parquet
    (sku_id, store_code, month_start, stock_qty)

Snapshot timing within the month is unknown (start vs end of month); the
receipts-feasibility test resolves that empirically.

Run: python -m fev4.stock_ingest
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pandas as pd

from . import config

OUT = config.cohort_paths(config.RUGS_SLUG)["dir"] / "store_stock_monthly.parquet"
MANIFEST = config.cohort_paths(config.RUGS_SLUG)["dir"] / "stock_ingest_manifest.json"

_COL_RE = re.compile(r"^([A-ZĂÂÎȘŢ]+) (\d{4})/STOC$")


def _parse_month_col(col: str) -> pd.Timestamp | None:
    m = _COL_RE.match(col.strip())
    if not m:
        return None
    month = config.RO_MONTHS.get(m.group(1))
    if month is None:
        return None
    return pd.Timestamp(year=int(m.group(2)), month=month, day=1)


def load_store_stock() -> pd.DataFrame:
    frames = []
    for store, path in config.STORE_STOCK_FILES.items():
        wide = pd.read_csv(path, dtype=str)
        sku_col = "ARTICOL COD"
        month_cols = {c: _parse_month_col(c) for c in wide.columns}
        month_cols = {c: ts for c, ts in month_cols.items() if ts is not None}
        long = wide.melt(id_vars=[sku_col], value_vars=list(month_cols),
                         var_name="col", value_name="stock_qty")
        long["month_start"] = long["col"].map(month_cols)
        long["stock_qty"] = pd.to_numeric(long["stock_qty"], errors="coerce").fillna(0.0)
        long["store_code"] = store
        long = long.rename(columns={sku_col: "sku_id"})[
            ["sku_id", "store_code", "month_start", "stock_qty"]
        ]
        frames.append(long)
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values(["store_code", "sku_id", "month_start"]).reset_index(drop=True)


def run() -> dict:
    df = load_store_stock()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)

    per_store = df.groupby("store_code").agg(
        skus=("sku_id", "nunique"), months=("month_start", "nunique"),
        ever_stocked=("stock_qty", lambda s: int((s > 0).sum() > 0)),
    )
    varying = (
        df.groupby(["store_code", "sku_id"])["stock_qty"]
        .agg(["max", "std"]).query("max > 0")
    )
    summary = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "stores": df["store_code"].nunique(),
        "skus": int(df["sku_id"].nunique()),
        "month_range": [str(df["month_start"].min().date()), str(df["month_start"].max().date())],
        "per_store_skus": per_store["skus"].to_dict(),
        "ever_stocked_sku_stores": int(len(varying)),
        "share_varying_of_ever_stocked": round(float((varying["std"] > 0).mean()), 3),
    }
    MANIFEST.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    s = run()
    print("Forecast V4 — Phase 0: store stock ingested")
    for k, v in s.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
