"""Explicit runtime locations for the sibling projects and data directory.

The fusion pipeline used to infer sibling locations from ``Path.parents``.
That made a copied checkout silently read a different local repository.  The
active CLIs now require these locations to be supplied through environment
variables and validate them before reading any upstream artifact.

Environment variables:

``A_SHARE_FACTOR_ENGINE_V6_ROOT``
    Checkout of ``a-share-factor-engine-v6``.
``AURUMQ_RL_ROOT``
    Checkout of ``aurumq-rl``.
``QUANTGPT_ROOT``
    Checkout of ``QuantGPT``.
``A_SHARE_KLINE_DATA_ROOT``
    Directory containing the normalized K-line CSVs and benchmark files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class UpstreamSpec:
    key: str
    env_var: str
    description: str
    markers: tuple[str, ...]


UPSTREAM_SPECS = {
    "v6": UpstreamSpec(
        key="v6",
        env_var="A_SHARE_FACTOR_ENGINE_V6_ROOT",
        description="a-share-factor-engine-v6 checkout",
        markers=("src", "pyproject.toml"),
    ),
    "aurumq": UpstreamSpec(
        key="aurumq",
        env_var="AURUMQ_RL_ROOT",
        description="aurumq-rl checkout",
        markers=("src",),
    ),
    "quantgpt": UpstreamSpec(
        key="quantgpt",
        env_var="QUANTGPT_ROOT",
        description="QuantGPT checkout",
        markers=("quantgpt",),
    ),
    "kline": UpstreamSpec(
        key="kline",
        env_var="A_SHARE_KLINE_DATA_ROOT",
        description="A-share K-line data directory",
        markers=("_bench_000001SH.csv",),
    ),
}


class UpstreamPathError(RuntimeError):
    """Raised when a required sibling dependency is not configured safely."""


def _spec(key: str) -> UpstreamSpec:
    try:
        return UPSTREAM_SPECS[key]
    except KeyError as exc:
        known = ", ".join(sorted(UPSTREAM_SPECS))
        raise ValueError(f"unknown upstream key {key!r}; expected one of: {known}") from exc


def _normalise_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def resolve_upstream_root(key: str, *, required: bool = True) -> Path | None:
    """Resolve one dependency root from its environment variable.

    ``required=False`` is intended only for import-time optional defaults.  A
    command that is about to consume an upstream file must call
    :func:`validate_startup` first (or use :func:`require_upstream_root`).
    """

    spec = _spec(key)
    raw = os.environ.get(spec.env_var, "").strip()
    if not raw:
        if required:
            raise UpstreamPathError(
                f"missing {spec.env_var}; set it to the {spec.description} path"
            )
        return None
    return _normalise_path(raw)


def configured_upstream_path(key: str, *parts: str) -> Path | None:
    """Return an import-time default without validating or inferring a path."""

    root = resolve_upstream_root(key, required=False)
    return root.joinpath(*parts) if root is not None else None


def _marker_exists(root: Path, marker: str) -> bool:
    target = root / marker
    # Directory markers (src/, quantgpt/) and file markers are both checked by
    # type so an unrelated file named ``src`` cannot satisfy startup setup.
    return target.is_dir() if Path(marker).suffix == "" else target.is_file()


def upstream_path(key: str, *parts: str) -> Path:
    """Resolve a path below a configured upstream root."""

    root = resolve_upstream_root(key)
    assert root is not None
    return root.joinpath(*parts)


def validate_startup(required: Iterable[str] = tuple(UPSTREAM_SPECS)) -> dict[str, Path]:
    """Validate all dependencies needed by a command before it reads data.

    The check deliberately verifies both the environment variable and a small
    set of stable markers.  It does not require generated artifacts, so a
    freshly cloned upstream repository can pass setup validation before its
    research outputs are built.
    """

    roots: dict[str, Path] = {}
    errors: list[str] = []
    for key in required:
        spec = _spec(key)
        try:
            root = resolve_upstream_root(key)
        except UpstreamPathError as exc:
            errors.append(str(exc))
            continue
        assert root is not None
        if not root.is_dir():
            errors.append(f"{spec.env_var}={root} is not an existing directory")
            continue
        missing = [marker for marker in spec.markers if not _marker_exists(root, marker)]
        if missing:
            errors.append(
                f"{spec.env_var}={root} is not a valid {spec.description}; "
                f"missing marker(s): {', '.join(missing)}"
            )
            continue
        roots[key] = root
    if errors:
        hint = "\n".join(f"  - {error}" for error in errors)
        raise UpstreamPathError(
            "upstream dependency startup check failed:\n"
            f"{hint}\n"
            "Set the paths in the current process environment and retry."
        )
    return roots


def require_upstream_root(key: str) -> Path:
    """Validate and return one upstream root for library callers."""

    return validate_startup((key,))[key]


def validate_startup_or_exit(required: Iterable[str] = tuple(UPSTREAM_SPECS)) -> dict[str, Path]:
    """CLI wrapper that turns setup errors into a concise non-zero exit."""

    try:
        return validate_startup(required)
    except UpstreamPathError as exc:
        print(f"[startup] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate fusion pipeline upstream paths")
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated dependency keys (v6,aurumq,quantgpt,kline); default: all",
    )
    args = parser.parse_args()
    keys = (
        tuple(item.strip() for item in args.only.split(",") if item.strip())
        if args.only
        else tuple(UPSTREAM_SPECS)
    )
    roots = validate_startup_or_exit(keys)
    print(json.dumps({key: str(path) for key, path in roots.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
