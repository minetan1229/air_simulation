import re
from pathlib import Path
import pytest

try:
    import yaml
except ImportError:
    yaml = None

JP_REGEX = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]")

JA_DOCS = [
    "docs/index.md",
    "docs/getting-started.md",
    "docs/usage.md",
    "docs/troubleshooting.md",
    "docs/SPECIFICATION.md",
    "docs/PHYSICS_MODELS.md",
    "docs/PHYSICS_ALGORITHM.md",
    "docs/VALIDATION.md",
    "docs/architecture.md",
    "docs/PHYSICS_ROADMAP.md",
    "docs/api.md",
    "docs/development.md",
    "docs/CFD_ROADMAP.md",
    "docs/discord-bridge.md",
    "README.md",
]

EN_DOCS = [
    "docs/index.en.md",
    "docs/getting-started.en.md",
    "docs/usage.en.md",
    "docs/troubleshooting.en.md",
    "docs/SPECIFICATION.en.md",
    "docs/VALIDATION.en.md",
    "docs/architecture.en.md",
    "docs/PHYSICS_ROADMAP.en.md",
    "docs/api.en.md",
    "docs/development.en.md",
    "docs/CFD_ROADMAP.en.md",
    "docs/discord-bridge.en.md",
    "README.en.md",
]


def test_japanese_documentation_contains_japanese():
    root = Path(__file__).resolve().parent.parent
    for rel_path in JA_DOCS:
        file_path = root / rel_path
        assert file_path.exists(), f"Japanese doc {rel_path} does not exist."
        content = file_path.read_text(encoding="utf-8")
        jp_chars = JP_REGEX.findall(content)
        assert len(jp_chars) >= 20, (
            f"Doc {rel_path} appears not to be in Japanese: only {len(jp_chars)} JP chars found."
        )


def test_english_documentation_files_exist():
    root = Path(__file__).resolve().parent.parent
    for rel_path in EN_DOCS:
        file_path = root / rel_path
        assert file_path.exists(), f"English doc {rel_path} does not exist."
        content = file_path.read_text(encoding="utf-8")
        assert len(content.strip()) > 50, f"English doc {rel_path} is empty."


@pytest.mark.skipif(yaml is None, reason="PyYAML not installed")
def test_mkdocs_nav_targets_exist():
    root = Path(__file__).resolve().parent.parent

    for config_name in ["mkdocs.yml", "mkdocs.en.yml"]:
        config_path = root / config_name
        assert config_path.exists(), f"{config_name} does not exist."
        class NoTagLoader(yaml.SafeLoader):
            pass

        NoTagLoader.add_multi_constructor("tag:yaml.org,2002:python/", lambda loader, suffix, node: None)

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.load(f, Loader=NoTagLoader)

        docs_dir = root / cfg.get("docs_dir", "docs")

        def check_nav(items):
            for item in items:
                if isinstance(item, dict):
                    for _, val in item.items():
                        if isinstance(val, str):
                            if not val.startswith("http://") and not val.startswith("https://"):
                                if config_name == "mkdocs.en.yml" and not docs_dir.exists():
                                    # docs_en is dynamically staged from docs/ during build
                                    source_dir = root / "docs"
                                    if val == "index.md":
                                        target = source_dir / "index.en.md"
                                    else:
                                        target = source_dir / val
                                else:
                                    target = docs_dir / val
                                assert target.exists(), f"File {val} from {config_name} nav does not exist at {target}."
                        elif isinstance(val, list):
                            check_nav(val)

        check_nav(cfg.get("nav", []))
