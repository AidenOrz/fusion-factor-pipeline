"""Developer observation lockbox (relaxed shadow of the official v6 lockbox).

The official ``a-share-factor-engine-v6`` lockbox keeps its strict single-use
formal reveal untouched. This module is a **separate shadow observer** with the
same SHA-256 append-only chain discipline, but:

* ``observe`` is an unlimited, non-consumptive read;
* ``formal-reveal`` stays single-use per epoch;
* every output is stamped ``developer_observation_only``.

Inputs and outputs never modify v6 production configs or the official lockbox.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CLASSIFICATION = "developer_observation_only"
REQUIRED_COLUMNS = ("date", "code", "score")
DEFAULT_ALLOWED_EXTRA = (
    "name",
    "signal",
    "close",
    "stop_loss",
    "risk_pct",
    "phase_tag",
    "trigger_tag",
    "volume_tag",
    "late_stage",
    "late_stage_tag",
    "late_stage_pct_rank",
    "sector_name",
    "sector_grade_cap",
    "acceptance_tag",
    "location_tag",
    "follow_tag",
    "stage2_tag",
    "risk_tags",
    "sector_flow_z",
    "sector_flow_tag",
    "veto_reasons",
    "dimension__contraction",
    "dimension__trigger_quality",
    "dimension__position",
    "corporate_action_tag",
)
FORBIDDEN_COLUMN_PREFIXES = ("exret", "mfe", "mae", "net_r", "pnl")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "artifacts" / "dev_lockbox"


class LockboxError(Exception):
    """Raised when a lockbox invariant is violated."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _validate_signal_rows(rows: list[dict[str, str]], expected_date: date) -> int:
    if not rows:
        raise LockboxError("Signal CSV contains no data rows")
    for row in rows:
        if row.get("date", "").strip() != expected_date.isoformat():
            raise LockboxError(
                f"Signal row date {row.get('date')!r} does not match --date {expected_date}"
            )
    codes = []
    for row in rows:
        raw_code = (row.get("code") or "").strip()
        if raw_code.endswith(".0"):
            raw_code = raw_code[:-2]
        if not raw_code.isdigit() or len(raw_code) != 6:
            raise LockboxError(f"Signal code must be a 6-digit string, got {raw_code!r}")
        codes.append(raw_code)
    if len(set(codes)) != len(codes):
        raise LockboxError("Signal CSV contains duplicate stock codes for the same date")
    return len(rows)


def _validate_columns(fieldnames: list[str], allowed_extra: tuple[str, ...] = DEFAULT_ALLOWED_EXTRA) -> None:
    names = [name.lower() for name in fieldnames]
    missing = [column for column in REQUIRED_COLUMNS if column not in names]
    if missing:
        raise LockboxError(f"Signal CSV is missing required columns: {missing}")
    for name in names:
        if any(name.startswith(prefix) for prefix in FORBIDDEN_COLUMN_PREFIXES):
            raise LockboxError(
                f"Signal CSV contains a forward-looking column {name!r}; "
                "candidate rows must not carry results"
            )
    extra = [name for name in names if name not in REQUIRED_COLUMNS and name not in allowed_extra]
    if extra:
        raise LockboxError(f"Signal CSV contains columns outside the allow-list: {extra}")


