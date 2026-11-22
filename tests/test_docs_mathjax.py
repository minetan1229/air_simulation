from pathlib import Path
import re


def test_mathjax_configuration_exists():
    root = Path(__file__).resolve().parent.parent
    js_path = root / "docs" / "javascripts" / "mathjax.js"
    assert js_path.exists(), "docs/javascripts/mathjax.js must exist for Arithmatex MathJax rendering."

    js_content = js_path.read_text(encoding="utf-8")
    assert "window.MathJax" in js_content
    assert "inlineMath" in js_content
    assert "displayMath" in js_content
    assert "processHtmlClass" in js_content
    assert "arithmatex" in js_content
    assert "document$.subscribe" in js_content


def test_mkdocs_mathjax_scripts_registered():
    root = Path(__file__).resolve().parent.parent
    for config_file in ["mkdocs.yml", "mkdocs.en.yml"]:
        config_path = root / config_file
        assert config_path.exists(), f"{config_file} must exist."

        text = config_path.read_text(encoding="utf-8")

        # Verify pymdownx.arithmatex with generic: true
        assert "pymdownx.arithmatex" in text, f"pymdownx.arithmatex missing in {config_file}"
        assert re.search(r"pymdownx\.arithmatex:\s*\n\s*generic:\s*true", text), (
            f"pymdownx.arithmatex must have generic: true in {config_file}"
        )

        # Verify MathJax extra_javascript entries
        assert "javascripts/mathjax.js" in text, f"javascripts/mathjax.js missing in {config_file}"
        assert "tex-mml-chtml.js" in text, f"MathJax CDN script missing in {config_file}"
