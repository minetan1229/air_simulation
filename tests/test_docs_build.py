"""Tests for documentation configuration, palette toggles, and multi-language build pipeline."""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


class MkDocsYamlLoader(yaml.SafeLoader):
    """Custom YAML loader that ignores Python object tags like !!python/name."""
    pass


MkDocsYamlLoader.add_multi_constructor("tag:yaml.org,2002:python/", lambda loader, suffix, node: None)


def load_mkdocs_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=MkDocsYamlLoader)


def test_mkdocs_palette_no_media_query_lock() -> None:
    """Ensure mkdocs.yml and mkdocs.en.yml do not use media queries that prevent switching to light mode."""
    for config_filename in ("mkdocs.yml", "mkdocs.en.yml"):
        config_path = REPO_ROOT / config_filename
        assert config_path.exists(), f"{config_filename} must exist"

        data = load_mkdocs_yaml(config_path)

        theme = data.get("theme", {})
        palettes = theme.get("palette", [])
        assert len(palettes) >= 2, f"{config_filename} should define at least 2 palettes for light/dark"

        schemes = [p.get("scheme") for p in palettes]
        assert "default" in schemes, f"{config_filename} must include 'default' (light) scheme"
        assert "slate" in schemes, f"{config_filename} must include 'slate' (dark) scheme"

        # Crucial: verify that 'media' is not set to prefers-color-scheme on palettes,
        # which blocks switching to light mode on dark OS
        for p in palettes:
            media = p.get("media")
            assert not media or media == "", (
                f"{config_filename} palette '{p.get('scheme')}' has media query '{media}', "
                f"which causes browser to revert and prevents toggling to light mode."
            )


def test_docs_overrides_hero_supports_english() -> None:
    """Ensure docs/overrides/main.html does not hardcode Japanese hero when language is English."""
    override_path = REPO_ROOT / "docs" / "overrides" / "main.html"
    assert override_path.exists(), "docs/overrides/main.html must exist"
    content = override_path.read_text(encoding="utf-8")

    assert "config.theme.language == 'en'" in content or 'config.theme.language == "en"' in content
    assert "Academic-grade" in content or "Flight Dynamics" in content


def test_mkdocs_en_config_structure() -> None:
    """Ensure mkdocs.en.yml uses docs_en and sets language to 'en'."""
    en_config_path = REPO_ROOT / "mkdocs.en.yml"
    data = load_mkdocs_yaml(en_config_path)

    assert data.get("docs_dir") == "docs_en"
    assert data.get("theme", {}).get("language") == "en"
    nav = data.get("nav", [])
    # Verify Home points to index.md in the staged docs_en
    first_item = nav[0]
    assert first_item.get("Home") == "index.md"


def test_build_docs_script_exists() -> None:
    """Ensure scripts/build_docs.py exists and is executable."""
    script_path = REPO_ROOT / "scripts" / "build_docs.py"
    assert script_path.exists()
    content = script_path.read_text(encoding="utf-8")
    assert "prepare_en_docs" in content
    assert "build_ja" in content
    assert "build_en" in content
