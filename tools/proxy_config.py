"""Proxy configuration and environment resolution for agent backends (Kilo Code, etc.).

Supports loading proxy configurations from:
1. Explicit arguments/parameters (single URL or comma-separated list)
2. Environment variables (HTTPS_PROXY, HTTP_PROXY, ALL_PROXY, NO_PROXY, KILO_PROXY, KILO_PROXIES)
3. JSON configuration file (default: tools/proxy_config.json or .proxy_config.json)
   Supports either single proxy keys or a "proxies" list for automatic 429 rotation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "tools" / "proxy_config.json"
ALTERNATIVE_CONFIG_PATH = PROJECT_ROOT / ".proxy_config.json"


def load_proxy_file(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load proxy configuration from a JSON file if it exists."""
    target_path = config_path or (
        DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.is_file() else ALTERNATIVE_CONFIG_PATH
    )
    if not target_path.is_file():
        return {}
    try:
        content = target_path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def resolve_proxy_pool(
    explicit_proxy: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> List[str]:
    """Resolve a list of proxy endpoints available for rotation."""
    pool: List[str] = []

    # 1. Explicit argument (may be comma-separated)
    if explicit_proxy:
        for item in explicit_proxy.split(","):
            val = item.strip()
            if val and val not in pool:
                pool.append(val)

    # 2. Environment variable list
    env_pool = os.getenv("KILO_PROXIES", "").strip()
    if env_pool:
        for item in env_pool.split(","):
            val = item.strip()
            if val and val not in pool:
                pool.append(val)

    # 3. File configuration
    file_cfg = load_proxy_file(config_path)
    file_proxies = file_cfg.get("proxies")
    if isinstance(file_proxies, list):
        for item in file_proxies:
            if isinstance(item, str) and item.strip() and item.strip() not in pool:
                pool.append(item.strip())

    # 4. Single proxy fallbacks
    single_env = os.getenv("KILO_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("ALL_PROXY")
    if single_env and single_env.strip() not in pool:
        pool.append(single_env.strip())

    single_file = file_cfg.get("https_proxy") or file_cfg.get("all_proxy") or file_cfg.get("proxy")
    if isinstance(single_file, str) and single_file.strip() and single_file.strip() not in pool:
        pool.append(single_file.strip())

    return pool


class ProxyRotator:
    """Manages round-robin rotation across multiple proxies upon 429."""

    def __init__(self, proxies: Optional[List[str]] = None) -> None:
        self.proxies: List[str] = list(proxies or [])
        self._index: int = 0

    @classmethod
    def from_config(
        cls,
        explicit_proxy: Optional[str] = None,
        config_path: Optional[Path] = None,
    ) -> ProxyRotator:
        pool = resolve_proxy_pool(explicit_proxy=explicit_proxy, config_path=config_path)
        return cls(proxies=pool)

    @property
    def current(self) -> Optional[str]:
        if not self.proxies:
            return None
        return self.proxies[self._index % len(self.proxies)]

    def rotate(self) -> Optional[str]:
        """Advance to the next proxy in the pool and return it."""
        if not self.proxies:
            return None
        self._index = (self._index + 1) % len(self.proxies)
        return self.current

    def __len__(self) -> int:
        return len(self.proxies)


def resolve_proxy_settings(
    explicit_proxy: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Resolve active proxy settings into a dictionary of proxy variables."""
    settings: Dict[str, str] = {}
    file_cfg = load_proxy_file(config_path)

    # 1. Base from file
    http_proxy = file_cfg.get("http_proxy") or file_cfg.get("HTTP_PROXY") or ""
    https_proxy = file_cfg.get("https_proxy") or file_cfg.get("HTTPS_PROXY") or ""
    all_proxy = file_cfg.get("all_proxy") or file_cfg.get("ALL_PROXY") or file_cfg.get("proxy") or ""
    no_proxy = file_cfg.get("no_proxy") or file_cfg.get("NO_PROXY") or ""

    # 2. File list fallback
    file_proxies = file_cfg.get("proxies")
    if isinstance(file_proxies, list) and file_proxies and not (http_proxy or https_proxy or all_proxy):
        first_proxy = str(file_proxies[0]).strip()
        all_proxy = first_proxy

    # 3. Override from environment
    http_proxy = os.getenv("HTTP_PROXY", os.getenv("http_proxy", http_proxy))
    https_proxy = os.getenv("HTTPS_PROXY", os.getenv("https_proxy", https_proxy))
    all_proxy = os.getenv("KILO_PROXY", os.getenv("ALL_PROXY", os.getenv("all_proxy", all_proxy)))
    no_proxy = os.getenv("NO_PROXY", os.getenv("no_proxy", no_proxy))

    # 4. Explicit override
    if explicit_proxy:
        first_explicit = explicit_proxy.split(",")[0].strip()
        all_proxy = first_explicit
        https_proxy = first_explicit
        http_proxy = first_explicit

    if all_proxy:
        if not https_proxy:
            https_proxy = all_proxy
        if not http_proxy:
            http_proxy = all_proxy

    if http_proxy:
        settings["http_proxy"] = http_proxy
        settings["HTTP_PROXY"] = http_proxy
    if https_proxy:
        settings["https_proxy"] = https_proxy
        settings["HTTPS_PROXY"] = https_proxy
    if all_proxy:
        settings["all_proxy"] = all_proxy
        settings["ALL_PROXY"] = all_proxy
    if no_proxy:
        settings["no_proxy"] = no_proxy
        settings["NO_PROXY"] = no_proxy

    return settings


def inject_proxy_to_env(
    env: Optional[Dict[str, str]] = None,
    explicit_proxy: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Return a copy of the environment dictionary with proxy settings injected."""
    new_env = dict(env if env is not None else os.environ)
    proxies = resolve_proxy_settings(explicit_proxy=explicit_proxy, config_path=config_path)
    new_env.update(proxies)
    return new_env


def format_proxy_summary(explicit_proxy: Optional[str] = None) -> str:
    """Return a human-readable one-line summary of active proxy configuration."""
    pool = resolve_proxy_pool(explicit_proxy=explicit_proxy)
    if not pool:
        return "None (direct connection)"
    if len(pool) == 1:
        return f"Single proxy: {pool[0]}"
    return f"Pool of {len(pool)} proxies (Active: {pool[0]})"
