"""Reference-data integration (PIT total shares + industry snapshot).

Verified data sources (2026-08, no proxy needed):
- baostock ``query_profit_data`` -> totalShare / liqaShare / pubDate / statDate.
- baostock ``query_stock_basic`` -> ipoDate / outDate / status.
- baostock ``query_stock_industry`` -> current industry + updateDate.
- WeStock ``finance --type balance`` -> PaidInCapital by report period with
  InfoPublDate (fallback cross-check); ``profile`` -> industry + regCapital.

Historical industry classification (true industry PIT) is NOT available from
any of the three free sources tested; only a current snapshot with
``updateDate`` is available. Code that needs industry PIT must therefore use
the snapshot as a lower bound of reliability and never claim full PIT.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "refdata"
DEFAULT_QUARTERS = (1, 2, 3, 4)


def _baostock():
    import baostock as bs

    return bs


def code_to_baostock(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code.startswith(("60", "68", "5")):
        return f"sh.{code}"
    if code.startswith(("00", "30", "1", "2")):
        return f"sz.{code}"
    if code.startswith(("8", "4", "92")):
        return f"bj.{code}"
    raise ValueError(f"unsupported code: {code}")


def _load_existing_shares(resume_path: Path | None) -> tuple[pd.DataFrame, set[tuple[str, str]]]:
    if resume_path is None or not resume_path.is_file():
        return pd.DataFrame(), set()
    existing = pd.read_parquet(resume_path)
    existing["code"] = existing["code"].astype(str).str.zfill(6)
    existing["stat_date"] = existing["stat_date"].astype(str).replace("nan", None)
    existing["pub_date"] = existing["pub_date"].astype(str).replace("nan", None)
    keys = {(row.code, row.stat_date) for row in existing.itertuples(index=False)}
    return existing, keys


def _save_shares(path: Path, existing: pd.DataFrame, new_rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.concat(
        [existing, pd.DataFrame(new_rows)],
        ignore_index=True,
    )
    frame = frame.drop_duplicates(["code", "stat_date"], keep="last").sort_values(
        ["code", "stat_date"]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return frame


def query_shares_quarters(
    codes: list[str],
    years: list[int],
    quarters: tuple[int, ...] = DEFAULT_QUARTERS,
    *,
    pause: float = 0.05,
    resume_path: Path | None = None,
) -> pd.DataFrame:
    """Query baostock profit tables and return a PIT total-shares table.

    Output columns: code, stat_date, pub_date, total_share, float_share.
    With ``resume_path`` the table is appended incrementally and already
    fetched ``(code, stat_date)`` keys are skipped, so an interrupted run
    can be resumed safely.
    """
    bs = _baostock()
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"baostock login failed: {login.error_msg}")
    existing, seen = _load_existing_shares(resume_path)
    new_rows: list[dict[str, Any]] = []
    requests = 0
    saved_rows = len(existing)
    try:
        for code in codes:
            bs_code = code_to_baostock(code)
            for year in years:
                for quarter in quarters:
                    result = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
                    requests += 1
                    if result.error_code != "0":
                        continue
                    while result.next():
                        fields = dict(zip(result.fields, result.get_row_data(), strict=False))
                        stat_date = fields.get("statDate") or ""
                        key = (code.zfill(6), stat_date)
                        if key in seen:
                            continue
                        seen.add(key)
                        new_rows.append(
                            {
                                "code": code.zfill(6),
                                "stat_date": stat_date or None,
                                "pub_date": fields.get("pubDate") or None,
                                "total_share": _to_float(fields.get("totalShare")),
                                "float_share": _to_float(fields.get("liqaShare")),
                            }
                        )
                    if requests % 500 == 0:
                        print(
                            f"[shares] requests={requests}, new_rows={len(new_rows)}, "
                            f"total_rows={saved_rows + len(new_rows)}",
                            file=sys.stderr,
                            flush=True,
                        )
                    if resume_path is not None and len(new_rows) >= 500:
                        existing = _save_shares(resume_path, existing, new_rows)
                        saved_rows = len(existing)
                        new_rows = []
                    time.sleep(pause)
    finally:
        bs.logout()
    if resume_path is not None and new_rows:
        existing = _save_shares(resume_path, existing, new_rows)
        return existing
    frame = pd.DataFrame(new_rows)
    if frame.empty:
        return existing if resume_path is not None else frame
    return frame.drop_duplicates(["code", "stat_date"], keep="last").sort_values(
        ["code", "stat_date"]
    )


def query_listing_basic(codes: list[str], *, pause: float = 0.05) -> pd.DataFrame:
    bs = _baostock()
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"baostock login failed: {login.error_msg}")
    rows: list[dict[str, Any]] = []
    try:
        for code in codes:
            bs_code = code_to_baostock(code)
            result = bs.query_stock_basic(code=bs_code)
            while result.next():
                fields = dict(zip(result.fields, result.get_row_data(), strict=False))
                rows.append(
                    {
                        "code": code.zfill(6),
                        "ipo_date": pd.Timestamp(fields.get("ipoDate") or pd.NaT).date()
                        if fields.get("ipoDate")
                        else None,
                        "delist_date": pd.Timestamp(fields.get("outDate") or pd.NaT).date()
                        if fields.get("outDate")
                        else None,
                        "security_type": fields.get("type"),
                        "status": fields.get("status"),
                    }
                )
            time.sleep(pause)
    finally:
        bs.logout()
    return pd.DataFrame(rows).drop_duplicates("code", keep="last")


def query_industry_snapshot(codes: list[str], *, pause: float = 0.05) -> pd.DataFrame:
    """Current industry snapshot with baostock's updateDate (NOT historical PIT)."""
    bs = _baostock()
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"baostock login failed: {login.error_msg}")
    rows: list[dict[str, Any]] = []
    try:
        for code in codes:
            bs_code = code_to_baostock(code)
            result = bs.query_stock_industry(code=bs_code)
            while result.next():
                fields = dict(zip(result.fields, result.get_row_data(), strict=False))
                update_date = fields.get("updateDate")
                rows.append(
                    {
                        "code": code.zfill(6),
                        "name": fields.get("code_name"),
                        "industry": fields.get("industry"),
                        "industry_type": fields.get("industryClassification"),
                        "update_date": pd.Timestamp(update_date).date() if update_date else None,
                        "pit_note": "snapshot_not_historical",
                    }
                )
            time.sleep(pause)
    finally:
        bs.logout()
    return pd.DataFrame(rows).drop_duplicates("code", keep="last")


