"""AurumQ-RL built-in factor library integration (alpha101 / gtja191).

The fusion panel is pandas-native; aurumq-rl factors are polars-native.
This module converts a daily OHLCV panel to the polars contract used by
``aurumq_rl.factors``, computes the requested registry, and returns pandas
columns ready to be joined into the fusion panel.

NOTE: full-market 296-factor computation is expensive. Use ``--codes`` to
limit a smoke run; production backfill should shard by date/code and cache.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.paths import (
    configured_upstream_path,
    require_upstream_root,
    upstream_path,
    validate_startup_or_exit,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = configured_upstream_path("v6", "artifacts", "panel", "panel.parquet")
DEFAULT_OUT = PROJECT_ROOT / "data" / "aurumq_factors_sample.parquet"


def _polars_frame(frame: pd.DataFrame) -> "pl.DataFrame":
    import polars as pl

    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="raise")
    work["code"] = work["code"].astype(str).str.zfill(6)
    work = work.sort_values(["code", "date"]).reset_index(drop=True)
    work["vol"] = work["volume"].astype(float) * 100.0
    work["vwap"] = work["amount"].astype(float) / work["vol"].replace(0, float("nan"))
    work["returns"] = work.groupby("code", sort=False)["close"].pct_change()
    if "market_cap" in work.columns:
        work["cap"] = work["market_cap"].astype(float)
    for window in (5, 10, 15, 20, 30, 40, 50, 60, 81, 120, 150, 180):
        work[f"adv{window}"] = (
            work.groupby("code", sort=False)["volume"]
            .transform(lambda series: series.rolling(window, min_periods=1).mean())
        )
    if "industry" in work.columns:
        work["sub_industry"] = work["industry"].astype(str)
    else:
        work["industry"] = "UNKNOWN"
        work["sub_industry"] = "UNKNOWN"
    keep = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
        "vwap",
        "returns",
        "industry",
        "sub_industry",
        *(["cap"] if "cap" in work.columns else []),
        *[f"adv{window}" for window in (5, 10, 15, 20, 30, 40, 50, 60, 81, 120, 150, 180)],
    ]
    work = work[keep].rename(
        columns={"date": "trade_date", "code": "stock_code", "vol": "volume"}
    )
    return pl.from_pandas(work)


def _series_dict_to_pandas(base: "pl.DataFrame", series: dict[str, "pl.Series"]) -> pd.DataFrame:
    import polars as pl

    keys = base.select(["stock_code", "trade_date"])
    out = pl.DataFrame(series)
    if keys.height != out.height:
        raise ValueError(
            "AurumQ factor row count mismatch: "
            f"keys={keys.height}, factors={out.height}; refusing positional horizontal concat"
        )
    result = pl.concat([keys, out], how="horizontal_extend")
    return result.to_pandas()


def compute_factors(
    frame: pd.DataFrame,
    families: tuple[str, ...] = ("alpha101",),
    *,
    shares_path: Path | str | None = None,
) -> pd.DataFrame:
    aurumq_root = require_upstream_root("aurumq")
    if str(aurumq_root / "src") not in sys.path:
        sys.path.insert(0, str(aurumq_root / "src"))

    if shares_path is not None:
        from bridge.refdata import shares_to_market_cap

        shares = pd.read_parquet(shares_path)
        frame = shares_to_market_cap(frame, shares)

    plf = _polars_frame(frame)
    factor_series: dict[str, "pl.Series"] = {}

    skipped: dict[str, str] = {}
    if "alpha101" in families:
        import aurumq_rl.factors.alpha101  # noqa: F401
        from aurumq_rl.factors.registry import ALPHA101_REGISTRY

        for name, entry in ALPHA101_REGISTRY.items():
            try:
                factor_series[name] = entry.impl(plf).rename(name)
            except Exception as exc:  # missing cap/industry columns etc.
                skipped[name] = f"{type(exc).__name__}: {exc}"

    if "gtja191" in families:
        import aurumq_rl.factors.gtja191  # noqa: F401
        from aurumq_rl.factors.registry import GTJA191_REGISTRY

        for name, entry in GTJA191_REGISTRY.items():
            try:
                factor_series[name] = entry.impl(plf).rename(name)
            except Exception as exc:
                skipped[name] = f"{type(exc).__name__}: {exc}"

    if skipped:
        print(
            f"[aurumq_factors] skipped {len(skipped)} factors due to missing columns: "
            f"{list(skipped.keys())[:10]}",
            file=sys.stderr,
        )

    if not factor_series:
        raise ValueError("No factor families requested")
    result = _series_dict_to_pandas(plf, factor_series)
    result = result.rename(columns={"stock_code": "code", "trade_date": "date"})
    result["date"] = pd.to_datetime(result["date"])
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute aurumq-rl built-in factors")
    parser.add_argument("--panel", default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--families", default="alpha101", help="comma separated: alpha101,gtja191")
    parser.add_argument("--codes", default=None, help="comma separated codes for a smoke run")
    parser.add_argument("--shares", default=None, help="baostock shares PIT parquet")
    args = parser.parse_args()

    validate_startup_or_exit(("v6", "aurumq"))
    panel_path = args.panel or upstream_path("v6", "artifacts", "panel", "panel.parquet")
    panel = pd.read_parquet(panel_path)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    if args.codes:
        wanted = {code.strip().zfill(6) for code in args.codes.split(",") if code.strip()}
        panel = panel[panel["code"].isin(wanted)]

    families = tuple(family.strip() for family in args.families.split(",") if family.strip())
    result = compute_factors(panel, families, shares_path=args.shares)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out, index=False)
    print(
        json.dumps(
            {
                "rows": int(len(result)),
                "factor_columns": int(result.shape[1] - 2),
                "path": str(out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
