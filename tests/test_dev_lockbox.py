"""Smoke tests for the relaxed developer observation lockbox."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bridge.dev_lockbox import DevLockbox, LockboxError


def _write_signals(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "code", "score", "name"])
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture()
def box(tmp_path: Path) -> DevLockbox:
    lockbox = DevLockbox(tmp_path, "dev")
    lockbox.init("2026-07-18")
    return lockbox


def _make_rows(day: str, codes: tuple[str, ...]) -> list[dict[str, str]]:
    return [
        {"date": day, "code": code, "score": str(float(index) + 0.1), "name": f"stock-{code}"}
        for index, code in enumerate(codes)
    ]


def test_init_creates_active_epoch(box: DevLockbox) -> None:
    status = box.status()
    assert status["epoch"] == "dev"
    assert status["status"] == "active"
    assert status["entries"] == 0
    assert status["classification"] == "developer_observation_only"


def test_append_chain_and_observe_is_non_consumptive(tmp_path: Path, box: DevLockbox) -> None:
    first = _write_signals(tmp_path / "d1.csv", _make_rows("2026-07-20", ("000001", "600519")))
    second = _write_signals(tmp_path / "d2.csv", _make_rows("2026-07-21", ("000002", "000858")))

    r1 = box.append("2026-07-20", first)
    r2 = box.append("2026-07-21", second)
    assert r1["entry"]["seq"] == 1
    assert r2["entry"]["previous_hash"] == r1["entry"]["entry_hash"]

    status = box.status()
    assert status["entries"] == 2
    assert status["latest_dates"] == ["2026-07-20", "2026-07-21"]

    observed = box.observe("2026-07-20")
    assert observed["observation"] == "non_consumptive"
    assert observed["formal_reveal_count"] == 0
    assert observed["entries"][0]["n_rows"] == 2
    assert box.verify()["valid"] is True


def test_duplicate_or_out_of_order_append_rejected(tmp_path: Path, box: DevLockbox) -> None:
    first = _write_signals(tmp_path / "d1.csv", _make_rows("2026-07-20", ("000001",)))
    box.append("2026-07-20", first)
    with pytest.raises(LockboxError, match="already sealed"):
        box.append("2026-07-20", first)

    later = _write_signals(tmp_path / "d0.csv", _make_rows("2026-07-19", ("000002",)))
    with pytest.raises(LockboxError, match="strictly later"):
        box.append("2026-07-19", later)


def test_forward_columns_and_bad_codes_rejected(tmp_path: Path, box: DevLockbox) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "date,code,score,EXRET20\n2026-07-20,000001,0.5,0.02\n", encoding="utf-8"
    )
    with pytest.raises(LockboxError, match="forward-looking"):
        box.append("2026-07-20", bad)

    bad_code = _write_signals(
        tmp_path / "bad_code.csv", _make_rows("2026-07-20", ("12345",))
    )
    with pytest.raises(LockboxError, match="6-digit"):
        box.append("2026-07-20", bad_code)


def test_tampered_snapshot_breaks_verify(tmp_path: Path, box: DevLockbox) -> None:
    signals = _write_signals(tmp_path / "d1.csv", _make_rows("2026-07-20", ("000001",)))
    box.append("2026-07-20", signals)
    snapshot = tmp_path / "dev" / "signals" / "2026-07-20" / "signals.csv"
    snapshot.write_text("date,code,score,name\n2026-07-20,000001,99.9,tampered\n", encoding="utf-8")
    with pytest.raises(LockboxError, match="hash mismatch"):
        box.verify()


def test_formal_reveal_is_single_use(tmp_path: Path, box: DevLockbox) -> None:
    signals = _write_signals(tmp_path / "d1.csv", _make_rows("2026-07-20", ("000001",)))
    box.append("2026-07-20", signals)
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "period_start": "2026-07-18",
                "period_end": "2026-07-20",
                "closed_trades": 1,
                "metrics": {"net_r_median": 0.01},
            }
        ),
        encoding="utf-8",
    )

    reveal = box.formal_reveal(results)
    assert reveal["formal_reveal_consumed"] is True
    status = box.status()
    assert status["formal_reveal_consumed"] is True
    assert status["status"] == "revealed"

    with pytest.raises(LockboxError, match="already been consumed"):
        box.formal_reveal(results)
