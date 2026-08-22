"""P0 data base: local A-share kline normalization.

Data source: the workspace sibling ``a-share-kline-data/`` (forward-adjusted
CSVs). Volume in those CSVs is stored in **lots (手)**; amount is in yuan.
This module converts volume to shares and builds an aurumq-rl-compatible
panel. Market cap is ONLY accepted from an explicit total-shares source;
``close * volume`` is rejected as a market-cap proxy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from bridge.paths import configured_upstream_path, upstream_path

DEFAULT_KLINE_DIR = configured_upstream_path("kline")

LOTS_PER_SHARE = 100.0
VWAP_RATIO_TOLERANCE = (0.2, 5.0)  # catches the 100x hand/share mistake


class DataContractError(ValueError):
    """Raised when a data contract invariant is violated."""


@dataclass(frozen=True)
class VolumeSource:
    """Declared unit for a volume source."""

    lots: Callable[[pd.DataFrame], pd.Series] = lambda df: df["volume"] * LOTS_PER_SHARE
    shares: Callable[[pd.DataFrame], pd.Series] = lambda df: df["volume"]


def code_to_ts_code(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code.startswith(("60", "68")):
        return f"{code}.SH"
    if code.startswith(("00", "30")):
        return f"{code}.SZ"
    if code.startswith(("8", "4", "92")):
        return f"{code}.BJ"
    return f"{code}.UNKNOWN"


def read_stock_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")
    required = {"date", "open", "high", "low", "close", "volume", "amount", "code", "name"}
    missing = required - set(df.columns)
    if missing:
        raise DataContractError(f"{path.name} missing columns: {sorted(missing)}")
    df["date"] = pd.to_datetime(df["date"], errors="raise")
    df["code"] = df["code"].str.zfill(6)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def load_kline_panel(
    kline_dir: Path | str | None = None,
    *,
    codes: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 0,
) -> pd.DataFrame:
    kline_dir = Path(kline_dir) if kline_dir is not None else upstream_path("kline")
    wanted = {str(code).zfill(6) for code in codes} if codes else None
    files = sorted(kline_dir.glob("*.csv"))
    frames: list[pd.DataFrame] = []
    for path in files:
        if path.name.startswith("_") or path.stem in {"_failed_stocks", "_stock_list"}:
            continue
        code = path.stem.zfill(6)
        if wanted is not None and code not in wanted:
            continue
        frame = read_stock_csv(path)
        if start is not None:
            frame = frame.loc[frame["date"] >= pd.Timestamp(start)]
        if end is not None:
            frame = frame.loc[frame["date"] <= pd.Timestamp(end)]
        frames.append(frame)
        if limit and len(frames) >= limit:
            break
    if not frames:
        raise DataContractError(f"No kline CSVs found under {kline_dir}")
    result = pd.concat(frames, ignore_index=True)
    return normalize_panel(result, volume_unit="lots")


def normalize_panel(df: pd.DataFrame, volume_unit: str = "lots") -> pd.DataFrame:
    """Normalize a price/volume panel into the aurumq contract.

    Returns columns: ts_code, trade_date, open, high, low, close, pct_chg,
    vol (shares), amount, vwap, adj_factor, is_st, days_since_ipo.
    ``market_cap`` is intentionally NOT derived here.
    """
    frame = df.copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame = frame.sort_values(["code", "date"]).reset_index(drop=True)

    if volume_unit == "lots":
        frame["vol"] = frame["volume"].astype(float) * LOTS_PER_SHARE
    elif volume_unit == "shares":
        frame["vol"] = frame["volume"].astype(float)
    else:
        raise DataContractError(f"Unknown volume_unit: {volume_unit!r}")

    frame["vwap"] = frame["amount"].astype(float) / frame["vol"].replace(0, np.nan)
    _validate_vwap_unit(frame)

    frame["ts_code"] = frame["code"].map(code_to_ts_code)
    frame["trade_date"] = frame["date"].dt.normalize()
    frame["prev_close"] = frame.groupby("code", sort=False)["close"].shift(1)
    frame["pct_chg"] = frame["close"] / frame["prev_close"] - 1.0
    frame["is_st"] = frame["name"].astype(str).str.contains(r"ST|退", regex=True)
    first_dates = frame.groupby("code")["trade_date"].transform("min")
    frame["days_since_ipo"] = (frame["trade_date"] - first_dates).dt.days
    frame["adj_factor"] = 1.0  # prices are already forward-adjusted

    columns = [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pct_chg",
        "vol",
        "amount",
        "vwap",
        "adj_factor",
        "is_st",
        "days_since_ipo",
        "code",
        "name",
    ]
    return frame[columns].reset_index(drop=True)


def _validate_vwap_unit(frame: pd.DataFrame) -> None:
    valid = frame["vol"].notna() & (frame["vol"] > 0) & frame["amount"].notna()
    ratio = frame.loc[valid, "vwap"] / frame.loc[valid, "close"]
    bad = ratio[(ratio < VWAP_RATIO_TOLERANCE[0]) | (ratio > VWAP_RATIO_TOLERANCE[1])]
    if len(bad):
        sample = bad.head(3).round(3).to_dict()
        raise DataContractError(
            f"vwap/close ratio outside {VWAP_RATIO_TOLERANCE} for {len(bad)} rows "
            f"(first values: {sample}); volume is probably not converted from lots to shares"
        )


def attach_total_shares(
    frame: pd.DataFrame,
    shares: pd.DataFrame,
    *,
    shares_column: str = "total_shares",
) -> pd.DataFrame:
    """Attach market_cap = close * total_shares using a PIT shares table.

    ``shares`` must contain at least ``code, effective_date, total_shares``.
    The join is as-of by date so history is not backfilled with the latest
    share count.
    """
    if not {"code", "effective_date", shares_column}.issubset(shares.columns):
        raise DataContractError(
            f"shares table must contain code/effective_date/{shares_column}"
        )
    shares = shares.copy()
    shares["code"] = shares["code"].astype(str).str.zfill(6)
    shares["effective_date"] = pd.to_datetime(shares["effective_date"], errors="raise").astype(
        "datetime64[us]"
    )
    shares = shares.sort_values(["code", "effective_date"]).reset_index(drop=True)
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").astype(
        "datetime64[us]"
    )
    frame = frame.sort_values(["code", "trade_date"])
    merged_parts: list[pd.DataFrame] = []
    for code, group in frame.groupby("code", sort=False):
        right = (
            shares.loc[shares["code"].eq(code)]
            .sort_values("effective_date")
            .drop(columns=["code"])
        )
        if right.empty:
            group[shares_column] = np.nan
            merged_parts.append(group)
            continue
        part = pd.merge_asof(
            group,
            right,
            left_on="trade_date",
            right_on="effective_date",
            direction="backward",
        )
        merged_parts.append(part)
    merged = pd.concat(merged_parts, ignore_index=True)
    merged["market_cap"] = merged["close"].astype(float) * merged[shares_column].astype(float)
    return merged


def require_market_cap(frame: pd.DataFrame) -> pd.DataFrame:
    if "market_cap" not in frame.columns:
        raise DataContractError(
            "market_cap column is missing; refuse to use close*volume as a proxy. "
            "Call attach_total_shares() with a PIT total-shares table first."
        )
    return frame
