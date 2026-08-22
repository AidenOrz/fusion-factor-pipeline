from pathlib import Path

import pytest

from bridge.paths import UpstreamPathError, configured_upstream_path, validate_startup


def _make_root(path: Path, *markers: str) -> Path:
    path.mkdir()
    for marker in markers:
        target = path / marker
        target.parent.mkdir(parents=True, exist_ok=True)
        if "." in target.name:
            target.write_text("", encoding="utf-8")
        else:
            target.mkdir(exist_ok=True)
    return path


def test_validate_startup_requires_explicit_environment(monkeypatch):
    monkeypatch.delenv("A_SHARE_FACTOR_ENGINE_V6_ROOT", raising=False)

    with pytest.raises(UpstreamPathError, match="A_SHARE_FACTOR_ENGINE_V6_ROOT"):
        validate_startup(("v6",))


def test_validate_startup_checks_repository_markers(monkeypatch, tmp_path):
    root = _make_root(tmp_path / "v6", "src", "pyproject.toml")
    monkeypatch.setenv("A_SHARE_FACTOR_ENGINE_V6_ROOT", str(root))

    resolved = validate_startup(("v6",))

    assert resolved["v6"] == root.resolve()
    assert configured_upstream_path("v6", "artifacts", "panel") == root.resolve() / "artifacts" / "panel"


def test_validate_startup_rejects_wrong_repository(monkeypatch, tmp_path):
    root = tmp_path / "not-v6"
    root.mkdir()
    monkeypatch.setenv("A_SHARE_FACTOR_ENGINE_V6_ROOT", str(root))

    with pytest.raises(UpstreamPathError, match="missing marker"):
        validate_startup(("v6",))
