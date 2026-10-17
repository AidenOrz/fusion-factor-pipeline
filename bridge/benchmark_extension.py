# -*- coding: utf-8 -*-
"""Close the CSI-All-Share (000985) benchmark gap with a verified source.

Why this exists
---------------
The fusion panel forward labels are defined against the 000985 benchmark
(benchmark_open / benchmark_close). The local archive _bench_000985.csv stops
at 2026-07-17 and baostock does not serve 000985 at all (sh.000985 returns zero
rows), so every forward date carried a NaN benchmark and therefore a NaN
y_excess20 label. That single missing series blocked forward IC, no matter how
complete the stock side was.

What this module does
---------------------
It fetches 000985 from a public quote API and then PROVES the series is the
same instrument as the local archive before writing anything: on the overlap
window the two must agree exactly on open/high/low/close. A source that only
looks plausible is not accepted -- the match must be bit-exact, because the
benchmark enters the label definition the whole C-track rests on.

The output is a small extension parquet. It is deliberately kept separate from
both the upstream archive and the frozen panel: nothing here rewrites history,
and a reader can always tell which dates came from which source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from bridge.paths import upstream_path, validate_startup_or_exit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT / "data" / "refdata" / "benchmark_000985_ext.parquet"

TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
BENCHMARK_SYMBOL = "sh000985"
BENCHMARK_NAME = "CSI All Share (000985)"
MIN_OVERLAP_DAYS = 200
RETRIES = 4
RETRY_SLEEP_SECONDS = 4.0


class BenchmarkSourceError(RuntimeError):
    """Raised when the fetched benchmark cannot be trusted."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_tencent_daily(
    symbol: str,
    start: str,
    end: str,
    *,
    count: int = 2000,
    session: Any = None,
) -> pd.DataFrame:
    """Fetch an index daily series from the Tencent quote API.

    The payload field order is date, open, close, high, low, volume -- note that
    close precedes high/low, which is the opposite of the local CSV order.
    Getting this wrong would silently swap high and close, so the reader is
    verified against the local archive in verify_against_local().
    """

    import requests

    getter = session.get if session is not None else requests.get
    params = {"param": f"{symbol},day,{start},{end},{count},"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    last_error: Exception | None = None
    payload: dict[str, Any] | None = None
    for attempt in range(RETRIES):
        try:
            response = getter(TENCENT_URL, params=params, timeout=25, headers=headers)
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:  # network hiccups are expected here
            last_error = exc
            if attempt + 1 < RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
    if payload is None:
        raise BenchmarkSourceError(
            f"benchmark fetch failed after {RETRIES} attempts: {last_error}"
        ) from last_error

    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("day") or node.get("qfqday") or []
    if not rows:
        raise BenchmarkSourceError(f"benchmark payload for {symbol} carried no rows")
    frame = pd.DataFrame(
        [row[:6] for row in rows],
        columns=["date", "open", "close", "high", "low", "volume"],
    )
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    for column in ("open", "close", "high", "low", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["open", "close", "high", "low"]].isna().any().any():
        raise BenchmarkSourceError("benchmark payload carried non-numeric prices")
    return frame.sort_values("date").reset_index(drop=True)


def verify_against_local(
    fetched: pd.DataFrame,
    local: pd.DataFrame,
    *,
    min_overlap_days: int = MIN_OVERLAP_DAYS,
) -> dict[str, Any]:
    """Require the fetched series to reproduce the local archive exactly."""

    merged = local.merge(fetched, on="date", how="inner", suffixes=("_local", "_fetched"))
    report: dict[str, Any] = {
        "overlap_days": int(len(merged)),
        "local_days": int(len(local)),
        "fetched_days": int(len(fetched)),
        "max_abs_diff": {},
    }
    if len(merged) < min_overlap_days:
        raise BenchmarkSourceError(
            f"only {len(merged)} overlapping days; need at least {min_overlap_days} "
            "before a source can be trusted"
        )
    for column in ("open", "close", "high", "low"):
        diff = (merged[f"{column}_local"] - merged[f"{column}_fetched"]).abs()
        worst = float(diff.max()) if len(diff) else float("nan")
        report["max_abs_diff"][column] = worst
        if not worst == 0.0:
            raise BenchmarkSourceError(
                f"benchmark mismatch on {column}: max abs diff = {worst}; "
                "the fetched series is not the same instrument"
            )
    missing_local = sorted(set(local["date"]) - set(fetched["date"]))
    report["local_dates_missing_from_source"] = len(missing_local)
    if missing_local:
        raise BenchmarkSourceError(
            f"source is missing {len(missing_local)} local archive dates, "
            f"first = {missing_local[0].date()}"
        )
    report["verified_exact"] = True
    return report


def load_local_benchmark(path: Path) -> pd.DataFrame:
    local = pd.read_csv(path)
    local["date"] = pd.to_datetime(local["date"], errors="raise")
    for column in ("open", "close", "high", "low"):
        local[column] = pd.to_numeric(local[column], errors="raise")
    return local.sort_values("date").reset_index(drop=True)


def build_extension(
    *,
    output: Path | str = DEFAULT_OUT,
    local_path: Path | str | None = None,
    start: str = "2023-01-01",
    end: str | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Fetch, verify, and write the benchmark extension as new dates only."""

    local_file = Path(local_path) if local_path else upstream_path("kline", "_bench_000985.csv")
    local = load_local_benchmark(local_file)
    end = end or str(pd.Timestamp.today().date())
    fetched = fetch_tencent_daily(BENCHMARK_SYMBOL, start, end, session=session)
    verification = verify_against_local(fetched, local)

    last_local = local["date"].max()
    extension = fetched.loc[fetched["date"] > last_local].copy()
    if extension.empty:
        raise BenchmarkSourceError(
            f"no benchmark dates beyond the local horizon {last_local.date()}"
        )
    extension["symbol"] = BENCHMARK_SYMBOL
    extension["source"] = "tencent_ifzq"

    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    extension.to_parquet(output_path, index=False)

    manifest = {
        "classification": "benchmark_extension",
        "benchmark_code": "000985",
        "benchmark_name": BENCHMARK_NAME,
        "source_url": TENCENT_URL,
        "source_symbol": BENCHMARK_SYMBOL,
        "local_archive_file": str(local_file),
        "local_archive_sha256": _sha256_file(local_file),
        "local_last_date": str(last_local.date()),
        "extension_start": str(extension["date"].min().date()),
        "extension_end": str(extension["date"].max().date()),
        "extension_rows": int(len(extension)),
        "verification": verification,
        "output_file": str(output_path),
        "output_sha256": _sha256_file(output_path),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "Extension only; the local archive is never rewritten. The fetched series "
            "must reproduce the archive exactly on the overlap window before any new "
            "date is accepted."
        ),
    }
    (output_path.with_suffix(".meta.json")).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Close the 000985 benchmark gap")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--local", default=None, help="local _bench_000985.csv")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    validate_startup_or_exit(("kline",))
    manifest = build_extension(
        output=args.out, local_path=args.local, start=args.start, end=args.end
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
