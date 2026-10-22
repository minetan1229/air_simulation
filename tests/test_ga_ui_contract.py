"""G-3 contract: the published GA / optimiser payload and the UI that reads it.

The audit gate G-3 ("経路の誠実性") requires the provenance flags ``model`` /
``model_detail`` / ``approximate`` / ``physics_verification_eligible`` /
``limitations`` to be published **by the server** *and* **read by the UI**.
Until now only the server side was checked by hand, so a change on either side
could silently break the contract.

This module pins the contract with static checks that need neither a browser
nor Node.js:

* the keys ``static/js/optimizer.js`` reads from the optimiser result are
  extracted from the source with a regular expression,
* the keys the server publishes are extracted from the source with the
  :mod:`ast` module (no import of the server code, so the test cannot be
  influenced by an import side effect and stays a *static* check),
* the two sets are compared, and every difference has to be justified in one
  of the registries below.

The registries are the interesting part: they name the differences that are
known **today** (a payload key that no UI renders, a UI read that no payload
publishes).  They are tripwires.  When the UI is fixed, the corresponding
entry becomes stale and this module fails, which asks for the entry to be
removed -- a difference can never be introduced silently.

The server side is read only; this module never modifies it.  A difference
that needs a server change is reported to the parent agent instead.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]

OPTIMIZER_JS = ROOT / "static" / "js" / "optimizer.js"
GA_RESEARCH_JS = ROOT / "static" / "js" / "ga_research.js"
GA_DOMAIN_PY = ROOT / "physics" / "ga_domain.py"
GA_RESEARCH_ROUTE_PY = ROOT / "api" / "routes" / "ga_research.py"
OPTIMIZER_DOMAIN_PY = ROOT / "physics" / "optimizer_domain.py"
PAYLOAD_CONTRACT_PY = ROOT / "physics" / "payload_contract.py"
JOBS_ROUTE_PY = ROOT / "api" / "routes" / "jobs.py"

#: The provenance contract of ``docs/SPECIFICATION.md`` -- the same five keys
#: that :data:`physics.payload_contract.METADATA_KEYS` publishes for every
#: flight backend.
PROVENANCE_KEYS: Tuple[str, ...] = (
    "model",
    "model_detail",
    "approximate",
    "physics_verification_eligible",
    "limitations",
)

#: The keys ``static/js/optimizer.js`` reads from the optimiser job result.
#: The set is pinned so that a change of the UI (or of the payload) is a
#: visible test failure instead of a silent drift.
EXPECTED_OPTIMIZER_UI_READS: Set[str] = {
    "approximate",
    "backend",
    "best_objectives",
    "best_params",
    "best_verified",
    "evaluations",
    "job_id",
    "limitations",
    "model",
    "model_detail",
    "objectives",
    "pareto_front",
    "physics_models",
    "verified_candidates",
}

#: The keys ``static/js/ga_research.js`` reads from the **GA research result**
#: payload, i.e. the reads of the result rendering path
#: (``loadResult`` / ``_renderResult``).
EXPECTED_GA_RESULT_UI_READS: Set[str] = {
    "crease_pattern_svg",
    "fitness",
    "genome",
    "metric",
    "recipe",
    "stats",
    "step_meshes",
    "throw_params",
}

#: Keys the optimiser UI reads that the optimiser endpoint does **not**
#: publish at the top level.  ``model`` and ``model_detail`` are *flight*
#: level metadata (``physics/payload_contract.py::METADATA_KEYS``); the
#: optimiser payload carries the flight result of the leading candidate under
#: ``best_physics``, so the two rows of ``renderBest`` are currently never
#: filled.  The UI degrades gracefully (``renderFront`` falls back to
#: ``reduced_surrogate``), so this is a display gap and not a crash.
UI_READS_WITHOUT_OPTIMIZER_KEY: Dict[str, str] = {
    "model": (
        "The optimiser job payload (physics/optimizer_domain.py::RESULT_KEYS) has no "
        "top level 'model' key; the flight level model tag is nested under "
        "'best_physics'.  static/js/optimizer.js:432 falls back to "
        "'reduced_surrogate' when 'approximate' is set."
    ),
    "model_detail": (
        "The optimiser job payload has no top level 'model_detail' key; the flight "
        "level integrator name is nested under 'best_physics'.  "
        "static/js/optimizer.js:550 therefore skips the row."
    ),
}

#: Provenance keys a server payload publishes but no UI renders yet.  Known
#: gaps of G-3, one registry per panel.  An entry that stops being a gap
#: makes this module fail until the entry is removed.
UI_GAPS_BY_PANEL: Dict[str, Dict[str, str]] = {
    "static/js/optimizer.js": {
        "physics_verification_eligible": (
            "The optimiser job payload publishes 'physics_verification_eligible' "
            "(physics/optimizer_domain.py::RESULT_KEYS) but static/js/optimizer.js "
            "never reads it: the panel shows 'approximate' (近似モデル) only.  The "
            "two flags have a different meaning (a surrogate is an approximation "
            "and is never verification eligible), so the panel does not distinguish "
            "them yet."
        ),
    },
    "static/js/ga_research.js": {
        "model": (
            "The GA research payload publishes 'model' "
            "(physics/ga_domain.py::_result_payload and "
            "api/routes/ga_research.py) but the research panel renders only the "
            "fitness / metric / statistics and no provenance row."
        ),
        "approximate": (
            "The GA research payload publishes 'approximate' but the research "
            "panel does not show whether the published flight was an approximation."
        ),
        "physics_verification_eligible": (
            "The GA research payload publishes 'physics_verification_eligible' but "
            "the research panel does not show whether the result may be used for a "
            "physics verification."
        ),
        "limitations": (
            "The GA research payload publishes 'limitations' but the research panel "
            "does not list them."
        ),
    },
}


# ---------------------------------------------------------------------------
# Static extraction helpers
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    return ast.parse(_read(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _dict_keys(node: ast.Dict) -> Set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _returned_dicts(func: ast.FunctionDef | ast.AsyncFunctionDef) -> List[ast.Dict]:
    return [
        node.value
        for node in ast.walk(func)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    ]


def _jsonify_dicts(func: ast.FunctionDef | ast.AsyncFunctionDef) -> List[ast.Dict]:
    """Every ``jsonify({...})`` dictionary of the function."""
    found: List[ast.Dict] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "jsonify":
            continue
        found.extend(arg for arg in node.args if isinstance(arg, ast.Dict))
    return found


def _assigned_tuple(tree: ast.Module, name: str) -> Tuple[str, ...]:
    """The string constants of a module level tuple constant."""
    for node in ast.walk(tree):
        target = None
        value = None
        if isinstance(node, ast.Assign):
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if getattr(target, "id", None) != name or not isinstance(value, ast.Tuple):
            continue
        return tuple(
            element.value
            for element in value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
    raise AssertionError(f"{name} not found")


def _js_reads(path: Path, receiver: str) -> Set[str]:
    """The member keys the JavaScript file reads from ``receiver``."""
    pattern = re.compile(rf"\b{re.escape(receiver)}\s*\??\.\s*([A-Za-z_$][\w$]*)")
    return set(pattern.findall(_read(path)))


def _js_function_body(path: Path, signature: str) -> str:
    """The body of the JavaScript method whose definition starts with ``signature``.

    The definition line is located with a regular expression (a call site such
    as ``this.loadResult()`` is not a definition), the body is delimited by
    the matching braces.
    """
    text = _read(path)
    match = re.search(rf"(?m)^\s*(?:async\s+)?{re.escape(signature)}\s*\(", text)
    if match is None:
        raise AssertionError(f"{signature} is not defined in {path.name}")
    start = text.find("{", match.end())
    if start < 0:
        raise AssertionError(f"{signature} has no body in {path.name}")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"the body of {signature} in {path.name} is not closed")


def _none_branch_dict(func: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Dict:
    """The dictionary returned by the ``if <param> is None:`` branch."""
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        if ast.unparse(node.test).replace(" ", "") != "bestisNone":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                return inner.value
    raise AssertionError("the 'best is None' branch of _result_payload was not found")


def _dict_value(node: ast.Dict, key: str):
    for candidate, value in zip(node.keys, node.values):
        if isinstance(candidate, ast.Constant) and candidate.value == key:
            return ast.literal_eval(value)
    raise AssertionError(f"key {key!r} not found")


def _subscript_assignments(func: ast.FunctionDef | ast.AsyncFunctionDef) -> Set[str]:
    """The string keys of ``payload["key"] = ...`` assignments of the function."""
    found: Set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            index = target.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                found.add(index.value)
    return found


# ---------------------------------------------------------------------------
# The extracted contract
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def optimizer_ui_reads() -> Set[str]:
    return _js_reads(OPTIMIZER_JS, "result")


@pytest.fixture(scope="module")
def ga_result_ui_reads() -> Set[str]:
    """The GA result payload reads of the research panel."""
    reads: Set[str] = set()
    for signature in ("loadResult", "_renderResult"):
        body = _js_function_body(GA_RESEARCH_JS, signature)
        pattern = re.compile(r"\bpayload\s*\??\.\s*([A-Za-z_$][\w$]*)")
        reads |= set(pattern.findall(body))
    return reads


@pytest.fixture(scope="module")
def optimizer_payload_keys() -> Set[str]:
    """The key set of the optimiser job payload (``RESULT_KEYS``)."""
    return set(_assigned_tuple(_parse(OPTIMIZER_DOMAIN_PY), "RESULT_KEYS"))


@pytest.fixture(scope="module")
def optimizer_endpoint_keys(optimizer_payload_keys: Set[str]) -> Set[str]:
    """Every key the optimiser *endpoint* publishes.

    ``physics/optimizer_domain.py::RESULT_KEYS`` is the exact key set of the
    domain payload.  The HTTP layer adds the job id
    (``api/routes/jobs.py``: ``result["job_id"] = job.job_id``) and the
    fallback payload of ``optimizer_result`` carries ``job_id`` as well.
    """
    tree = _parse(JOBS_ROUTE_PY)
    keys = set(optimizer_payload_keys)
    run = _function(tree, "optimizer_run")
    keys |= _subscript_assignments(run)
    result = _function(tree, "optimizer_result")
    for node in ast.walk(result):
        if isinstance(node, ast.Dict):
            keys |= _dict_keys(node)
    return keys


@pytest.fixture(scope="module")
def ga_payload_keys() -> Set[str]:
    """Every key of ``physics/ga_domain.py::_result_payload`` (all branches)."""
    payload = _function(_parse(GA_DOMAIN_PY), "_result_payload")
    keys: Set[str] = set()
    for branch in _returned_dicts(payload):
        keys |= _dict_keys(branch)
    return keys


@pytest.fixture(scope="module")
def ga_route_keys() -> Set[str]:
    """Every key of the ``/api/ga/research/result/<run_id>`` response."""
    route = _function(_parse(GA_RESEARCH_ROUTE_PY), "ga_research_result")
    keys: Set[str] = set()
    for branch in _jsonify_dicts(route):
        keys |= _dict_keys(branch)
    return keys


@pytest.fixture(scope="module")
def flight_metadata_keys() -> Set[str]:
    return set(_assigned_tuple(_parse(PAYLOAD_CONTRACT_PY), "METADATA_KEYS"))


# ---------------------------------------------------------------------------
# 1. The extraction itself is pinned
# ---------------------------------------------------------------------------
def test_optimizer_ui_reads_are_pinned(optimizer_ui_reads: Set[str]) -> None:
    """The optimiser panel reads exactly the keys the contract expects."""
    assert optimizer_ui_reads == EXPECTED_OPTIMIZER_UI_READS


def test_ga_result_ui_reads_are_pinned(ga_result_ui_reads: Set[str]) -> None:
    """The research panel reads exactly the keys of the GA result payload."""
    assert ga_result_ui_reads == EXPECTED_GA_RESULT_UI_READS


# ---------------------------------------------------------------------------
# 2. The optimiser job payload publishes what the optimiser panel reads
# ---------------------------------------------------------------------------
def test_optimizer_endpoint_publishes_the_ui_reads(
    optimizer_ui_reads: Set[str],
    optimizer_endpoint_keys: Set[str],
) -> None:
    """Every optimiser UI read is a published key, except the known gaps."""
    missing = optimizer_ui_reads - optimizer_endpoint_keys
    assert missing == set(UI_READS_WITHOUT_OPTIMIZER_KEY), (
        "the optimiser UI reads keys the optimiser endpoint does not publish: "
        f"{sorted(missing)}; add them to UI_READS_WITHOUT_OPTIMIZER_KEY with a "
        "reason or publish them on the server side"
    )


def test_optimizer_payload_publishes_the_required_reads(
    optimizer_payload_keys: Set[str],
) -> None:
    """The keys the panel cannot work without are all published."""
    required = {
        "objectives",
        "best_params",
        "best_objectives",
        "pareto_front",
        "evaluations",
        "backend",
        "approximate",
        "physics_models",
        "limitations",
        "best_verified",
        "verified_candidates",
    }
    assert required <= optimizer_payload_keys


def test_the_endpoint_publishes_the_job_id(optimizer_endpoint_keys: Set[str]) -> None:
    """The job id the panel compares against (``this.result?.job_id``) exists."""
    assert "job_id" in optimizer_endpoint_keys


# ---------------------------------------------------------------------------
# 3. The GA payload publishes the G-3 provenance keys
# ---------------------------------------------------------------------------
def test_ga_payload_publishes_the_provenance_keys(ga_payload_keys: Set[str]) -> None:
    """``physics/ga_domain.py::_result_payload`` publishes the provenance keys."""
    assert {"model", "approximate", "physics_verification_eligible", "limitations"} <= ga_payload_keys


def test_ga_route_publishes_the_provenance_keys(ga_route_keys: Set[str]) -> None:
    """The GA research route publishes the provenance keys."""
    assert {"model", "approximate", "physics_verification_eligible", "limitations"} <= ga_route_keys


def test_ga_payload_and_route_agree_on_the_provenance_keys(
    ga_payload_keys: Set[str],
    ga_route_keys: Set[str],
) -> None:
    """The engine payload and the route publish the same provenance keys."""
    provenance = set(PROVENANCE_KEYS)
    assert (ga_payload_keys & provenance) == (ga_route_keys & provenance)


def test_flight_contract_is_the_provenance_reference(flight_metadata_keys: Set[str]) -> None:
    """The flight payload contract is the reference for the provenance keys."""
    assert set(PROVENANCE_KEYS) == flight_metadata_keys


# ---------------------------------------------------------------------------
# 4. The cross check the audit asks for: UI reads vs. GA payload
# ---------------------------------------------------------------------------
def test_ga_result_ui_reads_are_published(
    ga_result_ui_reads: Set[str],
    ga_payload_keys: Set[str],
    ga_route_keys: Set[str],
) -> None:
    """Every key the research panel reads is published by the GA payload."""
    assert not (ga_result_ui_reads - ga_payload_keys), (
        "the research panel reads keys the GA payload does not publish: "
        f"{sorted(ga_result_ui_reads - ga_payload_keys)}"
    )
    assert not (ga_result_ui_reads - ga_route_keys), (
        "the research panel reads keys the GA route does not publish: "
        f"{sorted(ga_result_ui_reads - ga_route_keys)}"
    )


def test_ui_reads_cross_checked_against_the_ga_payload(
    optimizer_ui_reads: Set[str],
    ga_payload_keys: Set[str],
) -> None:
    """The UI reads that the GA payload does not publish are known and named.

    The panel of the optimiser consumes the *optimiser* endpoint, not the GA
    research endpoint, so the two payloads publish different keys.  Every
    difference is listed here so that a new one is a visible failure.
    """
    ui_reads_without_ga_key = optimizer_ui_reads - ga_payload_keys
    assert ui_reads_without_ga_key == {
        "backend",
        "best_objectives",
        "best_params",
        "best_verified",
        "evaluations",
        "job_id",
        "model_detail",
        "objectives",
        "pareto_front",
        "physics_models",
        "verified_candidates",
    }
    # The provenance keys the GA payload publishes and the optimiser panel
    # does not read are the registered gaps of section 5.
    provenance = {"model", "approximate", "physics_verification_eligible", "limitations"}
    assert (provenance & ga_payload_keys) - optimizer_ui_reads == {
        "physics_verification_eligible"
    }


# ---------------------------------------------------------------------------
# 5. The semantics of the two flags
# ---------------------------------------------------------------------------
def test_reduced_path_is_approximate_and_never_verification_eligible() -> None:
    """A reduced (surrogate) path is an approximation, never verifiable."""
    from physics.ga_domain import REDUCED_MODEL_TAG, ResearchRun

    provenance = ResearchRun._provenance(  # noqa: SLF001 - the contract under test
        object.__new__(ResearchRun),
        {
            "model": REDUCED_MODEL_TAG,
            "approximate": False,
            "physics_verification_eligible": True,
            "limitations": [],
        },
    )
    assert provenance["approximate"] is True
    assert provenance["physics_verification_eligible"] is False


def test_unpublished_candidate_is_approximate_and_never_verification_eligible() -> None:
    """A run without a publishable candidate publishes the conservative flags."""
    payload = _function(_parse(GA_DOMAIN_PY), "_result_payload")
    branch = _none_branch_dict(payload)
    assert _dict_value(branch, "approximate") is True
    assert _dict_value(branch, "physics_verification_eligible") is False


def test_the_two_flags_are_published_independently(
    ga_payload_keys: Set[str],
    ga_route_keys: Set[str],
) -> None:
    """``approximate`` and ``physics_verification_eligible`` are two keys."""
    assert "approximate" in ga_payload_keys
    assert "physics_verification_eligible" in ga_payload_keys
    assert "approximate" in ga_route_keys
    assert "physics_verification_eligible" in ga_route_keys


def test_ui_distinguishes_approximation_from_verification_eligibility(
    optimizer_ui_reads: Set[str],
) -> None:
    """The UI reads the approximation flag and names the missing one.

    ``approximate`` and ``physics_verification_eligible`` carry different
    information: a reduced path is an approximation *and* is not eligible for
    a physics verification.  The UI has to be able to tell the two apart, so
    the flag that is not read has to be a registered gap -- it may never
    disappear silently.
    """
    assert "approximate" in optimizer_ui_reads

    optimizer_gap = UI_GAPS_BY_PANEL["static/js/optimizer.js"].get("physics_verification_eligible")
    assert optimizer_gap is not None
    assert "physics_verification_eligible" not in optimizer_ui_reads, (
        "the optimiser panel now reads 'physics_verification_eligible'; remove the "
        "entry from UI_GAPS_BY_PANEL"
    )


def test_the_approximation_flag_has_its_own_label() -> None:
    """The approximation flag is rendered with its own label, not as the model."""
    source = _read(OPTIMIZER_JS)
    assert '"近似モデル"' in source, (
        "the optimiser panel lost the dedicated label of the 'approximate' flag"
    )


# ---------------------------------------------------------------------------
# 6. The gaps are the only gaps
# ---------------------------------------------------------------------------
def test_the_registered_gaps_are_the_only_gaps(
    optimizer_ui_reads: Set[str],
    ga_result_ui_reads: Set[str],
    optimizer_payload_keys: Set[str],
    ga_payload_keys: Set[str],
) -> None:
    """No payload publishes a provenance key that the UI ignores silently."""
    panels = (
        ("static/js/optimizer.js", optimizer_ui_reads, optimizer_payload_keys),
        ("static/js/ga_research.js", ga_result_ui_reads, ga_payload_keys),
    )
    for panel, reads, published in panels:
        provenance = set(PROVENANCE_KEYS)
        missing = (published & provenance) - reads
        registered = set(UI_GAPS_BY_PANEL[panel])
        assert missing == registered, (
            f"{panel}: the provenance keys that are published but not read are "
            f"{sorted(missing)}; the registry lists {sorted(registered)}.  Update "
            "UI_GAPS_BY_PANEL so that the difference stays explicit."
        )


def test_every_registered_gap_is_really_a_gap(
    optimizer_ui_reads: Set[str],
    ga_result_ui_reads: Set[str],
    optimizer_payload_keys: Set[str],
    ga_payload_keys: Set[str],
) -> None:
    """A registered gap that is not a gap anymore fails the suite."""
    panels = (
        ("static/js/optimizer.js", optimizer_ui_reads, optimizer_payload_keys),
        ("static/js/ga_research.js", ga_result_ui_reads, ga_payload_keys),
    )
    for panel, reads, published in panels:
        provenance = set(PROVENANCE_KEYS)
        for key in UI_GAPS_BY_PANEL[panel]:
            assert key in published, (
                f"{panel}: the registered gap {key!r} is not published by the "
                "server payload at all; remove the entry"
            )
            assert key not in reads, (
                f"{panel}: the registered gap {key!r} is read by the UI now; "
                "remove the entry"
            )


# ---------------------------------------------------------------------------
# 7. The optional JavaScript syntax check
# ---------------------------------------------------------------------------
def test_optimizer_javascript_parses() -> None:
    """``node --check`` on the panel (skipped when Node.js is not installed)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript syntax check was not run")
    completed = subprocess.run(
        [node, "--check", str(OPTIMIZER_JS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
