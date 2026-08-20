"""Research universes for the full-market ranking pipeline.

The masks in this module define row eligibility, not event signals.  They are
computed from information available on the row date and are intentionally
kept separate from the v6 event panel.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_C1000 = PROJECT_ROOT / "data" / "_c1000.csv"
DEFAULT_C2000 = PROJECT_ROOT / "data" / "_c2000.csv"
DEFAULT_MCAP = PROJECT_ROOT / "data" / "s1_mc_panel.parquet"
DEFAULT_ADV_THRESHOLD = 2_000_000.0


def _codes_from_component_file(path: Path | str | None) -> set[str]:
    if path is None or not Path(path).is_file():
        return set()
    frame = pd.read_csv(path)
    candidates = [column for column in frame.columns if "代码" in str(column) or "code" in str(column).lower()]
    if not candidates:
        raise ValueError(f"component file has no code column: {path}")
    return set(frame[candidates[0]].astype(str).str.extract(r"(\d{6})", expand=False).dropna())


def _load_mcap(
    mcap_path: Path | str | None,
) -> pd.DataFrame:
    if mcap_path is None or not Path(mcap_path).is_file():
        return pd.DataFrame(columns=["date", "code", "float_mcap", "isST"])
    mcap = pd.read_parquet(mcap_path, columns=["date", "code", "float_mcap", "isST"])
    mcap["date"] = pd.to_datetime(mcap["date"], errors="raise")
    mcap["code"] = mcap["code"].astype(str).str.zfill(6)
    return mcap.drop_duplicates(["date", "code"], keep="last")


def _attach_float_mcap(
    frame: pd.DataFrame,
    mcap: pd.DataFrame,
) -> pd.DataFrame:
    if mcap.empty:
        return frame
    left = frame.rename(columns={"float_mcap": "panel_float_mcap"})
    merged = left.merge(
        mcap.rename(columns={"isST": "mcap_is_st"}),
        on=["date", "code"],
        how="left",
        validate="one_to_one",
    )
    return merged


def _as_st_flag(values: pd.Series) -> pd.Series:
    """Normalize bool, numeric, and text ST encodings without treating '0' as true."""
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False).astype(bool)
    normalized = values.astype("string").str.strip().str.lower()
    return normalized.isin({"1", "1.0", "true", "t", "yes", "y", "st"})


def _float_mcap_membership(mcap: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build daily PIT B2/B3 membership from all available market-cap rows.

    The rank boundaries reproduce the published broad size buckets: B2 is
    ranks 801..3800 and B3 is the lower 2,000-stock segment 1801..3800.
    Membership is calculated against the complete mcap source before it is
    joined to the v6 main-board panel, so ranking is not distorted by the
    panel's board coverage.
    """
    valid = mcap.loc[mcap["float_mcap"].gt(0)].copy()
    if valid.empty:
        empty = pd.DataFrame(columns=["date", "code", "pit_b2", "pit_b3"])
        return empty, empty
    valid["mcap_rank"] = valid.groupby("date", sort=False)["float_mcap"].rank(
        method="first", ascending=False
    )
    membership = valid.loc[
        valid["mcap_rank"].between(801, 3800), ["date", "code", "mcap_rank"]
    ].copy()
    membership["pit_b2"] = True
    membership["pit_b3"] = membership["mcap_rank"].ge(1801)
    return membership[["date", "code", "pit_b2", "pit_b3"]], valid


def attach_universe_masks(
    frame: pd.DataFrame,
    *,
    mcap_path: Path | str | None = DEFAULT_MCAP,
    c1000_path: Path | str | None = DEFAULT_C1000,
    c2000_path: Path | str | None = DEFAULT_C2000,
    adv_threshold: float = DEFAULT_ADV_THRESHOLD,
    domain_mode: str = "pit_float_mcap",
) -> pd.DataFrame:
    """Attach B1/B2/B3 full-market eligibility masks.

    B1 is the full panel after row-date tradeability filters.  The default
    B2/B3 path uses daily market-cap ranks calculated from the full mcap
    source.  ``component_snapshot`` is retained only as an explicit legacy
    comparison mode because it cannot be historical PIT membership.
    """
    required = {"date", "code", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"universe frame missing columns: {sorted(missing)}")
    if domain_mode not in {"pit_float_mcap", "component_snapshot"}:
        raise ValueError("domain_mode must be 'pit_float_mcap' or 'component_snapshot'")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    result["code"] = result["code"].astype(str).str.zfill(6)
    mcap = _load_mcap(mcap_path)
    result = _attach_float_mcap(result, mcap)
    if domain_mode == "pit_float_mcap":
        membership, _ = _float_mcap_membership(mcap)
        result = result.merge(membership, on=["date", "code"], how="left", validate="one_to_one")
    if "amount" in result.columns:
        result = result.sort_values(["code", "date"]).reset_index(drop=True)
        amount = pd.to_numeric(result["amount"], errors="coerce")
        result["_amount"] = amount
        result["adv20"] = result.groupby("code", sort=False)["_amount"].transform(
            lambda values: values.rolling(20, min_periods=20).mean()
        )
        result = result.drop(columns="_amount")
    else:
        result["adv20"] = np.nan
    if "mcap_is_st" in result.columns:
        non_st = ~_as_st_flag(result["mcap_is_st"])
        if "is_st" in result.columns:
            non_st &= ~_as_st_flag(result["is_st"])
    elif "is_st" in result.columns:
        non_st = ~_as_st_flag(result["is_st"])
    else:
        non_st = pd.Series(True, index=result.index)
    if "bar_number" in result.columns:
        listed_long_enough = pd.to_numeric(result["bar_number"], errors="coerce").ge(250)
    else:
        listed_long_enough = pd.to_numeric(
            result.get("days_since_ipo", pd.Series(np.inf, index=result.index)), errors="coerce"
        ).ge(365)
    traded = result["close"].notna()
    if "volume" in result.columns:
        traded &= pd.to_numeric(result["volume"], errors="coerce").fillna(0).gt(0)
    liquid = result["adv20"].ge(float(adv_threshold))
    eligible = non_st & listed_long_enough & traded & liquid
    result["universe_b1"] = eligible
    if domain_mode == "pit_float_mcap":
        result["universe_b2"] = eligible & result["pit_b2"].fillna(False)
        result["universe_b3"] = eligible & result["pit_b3"].fillna(False)
        result = result.drop(columns=["pit_b2", "pit_b3"], errors="ignore")
        result["universe_version"] = "pit_float_mcap_daily_rank"
    else:
        c1000 = _codes_from_component_file(c1000_path)
        c2000 = _codes_from_component_file(c2000_path)
        result["universe_b2"] = eligible & result["code"].isin(c1000 | c2000)
        result["universe_b3"] = eligible & result["code"].isin(c2000)
        result["universe_version"] = "component_snapshot_2026_08_18"
    result["universe_adv_threshold"] = float(adv_threshold)
    return result


def universe_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Return daily row and stock counts for attached universe masks."""
    masks = [column for column in ("universe_b1", "universe_b2", "universe_b3") if column in frame]
    rows: list[dict[str, object]] = []
    for date, day in frame.groupby("date", sort=True):
        for mask in masks:
            selected = day.loc[day[mask].fillna(False)]
            rows.append(
                {
                    "date": date,
                    "universe": mask.removeprefix("universe_"),
                    "rows": int(len(selected)),
                    "stocks": int(selected["code"].nunique()),
                }
            )
    return pd.DataFrame(rows)