def shares_to_market_cap(frame: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    """Join baostock total shares onto a price panel as-of by pub_date.

    This is the PIT-safe path: shares become effective only on their
    publication date, never retroactively.
    """
    from bridge.data import attach_total_shares

    effective = shares.rename(
        columns={
            "pub_date": "effective_date",
            "total_share": "total_shares",
        }
    )
    effective = effective[["code", "effective_date", "total_shares"]].dropna()
    work = frame.copy()
    had_trade_date = "trade_date" in work.columns
    if not had_trade_date and "date" in work.columns:
        work = work.rename(columns={"date": "trade_date"})
    merged = attach_total_shares(work, effective)
    if not had_trade_date:
        merged = merged.rename(columns={"trade_date": "date"})
    return merged


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Baostock reference-data integration")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--codes", default="000001,600519")
    parser.add_argument("--codes-file", default=None)
    parser.add_argument("--years", default="2023,2024,2025")
    sub = parser.add_subparsers(dest="command", required=True)
    shares = sub.add_parser("shares")
    shares.add_argument("--resume", default=None, help="incremental parquet path")
    shares.add_argument("--pause", type=float, default=0.02)
    listing = sub.add_parser("listing")
    industry = sub.add_parser("industry")
    for p in (shares, listing, industry):
        pass  # args shared above
    args = parser.parse_args()

    if args.codes_file:
        codes = [
            line.strip().zfill(6)
            for line in Path(args.codes_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        codes = [code.strip().zfill(6) for code in args.codes.split(",") if code.strip()]
    years = [int(year) for year in args.years.split(",")]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "shares":
        path = Path(args.resume) if args.resume else out_dir / "shares_pit.parquet"
        frame = query_shares_quarters(codes, years, pause=args.pause, resume_path=path)
        print(json.dumps({"rows": len(frame), "path": str(path)}, ensure_ascii=False))
    elif args.command == "listing":
        frame = query_listing_basic(codes)
        path = out_dir / "listing_basic.parquet"
        frame.to_parquet(path, index=False)
        print(json.dumps({"rows": len(frame), "path": str(path)}, ensure_ascii=False))
    elif args.command == "industry":
        frame = query_industry_snapshot(codes)
        path = out_dir / "industry_snapshot.parquet"
        frame.to_parquet(path, index=False)
        print(json.dumps({"rows": len(frame), "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
