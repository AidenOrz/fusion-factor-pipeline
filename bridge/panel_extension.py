# -*- coding: utf-8 -*-
"""Build a v6-schema daily panel extension from the local K-line CSVs.

Purpose: the frozen fusion panel is materialized from
`v6/artifacts/panel/panel.parquet`, which upstream governance pins to the
2023-06-01..2025-12-31 development split (build-panel refuses anything else).
The local K-line CSVs reach further, and every column the fusion pipeline
consumes reproduces exactly from them, so the forward window can be
materialized here without touching upstream artifacts.

Emitted columns are limited to what `bridge/panel.py` actually reads:
identity/OHLCV fields, `industry`, `bar_number`, and the eight v6-derived
trend/volatility columns listed in `full_market_keep`.

Usage (then feed the output to `bridge/panel.py --panel <out>`):

    python bridge/panel_extension.py \
      --kline-dir "$env:A_SHARE_KLINE_DATA_ROOT" \
      --sector-map "<honest_backtest>/data/stock_sector_map.csv" \
      --benchmark "<kline>/_bench_000985.csv" \
      --start 2023-06-01 --end 2026-07-24 \
      --out artifacts/panel_ext.parquet

Verification contract: overlap the rebuilt window with the frozen panel and
require max abs diff = 0 on every feature column. The only legitimate
difference is a forward-label NA pattern at the tail (rebuilt panels can fill
labels the frozen one could not), so accept `frozen_only = 0` with
value-equal overlap. See docs/knowledge/local_kline_panel_rebuild.md.

Note: `all_main_board_kline.csv` is an aggregate file and is skipped; it holds
one row that violates the vwap contract in `bridge/data.py`.
"""
from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

DERIVED = [
    "atr_relative",
    "range_relative",
    "vol_ratio",
    "position_ema20",
    "ret_5d",
    "ret_20d",
    "realized_vol_120d_d1",
    "amount_dry_ratio",
]

# Columns consumed downstream; kept explicit so a schema drift is visible.
EMIT = [
    "date", "code", "name", "open", "high", "low", "close", "volume", "amount",
    "industry", "bar_number", *DERIVED,
]


def compute_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the v6 derived columns used by the fusion panel.

    Formulas mirror `v6/src/.../data/panel.py::compute_stock_indicators`.
    """
    r = frame.sort_values("date").reset_index(drop=True).copy()
    r["prev_close"] = r["close"].shift(1)
    r["daily_return"] = r["close"].pct_change()
    for period in (15, 20, 30, 60):
        r[f"hhv{period}"] = r["high"].rolling(period).max().shift(1)
        r[f"llv{period}"] = r["low"].rolling(period).min().shift(1)
    r["range_15"] = (r["hhv15"] - r["llv15"]) / r["llv15"]
    r["range_30"] = (r["hhv30"] - r["llv30"]) / r["llv30"]
    r["ema_20"] = r["close"].ewm(span=20, adjust=False).mean()
    tr = pd.concat(
        [
            r["high"] - r["low"],
            (r["high"] - r["close"].shift(1)).abs(),
            (r["low"] - r["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    r["atr10_d1"] = tr.rolling(10).mean().shift(1)
    r["atr50_d1"] = tr.rolling(50).mean().shift(1)
    r["volume_median20_d1"] = r["volume"].rolling(20).median().shift(1)
    r["amount_median10_d1"] = r["amount"].rolling(10).median().shift(1)
    r["amount_median60_d1"] = r["amount"].rolling(60).median().shift(1)
    r["atr_relative"] = r["atr10_d1"] / r["atr50_d1"]
    r["range_relative"] = r["range_15"] / r["range_30"]
    r["vol_ratio"] = r["volume"] / r["volume_median20_d1"]
    r["amount_dry_ratio"] = r["amount_median10_d1"] / r["amount_median60_d1"]
    r["position_ema20"] = r["prev_close"] / r["ema_20"].shift(1) - 1
    r["ret_5d"] = r["close"] / r["close"].shift(5) - 1
    r["ret_20d"] = r["close"] / r["close"].shift(20) - 1
    r["realized_vol_120d_d1"] = (
        r["daily_return"].rolling(120).std(ddof=1).shift(1) * np.sqrt(250)
    )
    r["bar_number"] = np.arange(len(r), dtype=np.int32)
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kline-dir", default=os.environ.get("A_SHARE_KLINE_DATA_ROOT"))
    ap.add_argument("--sector-map", default=None)
    ap.add_argument("--start", default="2026-07-18")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--benchmark", default=None, help="benchmark csv for benchmark_open/close")
    args = ap.parse_args()

    if not args.kline_dir:
        raise SystemExit("missing --kline-dir or A_SHARE_KLINE_DATA_ROOT")
    kdir = Path(args.kline_dir)
    # Exclude aggregate/auxiliary files that are not per-stock series.
    files = [
        p for p in sorted(glob.glob(str(kdir / "*.csv")))
        if not os.path.basename(p).startswith("_")
        and os.path.basename(p) != "all_main_board_kline.csv"
    ]
    if args.limit:
        files = files[: args.limit]
    print(f"per-stock CSVs: {len(files)}")

    industry = {}
    if args.sector_map and Path(args.sector_map).is_file():
        sm = pd.read_csv(args.sector_map)
        sm.columns = [str(c) for c in sm.columns]
        code_col = sm.columns[0]
        ind_col = [c for c in sm.columns if "industry" in c.lower()]
        if ind_col:
            for code, ind in zip(sm[code_col].astype(str).str.zfill(6), sm[ind_col[0]]):
                industry[code] = ind

    rows: list[pd.DataFrame] = []
    failures: list[str] = []
    for i, path in enumerate(files):
        code = Path(path).stem.zfill(6)
        try:
            df = pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            if df.empty:
                continue
            df["code"] = code
            for col in ("open", "high", "low", "close", "volume", "amount"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            computed = compute_indicators(df)
            window = computed[
                (computed["date"] >= pd.Timestamp(args.start))
                & (computed["date"] <= pd.Timestamp(args.end))
            ]
            if window.empty:
                continue
            window = window.copy()
            window["industry"] = industry.get(code, "UNKNOWN")
            rows.append(window[EMIT])
        except Exception as exc:  # keep the build honest about what failed
            failures.append(f"{code}: {exc}")
        if (i + 1) % 500 == 0:
            print(f"  {i + 1}/{len(files)} done")

    if not rows:
        raise SystemExit("no rows materialized in the requested window")
    out = pd.concat(rows, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    out["code"] = out["code"].astype(str).str.zfill(6)

    if args.benchmark and Path(args.benchmark).is_file():
        b = pd.read_csv(args.benchmark)
        b["date"] = pd.to_datetime(b["date"], errors="coerce")
        b = b.rename(columns={"open": "benchmark_open", "close": "benchmark_close"})
        out = out.merge(b[["date", "benchmark_open", "benchmark_close"]], on="date", how="left")
    else:
        out["benchmark_open"] = np.nan
        out["benchmark_close"] = np.nan

    out = out.sort_values(["date", "code"]).reset_index(drop=True)
    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst, index=False)
    print(f"wrote {dst}: rows={len(out)} dates={out['date'].nunique()} codes={out['code'].nunique()}")
    print(f"window: {out['date'].min().date()} -> {out['date'].max().date()}")
    print(f"failures: {len(failures)}")
    for f in failures[:10]:
        print("  ", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
