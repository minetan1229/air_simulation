#!/usr/bin/env python3
"""Multi-language documentation build pipeline for MkDocs Material.

Builds Japanese documentation to `site/` and English documentation to `site/en/`
ensuring proper index mapping, language assets, and color palette toggling.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT_DIR / "docs"
DOCS_EN_STAGING = ROOT_DIR / "docs_en"


def prepare_en_docs() -> None:
    """Create staging directory docs_en with index.en.md mapped to index.md."""
    if DOCS_EN_STAGING.exists():
        shutil.rmtree(DOCS_EN_STAGING)
    shutil.copytree(DOCS_DIR, DOCS_EN_STAGING)

    # 1. Map index.en.md to index.md so it becomes site/en/index.html
    en_index = DOCS_DIR / "index.en.md"
    if en_index.exists():
        (DOCS_EN_STAGING / "index.md").write_text(en_index.read_text(encoding="utf-8"), encoding="utf-8")

    # 2. Check for any other *.en.md files and replace corresponding *.md
    for en_file in DOCS_DIR.rglob("*.en.md"):
        rel_path = en_file.relative_to(DOCS_DIR)
        # e.g., 'getting-started.en.md' -> 'getting-started.md'
        base_name = en_file.name[:-6] + ".md"  # remove .en.md and add .md
        target = DOCS_EN_STAGING / rel_path.parent / base_name
        target.write_text(en_file.read_text(encoding="utf-8"), encoding="utf-8")

    # 3. Remove all *.en.md files from staging (they were copied by copytree)
    for en_file in DOCS_EN_STAGING.rglob("*.en.md"):
        en_file.unlink()


def cleanup_en_docs() -> None:
    """Remove staging directory docs_en."""
    if DOCS_EN_STAGING.exists():
        shutil.rmtree(DOCS_EN_STAGING, ignore_errors=True)


def build_ja(site_dir: Path) -> None:
    """Build Japanese documentation into site_dir."""
    print(f"[*] Building Japanese documentation into {site_dir}...")
    env = os.environ.copy()
    env["NO_MKDOCS_2_WARNING"] = "1"
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--config-file", str(ROOT_DIR / "mkdocs.yml"), "--site-dir", str(site_dir)],
        check=True,
        cwd=str(ROOT_DIR),
        env=env,
    )
    print("[OK] Japanese documentation build complete.")


def build_en(site_dir: Path) -> None:
    """Build English documentation into site_dir/en."""
    en_target = site_dir / "en"
    print(f"[*] Building English documentation into {en_target}...")
    env = os.environ.copy()
    env["NO_MKDOCS_2_WARNING"] = "1"
    try:
        prepare_en_docs()
        subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "--config-file", str(ROOT_DIR / "mkdocs.en.yml"), "--site-dir", str(en_target)],
            check=True,
            cwd=str(ROOT_DIR),
            env=env,
        )
        print("[OK] English documentation build complete.")
    finally:
        cleanup_en_docs()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build multi-language documentation.")
    parser.add_argument(
        "--lang",
        choices=["ja", "en", "all"],
        default="all",
        help="Language to build (default: all)",
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=ROOT_DIR / "site",
        help="Root output directory for the built site (default: site/)",
    )
    args = parser.parse_args()

    site_dir = args.site_dir.resolve()

    if args.lang in ("ja", "all"):
        build_ja(site_dir)
    if args.lang in ("en", "all"):
        build_en(site_dir)

    print(f"[OK] All requested documentation builds finished successfully in {site_dir}.")


if __name__ == "__main__":
    main()
