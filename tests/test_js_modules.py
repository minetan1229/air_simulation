"""The front-end module graph has to parse the way the browser parses it.

The console is loaded as an ES module graph, and a module that fails to
parse takes the *whole* application down with it: ``app.js`` imports the
gauges from ``telemetry.js``, so a single ``SyntaxError`` in that file left
the application without any JavaScript at all -- a static HTML shell with a
dead 3-D viewport, no telemetry and no panels.

The Python suite cannot see that class of failure on its own, so the module
graph is parsed here with the same goal the browser uses (ES module, not
script) and the relative import specifiers are resolved.

The check needs a Node.js runtime.  When none is available the test is
skipped rather than silently passed; the CI workflow runs the tool as an
explicit step, so the gap is closed there.
"""

from __future__ import annotations

import shutil

import pytest

from tools.check_js_modules import JS_DIR, check, module_files


def _node_available() -> bool:
    return bool(shutil.which("node") or shutil.which("nodejs"))


def test_every_front_end_module_parses_as_an_es_module():
    """No module may carry a syntax error the browser would reject.

    ``node --check`` on a ``.js`` file parses the *script* goal, whose scope
    rules are laxer than the module goal: a duplicate ``const`` in the same
    function body is accepted there and rejected by the browser.  The checker
    therefore parses a ``.mjs`` copy.
    """
    if not _node_available():
        pytest.skip("no Node.js runtime available")
    files = module_files(JS_DIR)
    assert files, "the front-end module directory must not be empty"
    syntax_errors, _missing = check(JS_DIR)
    assert not syntax_errors, "front-end modules do not parse:\n" + "\n".join(syntax_errors)


def test_every_relative_import_resolves():
    """An imported module that does not exist silently breaks the graph."""
    if not _node_available():
        pytest.skip("no Node.js runtime available")
    _syntax_errors, missing = check(JS_DIR)
    assert not missing, "unresolved front-end imports:\n" + "\n".join(missing)


def test_the_console_entry_point_is_a_module():
    """The console entry point has to be an ES module, not a classic script.

    A classic script shares its lexical scope with every other classic
    script, so a top-level ``const`` in one of them collides with the same
    name in the other.  The module goal keeps every file in its own scope.
    """
    from tools.check_js_modules import REPO_ROOT

    template = (REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'type="module"' in template, "the console entry point must be an ES module"