class DevLockbox:
    """Append-only, SHA-256-chained observation lockbox."""

    def __init__(self, root: Path | str = DEFAULT_ROOT, epoch: str = "dev") -> None:
        self.root = Path(root)
        self.epoch = epoch
        self.epoch_dir = self.root / epoch
        self.registry_path = self.epoch_dir / "registry.json"
        self.chain_path = self.epoch_dir / "chain.json"
        self.signals_dir = self.epoch_dir / "signals"
        self.results_path = self.epoch_dir / "results.json"

    # ------------------------------------------------------------------ init
    def init(
        self,
        test_start: str,
        signal_generator: str = "dev_observation",
        allowed_extra: tuple[str, ...] = DEFAULT_ALLOWED_EXTRA,
    ) -> dict[str, Any]:
        if self.registry_path.exists():
            raise LockboxError(f"Lockbox epoch {self.epoch!r} already exists: {self.registry_path}")
        self.epoch_dir.mkdir(parents=True, exist_ok=True)
        self.signals_dir.mkdir(parents=True, exist_ok=True)
        registry = {
            "schema": SCHEMA_VERSION,
            "classification": CLASSIFICATION,
            "epoch": self.epoch,
            "mode": "developer_observation",
            "status": "active",
            "test_start": _parse_date(test_start).isoformat(),
            "signal_generator": signal_generator,
            "formal_reveal_limit": 1,
            "formal_reveal_count": 0,
            "formal_reveal_consumed": False,
            "results_sha256": None,
            "entries": 0,
            "chain_head": None,
            "allowed_extra_columns": list(allowed_extra),
        }
        _write_json(self.registry_path, registry)
        _write_json(self.chain_path, {"entries": []})
        return self.status()

    # ----------------------------------------------------------------- read
    def _registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            raise LockboxError(f"No lockbox registry at {self.registry_path}; run init first")
        return _read_json(self.registry_path)

    def _chain(self) -> dict[str, Any]:
        if not self.chain_path.is_file():
            raise LockboxError(f"No lockbox chain at {self.chain_path}; run init first")
        return _read_json(self.chain_path)

    def status(self) -> dict[str, Any]:
        registry = self._registry()
        return {
            "classification": CLASSIFICATION,
            "epoch": registry["epoch"],
            "status": registry["status"],
            "test_start": registry["test_start"],
            "entries": registry["entries"],
            "chain_head": registry["chain_head"],
            "formal_reveal_count": registry["formal_reveal_count"],
            "formal_reveal_consumed": registry["formal_reveal_consumed"],
            "latest_dates": self._latest_dates(),
        }

    def _latest_dates(self, limit: int = 5) -> list[str]:
        chain = self._chain()
        entries = chain.get("entries", [])
        return [entry["date"] for entry in entries[-limit:]]

    # ---------------------------------------------------------------- append
    def append(self, append_date: str, signals_csv: Path | str) -> dict[str, Any]:
        registry = self._registry()
        if registry.get("status") != "active":
            raise LockboxError(f"Lockbox epoch is not active (status={registry.get('status')})")
        expected = _parse_date(append_date)
        source = Path(signals_csv)
        if not source.is_file():
            raise FileNotFoundError(f"Signal file missing: {source}")

        raw = source.read_bytes()
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
        _validate_columns(fieldnames, tuple(registry.get("allowed_extra_columns", DEFAULT_ALLOWED_EXTRA)))
        n_rows = _validate_signal_rows(rows, expected)

        test_start = _parse_date(registry["test_start"])
        if expected < test_start:
            raise LockboxError(
                f"Append date {expected} is earlier than epoch test_start {test_start}"
            )
        chain = self._chain()
        entries = chain.get("entries", [])
        previous_hash = entries[-1]["entry_hash"] if entries else None
        previous_date = _parse_date(entries[-1]["date"]) if entries else None
        if previous_date is not None and expected <= previous_date:
            raise LockboxError(
                f"Append date {expected} must be strictly later than previous {previous_date}"
            )

        snapshot_dir = self.signals_dir / expected.isoformat()
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_dir / "signals.csv"
        if snapshot.exists():
            raise LockboxError(f"A different signal file is already sealed for {expected}")
        snapshot.write_bytes(raw)

        payload = {
            "seq": len(entries) + 1,
            "date": expected.isoformat(),
            "signals_sha256": sha256_bytes(raw),
            "n_rows": n_rows,
            "previous_hash": previous_hash,
        }
        entry_hash = _json_digest(payload)
        entry = {**payload, "entry_hash": entry_hash}
        entries.append(entry)
        _write_json(self.chain_path, {"entries": entries})

        registry["entries"] = len(entries)
        registry["chain_head"] = entry_hash
        _write_json(self.registry_path, registry)
        return {"classification": CLASSIFICATION, "entry": entry}

    # ---------------------------------------------------------------- verify
    def verify(self) -> dict[str, Any]:
        registry = self._registry()
        chain = self._chain()
        entries = chain.get("entries", [])
        previous = None
        previous_date: date | None = None
        for entry in entries:
            payload = {key: value for key, value in entry.items() if key != "entry_hash"}
            if _json_digest(payload) != entry.get("entry_hash"):
                raise LockboxError(f"Chain entry hash mismatch at seq {entry.get('seq')}")
            if entry.get("previous_hash") != previous:
                raise LockboxError(f"Chain link mismatch at seq {entry.get('seq')}")
            current_date = _parse_date(entry["date"])
            if previous_date is not None and current_date <= previous_date:
                raise LockboxError("Chain dates are not strictly increasing")
            snapshot = self.signals_dir / current_date.isoformat() / "signals.csv"
            if not snapshot.is_file():
                raise LockboxError(f"Snapshot missing for {current_date}")
            if sha256_bytes(snapshot.read_bytes()) != entry.get("signals_sha256"):
                raise LockboxError(f"Snapshot hash mismatch for {current_date}")
            previous = entry["entry_hash"]
            previous_date = current_date
        if registry.get("entries") != len(entries):
            raise LockboxError("Registry entry count disagrees with the chain")
        if entries and registry.get("chain_head") != entries[-1]["entry_hash"]:
            raise LockboxError("Registry chain_head disagrees with the chain")
        return {
            "classification": CLASSIFICATION,
            "valid": True,
            "entries": len(entries),
            "chain_head": previous,
            "formal_reveal_consumed": registry.get("formal_reveal_consumed", False),
        }

    # --------------------------------------------------------------- observe
    def observe(
        self,
        observe_date: str | None = None,
        results_file: Path | str | None = None,
    ) -> dict[str, Any]:
        """Non-consumptive read. Never increments formal_reveal_count."""
        verification = self.verify()
        registry = self._registry()
        chain = self._chain()
        entries = chain.get("entries", [])
        if observe_date is not None:
            wanted = _parse_date(observe_date).isoformat()
            entries = [entry for entry in entries if entry["date"] == wanted]
            if not entries:
                raise LockboxError(f"No sealed entry for {wanted}")
        result_summary = None
        if results_file is not None:
            result_summary = self._load_results(Path(results_file))
        return {
            "classification": CLASSIFICATION,
            "observation": "non_consumptive",
            "formal_reveal_count": registry.get("formal_reveal_count", 0),
            "entries": entries,
            "result_summary": result_summary,
            "verification": verification,
        }

    def _load_results(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f"Results file missing: {path}")
        payload = _read_json(path)
        required = ("period_start", "period_end", "closed_trades", "metrics")
        missing = [key for key in required if key not in payload]
        if missing:
            raise LockboxError(f"Results JSON is missing fields: {missing}")
        if not isinstance(payload["closed_trades"], int) or payload["closed_trades"] < 0:
            raise LockboxError("closed_trades must be a non-negative integer")
        return payload

    # --------------------------------------------------------- formal reveal
    def formal_reveal(self, results_file: Path | str) -> dict[str, Any]:
        registry = self._registry()
        if registry.get("formal_reveal_consumed"):
            raise LockboxError("Formal reveal for this epoch has already been consumed")
        verification = self.verify()
        if verification["entries"] == 0:
            raise LockboxError("Cannot reveal an empty epoch")
        payload = self._load_results(Path(results_file))
        raw = Path(results_file).read_bytes()
        self.results_path.write_bytes(raw)
        registry["results_sha256"] = sha256_bytes(raw)
        registry["formal_reveal_count"] = int(registry.get("formal_reveal_count", 0)) + 1
        registry["formal_reveal_consumed"] = True
        registry["status"] = "revealed"
        _write_json(self.registry_path, registry)
        return {
            "classification": CLASSIFICATION,
            "formal_reveal_consumed": True,
            "results_sha256": registry["results_sha256"],
            "verification": verification,
        }


# --------------------------------------------------------------------- CLI
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Developer observation lockbox")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--epoch", default="dev")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--test-start", required=True)

    append = sub.add_parser("append")
    append.add_argument("--date", required=True)
    append.add_argument("--signals", required=True)

    observe = sub.add_parser("observe")
    observe.add_argument("--date", default=None)
    observe.add_argument("--with-results", default=None)

    sub.add_parser("status")
    sub.add_parser("verify")

    reveal = sub.add_parser("formal-reveal")
    reveal.add_argument("--results", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    box = DevLockbox(args.root, args.epoch)
    try:
        if args.command == "init":
            output = box.init(args.test_start)
        elif args.command == "append":
            output = box.append(args.date, args.signals)
        elif args.command == "observe":
            output = box.observe(args.date, args.with_results)
        elif args.command == "status":
            output = box.status()
        elif args.command == "verify":
            output = box.verify()
        elif args.command == "formal-reveal":
            output = box.formal_reveal(args.results)
        else:  # pragma: no cover - argparse guards this
            raise LockboxError(f"Unknown command: {args.command}")
    except (LockboxError, FileNotFoundError, ValueError) as exc:
        print(f"[lockbox] error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
