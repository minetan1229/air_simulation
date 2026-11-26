"""Tests for proxy configuration resolution and subprocess environment injection."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.proxy_config import (
    ProxyRotator,
    format_proxy_summary,
    inject_proxy_to_env,
    load_proxy_file,
    resolve_proxy_pool,
    resolve_proxy_settings,
)


def test_resolve_proxy_empty(monkeypatch):
    """When no env vars or config file exist, settings should be empty."""
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("KILO_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    with patch("tools.proxy_config.load_proxy_file", return_value={}):
        settings = resolve_proxy_settings()
        assert settings == {}
        assert format_proxy_summary() == "None (direct connection)"


def test_resolve_proxy_from_explicit_arg(monkeypatch):
    """Explicit proxy arg takes precedence and populates both http and https."""
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)

    with patch("tools.proxy_config.load_proxy_file", return_value={}):
        settings = resolve_proxy_settings(explicit_proxy="http://proxy.corp.local:8080")
        assert settings["http_proxy"] == "http://proxy.corp.local:8080"
        assert settings["https_proxy"] == "http://proxy.corp.local:8080"
        assert settings["all_proxy"] == "http://proxy.corp.local:8080"
        assert settings["HTTP_PROXY"] == "http://proxy.corp.local:8080"
        assert settings["HTTPS_PROXY"] == "http://proxy.corp.local:8080"
        assert "http://proxy.corp.local:8080" in format_proxy_summary(explicit_proxy="http://proxy.corp.local:8080")


def test_resolve_proxy_from_file(tmp_path, monkeypatch):
    """Loading proxy config from JSON file."""
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    cfg_file = tmp_path / "custom_proxy.json"
    cfg_file.write_text(
        json.dumps(
            {
                "https_proxy": "http://10.0.0.1:8888",
                "http_proxy": "http://10.0.0.1:8888",
                "no_proxy": "localhost,127.0.0.1",
            }
        ),
        encoding="utf-8",
    )

    settings = resolve_proxy_settings(config_path=cfg_file)
    assert settings["https_proxy"] == "http://10.0.0.1:8888"
    assert settings["HTTPS_PROXY"] == "http://10.0.0.1:8888"
    assert settings["no_proxy"] == "localhost,127.0.0.1"


def test_resolve_proxy_from_env(monkeypatch):
    """Environment variables should override file settings."""
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:8080")
    monkeypatch.setenv("NO_PROXY", "example.com")

    with patch("tools.proxy_config.load_proxy_file", return_value={"https_proxy": "http://file-proxy:8080"}):
        settings = resolve_proxy_settings()
        assert settings["https_proxy"] == "http://env-proxy:8080"
        assert settings["no_proxy"] == "example.com"


def test_inject_proxy_to_env():
    """Environment injection preserves existing variables while adding proxy variables."""
    base_env = {"PATH": "/usr/bin", "FOO": "BAR"}
    injected = inject_proxy_to_env(env=base_env, explicit_proxy="http://test-proxy:8080")
    assert injected["PATH"] == "/usr/bin"
    assert injected["FOO"] == "BAR"
    assert injected["HTTP_PROXY"] == "http://test-proxy:8080"
    assert injected["HTTPS_PROXY"] == "http://test-proxy:8080"


def test_resolve_proxy_pool_comma_separated():
    """Comma-separated proxy list should resolve to individual items in pool."""
    pool = resolve_proxy_pool(explicit_proxy="http://proxy1:8080, http://proxy2:8080,http://proxy3:8080")
    assert pool == ["http://proxy1:8080", "http://proxy2:8080", "http://proxy3:8080"]


def test_resolve_proxy_pool_from_file(tmp_path):
    """File containing 'proxies' array resolves correctly."""
    cfg = tmp_path / "pool.json"
    cfg.write_text(json.dumps({"proxies": ["http://p1:8080", "http://p2:8080"]}), encoding="utf-8")
    pool = resolve_proxy_pool(config_path=cfg)
    assert pool == ["http://p1:8080", "http://p2:8080"]


def test_proxy_rotator_round_robin():
    """ProxyRotator steps through all proxies and wraps around."""
    rotator = ProxyRotator(["http://p1:8080", "http://p2:8080", "http://p3:8080"])
    assert len(rotator) == 3
    assert rotator.current == "http://p1:8080"
    assert rotator.rotate() == "http://p2:8080"
    assert rotator.current == "http://p2:8080"
    assert rotator.rotate() == "http://p3:8080"
    assert rotator.rotate() == "http://p1:8080"
