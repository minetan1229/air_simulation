"""Regression tests for security, packaging, and architecture audit fixes."""

from __future__ import annotations

import os
import random
import tempfile
import tomllib
from pathlib import Path

import numpy as np
import pytest
from setuptools import find_packages

from api.core import BadRequest
from api.routes.ga_research import _safe_ga_path
from app import _is_origin_allowed
from optimization.ga_domain import _decode_rng_state, _encode_rng_state
from optimization.pareto_archive import (
    crowding_distance_matrix,
    dominance_matrix,
    non_dominated_sort_matrix,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_safe_ga_path_blocks_outside_absolute_path(tmp_path: Path) -> None:
    """SEC-01: Absolute paths outside the allowed base_dir must be rejected."""
    base = tmp_path / "allowed"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.json"

    with pytest.raises(BadRequest, match="escapes the allowed directory"):
        _safe_ga_path(str(target), "config_path", base_dir=str(base))

    inside = base / "valid.json"
    assert _safe_ga_path(str(inside), "config_path", base_dir=str(base)) == str(inside.resolve())


def test_safe_ga_path_respects_allowlist_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-01: Explicitly allowlisted directories via GA_ALLOWED_DIRS are permitted."""
    base = tmp_path / "base"
    extra = tmp_path / "extra"
    base.mkdir()
    extra.mkdir()
    target = extra / "checkpoint.json"

    monkeypatch.setenv("GA_ALLOWED_DIRS", str(extra))
    assert _safe_ga_path(str(target), "checkpoint", base_dir=str(base)) == str(target.resolve())


def test_ga_rng_state_json_roundtrip_and_rejects_pickle() -> None:
    """SEC-02: RNG state uses pure JSON primitives and rejects legacy pickle strings."""
    rng = random.Random(12345)
    _ = [rng.random() for _ in range(5)]
    encoded = _encode_rng_state(rng.getstate())
    assert isinstance(encoded, dict)
    assert set(encoded.keys()) == {"version", "state", "gauss_next"}

    restored_rng = random.Random(0)
    restored_rng.setstate(_decode_rng_state(encoded))
    assert [rng.random() for _ in range(5)] == [restored_rng.random() for _ in range(5)]

    with pytest.raises(ValueError, match="pickle deserialization is disabled"):
        _decode_rng_state("gASVAAAAAAAA")


def test_cors_origin_strict_hostname_validation() -> None:
    """SEC-05: CORS local-origin validation must reject spoofing prefixes."""
    assert _is_origin_allowed("http://localhost:5000") is True
    assert _is_origin_allowed("https://127.0.0.1:8443") is True
    assert _is_origin_allowed("http://localhost") is True
    assert _is_origin_allowed("http://127.0.0.1") is True

    assert _is_origin_allowed("http://localhost:80.example.com") is False
    assert _is_origin_allowed("http://127.0.0.1:anything") is False
    assert _is_origin_allowed("http://localhost@evil.com") is False
    assert _is_origin_allowed("http://localhost:5000/extra") is False
    assert _is_origin_allowed("ftp://localhost:5000") is False


def test_pyproject_includes_infrastructure_and_runtime_deps() -> None:
    """PKG-01: pyproject.toml must package infrastructure* and list orjson/pytest-xdist."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)

    find_cfg = data["tool"]["setuptools"]["packages"]["find"]
    pkgs = find_packages(
        where=str(REPO_ROOT / find_cfg["where"][0]),
        include=find_cfg["include"],
        exclude=find_cfg["exclude"],
    )
    assert "infrastructure" in pkgs

    deps = data["project"]["dependencies"]
    dev_deps = data["project"]["optional-dependencies"]["dev"]
    assert any(d.startswith("orjson") for d in deps)
    assert any(d.startswith("pytest-xdist") for d in dev_deps)


def test_physics_investigate_reexports_optimization_investigate() -> None:
    """ARCH-01: physics.investigate shims re-export canonical optimization.investigate objects."""
    import optimization.investigate as opt_inv
    import physics.investigate as phys_inv

    assert phys_inv.DiagnosisReport is opt_inv.DiagnosisReport
    assert phys_inv.compute_flight_metrics is opt_inv.compute_flight_metrics


def test_pareto_archive_dominance_and_sorting() -> None:
    """ARCH-02: Cover optimization.pareto_archive dominance, sorting, and crowding distance."""
    matrix = np.array(
        [
            [10.0, 2.0],
            [8.0, 4.0],
            [5.0, 1.0],
            [np.nan, 3.0],
        ],
        dtype=float,
    )
    directions = np.array([1.0, 1.0], dtype=float)
    dom = dominance_matrix(matrix, directions)
    assert dom[0, 2] and dom[1, 2]
    assert not dom[0, 1] and not dom[1, 0]
    assert dom[0, 3] and not dom[3, 0]

    fronts = non_dominated_sort_matrix(matrix, directions)
    assert set(fronts[0]) == {0, 1}
    assert fronts[1] == [2]
    assert fronts[2] == [3]

    cd = crowding_distance_matrix(matrix, fronts[0])
    assert len(cd) == 2
    assert all(val == float("inf") for val in cd)
