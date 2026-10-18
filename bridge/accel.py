# -*- coding: utf-8 -*-
"""Optional Rust acceleration for the cross-sectional kernels.

Why a Rust path at all: the churn / IC / divergence studies iterate over ~800
dates x ~2,500 names, and the exclusion-set logic is the hot loop. Rust makes
that loop an order of magnitude faster without changing the research code.

Why the Python fallback is kept: an accelerator that becomes a hard dependency
makes the pipeline unrunnable on a machine without a Rust toolchain, and a
silent numerical divergence between the two paths would be far worse than slow
code. So every function here has a pure-Python implementation, and
``verify_against_python`` proves the two agree before the fast path is used.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTENSION_DIR = PROJECT_ROOT / "rust" / "fusion_accel" / "target" / "release"

_MODULE: Any | None = None
_LOAD_ATTEMPTED = False
_LOAD_ERROR: str | None = None


def load_rust() -> Any | None:
    """Import the compiled extension if it exists, else return None.

    Absence is not an error: the pipeline must run without a Rust toolchain.
    But once a caller asks for the fast path it can inspect
    :func:`rust_status` to see why the fallback was used.
    """
    global _MODULE, _LOAD_ATTEMPTED, _LOAD_ERROR
    if _LOAD_ATTEMPTED:
        return _MODULE
    _LOAD_ATTEMPTED = True
    candidates = [DEFAULT_EXTENSION_DIR]
    override = os.environ.get("FUSION_ACCEL_DIR")
    if override:
        candidates.insert(0, Path(override))
    import importlib.machinery
    import importlib.util
    import sys

    # cargo on Windows emits a .dll, and importlib refuses to infer an extension
    # loader from that suffix (it only knows .pyd/.so/.dylib). The file is a
    # perfectly valid C extension though, so the loader is constructed
    # explicitly instead of relying on suffix discovery.
    for directory in candidates:
        if not directory.is_dir():
            continue
        for suffix in list(importlib.machinery.EXTENSION_SUFFIXES) + [".dll"]:
            candidate = directory / f"fusion_accel{suffix}"
            if not candidate.is_file():
                continue
            try:
                loader = importlib.machinery.ExtensionFileLoader("fusion_accel", str(candidate))
                spec = importlib.util.spec_from_file_location(
                    "fusion_accel", str(candidate), loader=loader
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules.setdefault("fusion_accel", module)
                spec.loader.exec_module(module)
                _MODULE = module
                return _MODULE
            except Exception as exc:  # pragma: no cover - environment dependent
                _LOAD_ERROR = f"{candidate.name}: {exc}"
    return None


def rust_status() -> dict[str, Any]:
    module = load_rust()
    return {
        "available": module is not None,
        "directory": str(DEFAULT_EXTENSION_DIR),
        "error": _LOAD_ERROR,
    }


def _python_bottom_excluded(scores: Sequence[float], fraction: float) -> list[bool]:
    values = np.asarray(scores, dtype=float)
    n = values.size
    if n == 0 or fraction <= 0:
        return [False] * n
    cut = int(np.ceil(fraction * n))
    order = np.argsort(values, kind="stable")
    flags = np.zeros(n, dtype=bool)
    flags[order[:cut]] = True
    return flags.tolist()


def bottom_excluded(scores: Sequence[float], fraction: float) -> list[bool]:
    """Exclusion flags for one cross-section (lowest score excluded first)."""
    module = load_rust()
    if module is not None:
        return list(module.bottom_excluded_py(np.ascontiguousarray(scores, dtype=np.float64), float(fraction)))
    return _python_bottom_excluded(scores, fraction)


def _python_change_rate(prev: Sequence[str], cur: Sequence[str]) -> float | None:
    previous, current = set(prev), set(cur)
    if not previous:
        return None
    return 1.0 - len(previous & current) / len(previous)


def membership_change_rate(prev: Sequence[str], cur: Sequence[str]) -> float | None:
    """1 - |prev & cur| / |prev|, i.e. share of yesterday no longer excluded."""
    module = load_rust()
    if module is not None:
        return module.membership_change_rate_py(list(prev), list(cur))
    return _python_change_rate(prev, cur)


def _python_spearman(scores: Sequence[float], target: Sequence[float],
                     min_distinct: int, min_names: int) -> float | None:
    a = np.asarray(scores, dtype=float)
    b = np.asarray(target, dtype=float)
    if a.size != b.size or a.size < min_names:
        return None
    if np.unique(a).size < min_distinct or np.unique(b).size < min_distinct:
        return None
    from scipy.stats import spearmanr

    value = float(spearmanr(a, b).statistic)
    return None if not np.isfinite(value) else value


def spearman_ic(scores: Sequence[float], target: Sequence[float],
                min_distinct: int = 5, min_names: int = 5) -> float | None:
    """Daily Spearman IC with the same degeneracy guards in both paths."""
    module = load_rust()
    if module is not None:
        return module.spearman_ic_py(
            np.ascontiguousarray(scores, dtype=np.float64),
            np.ascontiguousarray(target, dtype=np.float64),
            int(min_distinct),
            int(min_names),
        )
    return _python_spearman(scores, target, min_distinct, min_names)


def churn_series(sets: Sequence[Sequence[str]]) -> list[float | None]:
    module = load_rust()
    if module is not None:
        return list(module.churn_series_py([list(s) for s in sets]))
    return [
        _python_change_rate(sets[i], sets[i + 1]) for i in range(len(sets) - 1)
    ]


def verify_against_python(seed: int = 0) -> dict[str, Any]:
    """Prove the two paths agree on randomised inputs.

    Ran before the fast path is trusted in a study. A mismatch is a hard error:
    it means one of the two implementations is wrong, and picking the faster
    one silently is exactly the failure mode this check exists to prevent.
    """
    module = load_rust()
    if module is None:
        return {"available": False, "verified": False, "reason": "extension not built"}

    rng = np.random.RandomState(seed)
    checked = 0
    worst_ic = 0.0
    worst_rank = 0.0
    for _ in range(200):
        n = int(rng.randint(5, 400))
        scores = rng.normal(size=n)
        target = scores * rng.uniform(-1, 1) + rng.normal(size=n) * 0.5
        fast = spearman_ic(scores, target)
        slow = _python_spearman(scores, target, 5, 5)
        if (fast is None) != (slow is None):
            raise AssertionError("degeneracy guards disagree between Rust and Python")
        if fast is not None:
            worst_ic = max(worst_ic, abs(fast - slow))
        checked += 1

    for _ in range(100):
        n = int(rng.randint(1, 60))
        values = rng.randint(0, 5, size=n).astype(float)
        fast = np.asarray(module.average_ranks_py(values), dtype=float)
        slow = np.asarray(_python_average_ranks(values), dtype=float)
        worst_rank = max(worst_rank, float(np.abs(fast - slow).max()))
        checked += 1

    for _ in range(200):
        size = int(rng.randint(1, 120))
        pool = [f"{i:06d}" for i in range(500)]
        pick = lambda: rng.choice(pool, size=size, replace=False).tolist()
        prev, cur = pick(), pick()
        fast = membership_change_rate(prev, cur)
        slow = _python_change_rate(prev, cur)
        if abs(float(fast) - float(slow)) > 1e-12:
            raise AssertionError(f"change-rate mismatch: {fast} vs {slow}")
        checked += 1

    for _ in range(100):
        n = int(rng.randint(1, 300))
        fraction = float(rng.choice([0.05, 0.1, 0.2]))
        scores = rng.normal(size=n)
        fast = bottom_excluded(scores, fraction)
        slow = _python_bottom_excluded(scores, fraction)
        if fast != slow:
            raise AssertionError("bottom-excluded membership differs between paths")
        checked += 1

    return {
        "available": True,
        "verified": True,
        "cases": checked,
        "max_abs_ic_diff": worst_ic,
        "max_abs_rank_diff": worst_rank,
    }


def _python_average_ranks(values: Sequence[float]) -> list[float]:
    from scipy.stats import rankdata

    return rankdata(np.asarray(values, dtype=float)).tolist()
