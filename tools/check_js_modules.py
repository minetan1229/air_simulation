"""Parse every front-end module the way the browser parses it.

The browser loads the console as an ES module graph
(``<script type="module" src="static/js/app.js">``).  A module that fails to
parse kills the whole graph: ``app.js`` imports the gauges from
``telemetry.js``, so a single ``SyntaxError`` in ``telemetry.js`` leaves the
application with *no* JavaScript at all -- a static HTML shell with a dead
3-D viewport, no telemetry and no panels.  Nothing in the Python test suite
noticed, because the failure is entirely in the browser.

``node --check`` on a ``.js`` file parses the file as a **script**, whose
scope rules differ from the module goal: a duplicate ``const`` in the same
function body is accepted there and rejected by the browser.  This tool
therefore copies every module to a ``.mjs`` file -- the module goal -- and
parses that copy, which reproduces the browser's verdict exactly.

The tool also resolves the static ``import``/``export ... from`` specifiers of
every module, so a module that is imported but missing is reported as well.

Usage::

    python tools/check_js_modules.py

Exit code ``0`` when every module parses and every relative import resolves,
``1`` otherwise.  When no Node.js runtime is available the tool reports that
fact and exits with ``2``, so a caller can tell "not checked" from "broken".
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
JS_DIR = REPO_ROOT / "static" / "js"

#: ``import x from "./y.js"`` / ``export * from "./y.js"`` -- the specifier is
#: a relative path, so it has to resolve inside ``static/js``.
_IMPORT_RE = re.compile(
    r"""(?:^|\s)(?:import|export)\b[^;\n]*?from\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)

#: ``import "./y.js"`` -- a side-effect import has no ``from`` clause.
_SIDE_EFFECT_IMPORT_RE = re.compile(
    r"""(?:^|\s)import\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def _node_executable() -> Optional[str]:
    """The Node.js runtime used to parse the modules, if there is one."""
    return shutil.which("node") or shutil.which("nodejs")


def module_files(js_dir: Path = JS_DIR) -> List[Path]:
    """Every front-end module, in a stable order."""
    if not js_dir.is_dir():
        return []
    return sorted(js_dir.glob("*.js"))


def _specifiers(source: str) -> List[str]:
    """Every static module specifier of a source text."""
    found = _IMPORT_RE.findall(source)
    found.extend(_SIDE_EFFECT_IMPORT_RE.findall(source))
    return found


def _check_syntax(node: str, path: Path, workdir: Path) -> Optional[str]:
    """Parse one module with the module goal; return the error text."""
    target = workdir / (path.stem + ".mjs")
    target.write_bytes(path.read_bytes())
    completed = subprocess.run(
        [node, "--check", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode == 0:
        return None
    message = (completed.stderr or completed.stdout or "").strip()
    # Report the original path, not the temporary copy.
    return message.replace(str(target), str(path))


def _missing_imports(path: Path, source: str) -> List[str]:
    """Relative specifiers of a module that do not resolve to a file."""
    missing: List[str] = []
    for specifier in _specifiers(source):
        if not specifier.startswith("."):
            continue
        target = (path.parent / specifier).resolve()
        if not target.exists():
            missing.append(specifier)
    return missing


def check(js_dir: Path = JS_DIR) -> Tuple[List[str], List[str]]:
    """Return ``(syntax_errors, missing_imports)`` for the module graph."""
    node = _node_executable()
    syntax_errors: List[str] = []
    missing_imports: List[str] = []
    files = module_files(js_dir)
    if node is None:
        return ([], []) if not files else ([], [])
    with tempfile.TemporaryDirectory(prefix="js-modules-") as tmp:
        workdir = Path(tmp)
        for path in files:
            source = path.read_text(encoding="utf-8", errors="replace")
            error = _check_syntax(node, path, workdir)
            if error is not None:
                syntax_errors.append(error)
            for specifier in _missing_imports(path, source):
                missing_imports.append(f"{_display_path(path)}: {specifier}")
    return syntax_errors, missing_imports


def _display_path(path: Path) -> str:
    """The path relative to the repository, or the absolute one."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    node = _node_executable()
    files = module_files()
    if not files:
        print("no front-end module found in static/js")
        return 0
    if node is None:
        print(
            "no Node.js runtime found: the front-end modules were NOT parsed. "
            "Install Node.js (>= 18) to run this check."
        )
        return 2
    syntax_errors, missing_imports = check()
    for error in syntax_errors:
        print(error)
    for missing in missing_imports:
        print(f"unresolved import in {missing}")
    if syntax_errors or missing_imports:
        print(
            f"FAILED: {len(syntax_errors)} module(s) do not parse, "
            f"{len(missing_imports)} unresolved import(s)"
        )
        return 1
    print(f"OK: {len(files)} front-end modules parse and every import resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
