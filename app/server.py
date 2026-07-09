"""Forecast V4 — FastAPI backend for the Stockly rug replenishment app (UI v2).

Serves the numbers the engine already produced (`data/rugs_v1/demo/`) as JSON to a
plain HTML/JS frontend (`app/web/`, Tabulator tables). All decision logic is a
verbatim port of the legacy Streamlit `recompute()` — same materiality gate, same
order rule — so nothing here can drift from `fev4/demo_data.py`. Adds two things
the UI needs: a per-suggestion **confidence** and structured (language-neutral)
"why" parts so the client can render the reason in EN or RO.

Run: .venv/bin/uvicorn app.server:app --port 8601
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "rugs_v1" / "demo"
BT = ROOT / "data" / "rugs_v1"
WEB = Path(__file__).resolve().parent / "web"

MIN_MATERIAL_MONTHLY = 0.5   # must match fev4/demo_data.py
SHRINK_K = 6.0               # must match fev4/interpretable_model.py
CONF_Q = {"p50": "p50", "p90": "p90", "p95": "p95"}

# --------------------------------------------------------------------------- #
# load once (small: ~8.4k rows)
# --------------------------------------------------------------------------- #
D0 = pd.read_parquet(DATA / "dashboard.parquet").copy()
D0["name"] = D0["denumire_articol"].fillna(D0["sku_id"])
WK = pd.read_parquet(DATA / "weekly_history.parquet")
MS = pd.read_parquet(DATA / "monthly_stock.parquet")
META = json.loads((DATA / "meta.json").read_text())
AS_OF = pd.Timestamp(META["as_of"])
ALL_STORES = sorted(D0["store_code"].unique())


def _json(obj) -> JSONResponse:
    """NaN/inf-safe JSON."""
    return JSONResponse(content=json.loads(json.dumps(obj, default=str).replace("NaN", "null")
                                           .replace("Infinity", "null").replace("-null", "null")))


def _confidence(df: pd.DataFrame) -> np.ndarray:
    """How much the forecast rests on the SKU's OWN recent sales vs borrowed from
    its design family. High = repeated own sales + real history; Low = a guess."""
    pos13 = df["pos13"].fillna(0).to_numpy()
    roll13 = df["roll13"].fillna(0).to_numpy()
    hist = df["hist_weeks"].fillna(0).to_numpy()
    high = (pos13 >= 0.30) & (hist >= 26)
    low = (roll13 < 0.02) | (hist < 8)
    return np.where(high, "high", np.where(low, "low", "med"))


def recompute(lt: int, ss: int, moq: int, conf: str, stores: list[str]) -> pd.DataFrame:
    """Verbatim port of streamlit_app.recompute() + confidence + why-parts."""
    df = D0[D0["store_code"].isin(stores)].copy()
    for who in ("ours", "theirs"):
        rate = df[f"rate_{who}"].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            cover = np.where(rate > 0, df["stock"].to_numpy() / rate, np.inf)
        df[f"days_cover_{who}"] = np.round(cover, 1)
        seg = np.select([cover < lt, cover < lt + ss, cover < lt + ss + 14, cover <= 90],
                        ["critical", "urgent", "attention", "ok"], "overstock")
        immaterial = (rate * 30.0) < MIN_MATERIAL_MONTHLY
        df[f"segment_{who}"] = np.where(immaterial & np.isin(seg, ["critical", "urgent"]),
                                        "attention", seg)
    q = df[conf].to_numpy()
    order = np.maximum(0, np.ceil(q) - df["stock"].to_numpy())
    order = np.where((order > 0) & (order < moq), moq, order).astype(int)
    df["order_qty"] = order
    seg = df["segment_ours"]
    df["action"] = np.select(
        [(seg.isin(["critical", "urgent"])) & (order > 0),
         df["rocket"] & (order > 0),
         df["klass"] == "dead", df["klass"] == "dying",
         order > 0, (seg == "overstock") & (df["stock"] > 0)],
        ["reorder_now", "reorder_accel", "stop_clear", "stop_reorder",
         "reorder", "overstock_hold"], default="ok")
    df["confidence"] = _confidence(df)
    df["sells_per_mo"] = (df["rate_ours"] * 30).round(2)
    df["expected"] = df["p50"].round(0)
    df["safe"] = df[conf].round(0)
    return df


def _rows(df: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in df.iterrows():
        dc = r["days_cover_ours"]
        trend_dir = "up" if r["trend"] > 1.05 else ("down" if r["trend"] < 0.95 else "flat")
        out.append({
            "sku": r["sku_id"], "name": r["name"], "store": r["store_code"],
            "action": r["action"], "order": int(r["order_qty"]),
            "confidence": r["confidence"], "status": r["segment_ours"],
            "stock": int(r["stock"]), "sells_mo": float(r["sells_per_mo"]),
            "days_cover": None if not np.isfinite(dc) else float(dc),
            "expected": None if pd.isna(r["expected"]) else int(r["expected"]),
            "safe": None if pd.isna(r["safe"]) else int(r["safe"]),
            "idle_mo": int(r["months_since_sale"]) if pd.notna(r["months_since_sale"]) else None,
            "stockout_risk": float(r["stockout_risk_pct"]) if pd.notna(r["stockout_risk_pct"]) else None,
            "rocket": bool(r["rocket"]),
            "why": {  # language-neutral parts; client composes the sentence
                "rate_mo": float(r["sells_per_mo"]),
                "season_pct": int(round((r["season"] - 1) * 100)),
                "trend": trend_dir, "rocket": bool(r["rocket"]),
                "stock": int(r["stock"]), "need": None if pd.isna(r["safe"]) else int(r["safe"]),
                "order": int(r["order_qty"]),
            },
        })
    return out


app = FastAPI(title="Stockly API")


@app.get("/api/meta")
def api_meta():
    return _json({
        "as_of": META["as_of"], "stores": ALL_STORES,
        "lead_time_days": META["lead_time_days"], "safety_days": META["safety_days"],
        "rocket_ratio": META["rocket_ratio"], "overstock_days": META["overstock_days"],
        "phi": META.get("phi"), "shrink_k": META.get("shrink_k"),
        "calibration_params": META["calibration_params"],
        "min_material_monthly": MIN_MATERIAL_MONTHLY, "notes": META["notes"],
    })


def _parse_stores(stores: str | None) -> list[str]:
    if not stores:
        return ALL_STORES
    picked = [s for s in stores.split(",") if s in ALL_STORES]
    return picked or ALL_STORES


@app.get("/api/dashboard")
def api_dashboard(lt: int = Query(30), ss: int = Query(7), moq: int = Query(1),
                  conf: str = Query("p90"), stores: str | None = None):
    conf = CONF_Q.get(conf, "p90")
    df = recompute(lt, ss, moq, conf, _parse_stores(stores))
    counts = df["segment_ours"].value_counts().to_dict()
    reorder = df[df["order_qty"] > 0]
    dead = df[df["klass"].isin(["dead", "dying"])]
    kpis = {
        "reorder_skus": int(len(reorder)),
        "reorder_units": int(reorder["order_qty"].sum()),
        "accelerating": int((df["rocket"] & (df["stock"] > 0)).sum()),
        "dead_cash": float(dead["trapped_value"].fillna(0).sum()),
        "segment_counts": {k: int(counts.get(k, 0)) for k in
                           ["critical", "urgent", "attention", "ok", "overstock"]},
    }
    return _json({"rows": _rows(df.sort_values(["priority", "sells_per_mo"],
                                               ascending=[True, False])), "kpis": kpis})


@app.get("/api/deadstock")
def api_deadstock(stores: str | None = None):
    df = D0[D0["store_code"].isin(_parse_stores(stores))].copy()
    kl = df[(df["klass"] != "active") | (df["segment_ours"] == "overstock")].copy()
    kl = kl[kl["stock"] > 0]
    kl["trapped_value"] = kl["trapped_value"].fillna(0.0)
    kl["idle_mo"] = kl["months_since_sale"].fillna(0).astype(int)
    # weeks_since_sale caps at 999 (~230 mo); our data starts Dec-2021 (~55 mo), so
    # anything beyond the window means "never sold in the data" -> no real last-sale date.
    never = kl["idle_mo"] > 54
    kl["last_sale"] = (AS_OF - pd.to_timedelta(kl["idle_mo"].clip(upper=54) * 30, unit="D")).dt.strftime("%Y-%m")
    kl.loc[never, "last_sale"] = None
    kl.loc[never, "idle_mo"] = -1  # client renders as "never / >4yr"
    kl["unit_value"] = np.where(kl["stock"] > 0, kl["trapped_value"] / kl["stock"], 0.0).round(0)
    root = np.select(
        [kl["klass"] == "dead", kl["klass"] == "dying", kl["season"] < 0.9, kl["trend"] < 0.9],
        ["dead", "dying", "out_of_season", "trend_down"], default="slow")
    kl["root_cause"] = root
    rows = [{
        "sku": r["sku_id"], "name": r["name"], "store": r["store_code"],
        "stock": int(r["stock"]), "last_sale": r["last_sale"], "idle_mo": int(r["idle_mo"]),
        "root_cause": r["root_cause"], "klass": r["klass"],
        "unit_value": float(r["unit_value"]), "trapped": float(r["trapped_value"]),
    } for _, r in kl.iterrows()]
    kpis = {
        "total": float(kl["trapped_value"].sum()),
        "confirmed_dead": float(kl[(kl["klass"] == "dead") & (kl["idle_mo"] >= 12)]["trapped_value"].sum()),
        "dying": float(kl[kl["klass"] == "dying"]["trapped_value"].sum()),
        "positions": int(len(kl)),
    }
    return _json({"rows": rows, "kpis": kpis})


@app.get("/api/sku/{store}/{sku}")
def api_sku(store: str, sku: str, lt: int = 30, ss: int = 7, moq: int = 1, conf: str = "p90"):
    conf = CONF_Q.get(conf, "p90")
    df = recompute(lt, ss, moq, conf, ALL_STORES)
    row = df[(df["sku_id"] == sku) & (df["store_code"] == store)]
    if row.empty:
        return _json({"error": "not found"})
    r = row.iloc[0]
    w = WK[(WK["sku_id"] == sku) & (WK["store_code"] == store)].sort_values("week_start")
    weekly = [{"week": d.strftime("%Y-%m-%d"), "units": float(u)}
              for d, u in zip(w["week_start"], w["gross_units"])]
    wy = w.copy()
    wy["year"] = wy["week_start"].dt.year
    wy["woy"] = wy["week_start"].dt.isocalendar().week.astype(int)
    years = sorted(wy["year"].unique())[-2:]
    dual = {}
    if len(years) == 2:
        piv = wy[wy["year"].isin(years)].pivot_table(index="woy", columns="year",
                                                     values="gross_units", aggfunc="sum").fillna(0)
        dual = {"woy": [int(x) for x in piv.index],
                "prev": {"year": int(years[0]), "units": [float(x) for x in piv[years[0]]]},
                "curr": {"year": int(years[1]), "units": [float(x) for x in piv[years[1]]]}}
    m = MS[(MS["sku_id"] == sku) & (MS["store_code"] == store)].sort_values("month_start")
    stock_series = [{"month": d.strftime("%Y-%m"), "stock": float(s)}
                    for d, s in zip(m["month_start"], m["stock_eom"])]
    return _json({
        "sku": sku, "name": r["name"], "store": store,
        "stock": int(r["stock"]), "order": int(r["order_qty"]), "confidence": r["confidence"],
        "sells_mo": float(r["sells_per_mo"]), "expected": None if pd.isna(r["expected"]) else int(r["expected"]),
        "safe": None if pd.isna(r["safe"]) else int(r["safe"]),
        "stockout_risk": float(r["stockout_risk_pct"]) if pd.notna(r["stockout_risk_pct"]) else None,
        "decomp": {"roll13": float(r["roll13"]), "pooled_rate": float(r["pooled_rate"]),
                   "season": float(r["season"]), "trend": float(r["trend"]),
                   "rate_theirs": float(r["rate_theirs"]), "days_cover_theirs": float(r["days_cover_theirs"])},
        "weekly": weekly, "dual_calendar": dual, "monthly_stock": stock_series,
    })


@app.get("/api/proof")
def api_proof():
    def _load(name):
        p = BT / name
        return json.loads(p.read_text()) if p.exists() else None
    return _json({
        "warning_quality": META["warning_quality"],
        "backtest_2026": _load("backtest_2026_metrics.json"),
        "backtest_value": _load("backtest_2026_value.json"),
    })


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=str(WEB)), name="web")
