"""Standalone smoke runner for the dev lockbox.

Works without pytest tmp_path (useful in restricted sandboxes where the
system temp directory is not readable). Run from the project root:

    python tests/run_smoke.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.dev_lockbox import DevLockbox, LockboxError


def write_signals(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "code", "score", "name"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def make_rows(day: str, codes: tuple[str, ...]) -> list[dict[str, str]]:
    return [
        {"date": day, "code": code, "score": f"{i + 0.1:.1f}", "name": f"stock-{code}"}
        for i, code in enumerate(codes)
    ]


def expect_failure(label: str, fn) -> None:
    try:
        fn()
    except LockboxError:
        print(f"[ok] {label}: rejected as expected")
        return
    raise AssertionError(f"{label}: expected LockboxError, but call succeeded")


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "artifacts" / "smoke_tmp"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)

    box = DevLockbox(root, "dev")
    box.init("2026-07-18")
    assert box.status()["entries"] == 0

    d1 = write_signals(root / "d1.csv", make_rows("2026-07-20", ("000001", "600519")))
    d2 = write_signals(root / "d2.csv", make_rows("2026-07-21", ("000002", "000858")))
    r1 = box.append("2026-07-20", d1)
    r2 = box.append("2026-07-21", d2)
    assert r2["entry"]["previous_hash"] == r1["entry"]["entry_hash"]
    assert box.status()["entries"] == 2

    observed = box.observe("2026-07-20")
    assert observed["observation"] == "non_consumptive"
    assert observed["formal_reveal_count"] == 0
    assert box.verify()["valid"] is True

    expect_failure("duplicate date", lambda: box.append("2026-07-20", d1))
    expect_failure(
        "out-of-order date",
        lambda: box.append("2026-07-19", write_signals(root / "d0.csv", make_rows("2026-07-19", ("000003",)))),
    )

    bad = root / "bad.csv"
    bad.write_text("date,code,score,EXRET20\n2026-07-22,000004,0.5,0.02\n", encoding="utf-8")
    expect_failure("forward-looking column", lambda: box.append("2026-07-22", bad))

    results = root / "results.json"
    results.write_text(
        json.dumps(
            {
                "period_start": "2026-07-18",
                "period_end": "2026-07-21",
                "closed_trades": 2,
                "metrics": {"net_r_median": 0.12, "net_r_iqr": 0.31, "win_rate": 0.5},
            }
        ),
        encoding="utf-8",
    )
    reveal = box.formal_reveal(results)
    assert reveal["formal_reveal_consumed"] is True
    expect_failure("second formal reveal", lambda: box.formal_reveal(results))

    print("[ok] dev lockbox smoke: all checks passed")
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
