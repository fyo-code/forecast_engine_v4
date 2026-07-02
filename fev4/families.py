"""Forecast V4 — Phase 2.1: design-family extraction for rugs.

Rug names are highly regular: ``COVOR <DESIGN> <SIZE> <COLOR>`` (e.g.
"COVOR KAVYA 080x300cm RED", "COVOR DE EXTERIOR MENZY 200x290cm 5001 BROWN",
"COVOR CRYSTAL L.170 l.120 Beige"). The design name repeats across size/color
variants — that repetition is the pooling unit for seasonal indices and
sparse-SKU fallback rates.

Output: data/rugs_v1/sku_families.parquet
    (sku_id, family, is_outdoor, family_size, parse_status)

Run: python -m fev4.families
"""

from __future__ import annotations

import re

import numpy as np

import pandas as pd

from . import config

PATHS = config.cohort_paths(config.RUGS_SLUG)
OUT = PATHS["dir"] / "sku_families.parquet"

# A token that starts the size/variant tail: 080x150, 160x230 cm, L.170, l.120,
# 160cm, ROTUND, or a pure number (variant codes like "6015", "01").
_SIZE_TOKEN = re.compile(
    r"^(\d{2,3}\s*[xX]\s*\d{2,3}|[Ll]\.?\d+|\d+\s*[Cc][Mm]|ROTUND\w*|OVAL\w*|\d+)$"
)
_LEAD = re.compile(r"^COVOR(?:AS)?\b", re.IGNORECASE)
_OUTDOOR = re.compile(r"\bDE\s+EXTERIOR\b|\bEXTERIOR\b|\bOUTDOOR\b", re.IGNORECASE)


def parse_family(name: object) -> tuple[str | None, bool, str]:
    """Return (family, is_outdoor, parse_status) for one product name."""
    if not isinstance(name, str) or not name.strip():
        return None, False, "no_name"
    s = name.strip().upper()
    if not _LEAD.match(s):
        return None, False, "not_covor_name"
    s = _LEAD.sub("", s, count=1).strip()
    outdoor = bool(_OUTDOOR.search(s))
    s = _OUTDOOR.sub(" ", s).strip()
    tokens = [t for t in re.split(r"[\s,/]+", s) if t]
    design: list[str] = []
    for tok in tokens:
        # design tokens may carry an attached variant number (LAGE32, PACIFICO31): keep them
        if _SIZE_TOKEN.match(tok):
            break
        design.append(tok)
        if len(design) >= 3:  # design names are 1-3 tokens; stop before colors
            break
    if not design:
        return None, outdoor, "no_design_token"
    return " ".join(design), outdoor, "ok"


_TRAIL_SIZE = re.compile(r"\d+R?$")


def code_prefix(sku: object) -> str | None:
    """Fallback family: SKU code minus trailing size digits (DKTABOOANTH120 -> DKTABOOANTH).

    Pools size variants within a design+color; coarser than the name family (which also
    pools colors) but covers SKUs with no product name.
    """
    s = str(sku).strip().upper()
    pref = _TRAIL_SIZE.sub("", s)
    return pref if len(pref) >= 4 and pref != s else None


def build() -> pd.DataFrame:
    attrs = pd.read_parquet(PATHS["sku_attr"])
    parsed = attrs["denumire_articol"].map(parse_family)
    out = pd.DataFrame(
        {
            "sku_id": attrs["sku"],
            "name_family": [p[0] for p in parsed],
            "is_outdoor": [p[1] for p in parsed],
            "parse_status": [p[2] for p in parsed],
        }
    )
    out["code_family"] = out["sku_id"].map(code_prefix)

    # keep each level only where it actually pools (>=2 SKUs)
    for col in ("name_family", "code_family"):
        sizes = out.groupby(col)["sku_id"].transform("count")
        out.loc[out[col].notna() & (sizes < 2), col] = None

    # hierarchy: name family (colors+sizes) > code prefix (sizes) > none
    out["family"] = out["name_family"].fillna(out["code_family"])
    out["family_level"] = np.select(
        [out["name_family"].notna(), out["code_family"].notna()],
        ["name", "code_prefix"], default="none",
    )
    out["family_size"] = out.groupby("family")["sku_id"].transform("count").where(
        out["family"].notna(), 0
    ).fillna(0).astype(int)
    return out


def main() -> None:
    out = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)
    ok = out["family"].notna()
    fam = out.loc[ok].groupby("family")["sku_id"].count().sort_values(ascending=False)
    print("Forecast V4 — Phase 2.1: rug design families")
    print(f"  SKUs: {len(out):,} | with family (size>=2): {ok.sum():,} ({ok.mean():.0%})")
    print(f"  parse status: {out['parse_status'].value_counts().to_dict()}")
    print(f"  families: {fam.size:,} | median SKUs/family: {fam.median():.0f} | top: {dict(fam.head(5))}")
    print(f"  outdoor SKUs: {int(out['is_outdoor'].sum()):,}")
    print(f"  wrote {OUT.name}")


if __name__ == "__main__":
    main()
