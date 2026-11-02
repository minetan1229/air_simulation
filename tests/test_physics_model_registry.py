"""The published physics-reference registry has to match the implementation.

``/api/v1/meta/physics-references`` is the public list of the models the
simulator is built on.  A registry entry that names an equation the code
does not use is a false claim about the simulator: the registry used to
advertise the Viterna-Corrigan post-stall extrapolation while the
post-stall branch was (and still is) the Hoerner flat plate.

Every entry therefore declares

* ``implemented`` -- whether the model is the one the simulator runs,
* ``canonical_module`` -- where the single implementation lives,
* ``applicability`` -- the limits of the model,

and this module checks that the declarations are true of the code:

* every ``implemented`` entry points at a module and a symbol that exist,
* the entry that claims to be *not* implemented really is absent from the
  post-stall branch,
* the canonical module of the models that have one is the module the code
  actually calls.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The registry is a literal inside the route module; it is read from the
#: source so the test does not need a running server.
META_SOURCE = REPO_ROOT / "api" / "routes" / "meta.py"


def _registry_entries() -> list:
    """The reference dicts of the ``physics-references`` route."""
    source = META_SOURCE.read_text(encoding="utf-8")
    entries = []
    for match in re.finditer(r"\{\s*\"id\":\s*\"([a-z0-9_]+)\",(.*?)\n        \},", source, re.S):
        entry_id, body = match.group(1), match.group(2)
        entries.append((entry_id, body))
    assert entries, "no reference entry was found in the registry"
    return entries


def _flag(body: str, name: str) -> str | None:
    match = re.search(rf'"{name}":\s*(True|False|None)', body)
    return match.group(1) if match else None


def _canonical_module(body: str) -> str | None:
    match = re.search(r'"canonical_module":\s*"([^"]+)"', body)
    return match.group(1) if match else None


@pytest.mark.parametrize("entry_id,body", _registry_entries())
def test_every_entry_declares_its_implementation_status(entry_id: str, body: str) -> None:
    """A reference has to say whether it is the model the simulator runs."""
    assert _flag(body, "implemented") is not None, (
        f"{entry_id}: the registry entry does not declare 'implemented'"
    )
    assert '"applicability"' in body, (
        f"{entry_id}: the registry entry does not state the applicability limits"
    )


@pytest.mark.parametrize("entry_id,body", _registry_entries())
def test_an_implemented_entry_points_at_a_real_symbol(entry_id: str, body: str) -> None:
    """The canonical module of an implemented model has to exist."""
    if _flag(body, "implemented") != "True":
        return
    target = _canonical_module(body)
    assert target is not None, f"{entry_id}: no canonical module is declared"
    module_path, _, symbol = target.partition("::")
    relative = module_path.replace("/", ".").removesuffix(".py")
    module = importlib.import_module(relative)
    assert hasattr(module, symbol), (
        f"{entry_id}: the canonical module {relative} has no symbol {symbol}"
    )


def test_the_post_stall_model_is_the_hoerner_flat_plate() -> None:
    """The implemented post-stall branch is the Hoerner flat plate.

    Viterna-Corrigan is published in the registry as the *alternative* that
    is deliberately not used, because its geometric coefficients would have
    to be estimated for a paper airframe.
    """
    from physics import aero_kernel

    source = pathlib.Path(aero_kernel.__file__).read_text(encoding="utf-8")
    assert "hoerner" in source.lower(), "the post-stall branch lost its Hoerner reference"
    assert "viterna" not in source.lower(), (
        "the post-stall branch must not silently switch to the Viterna-Corrigan model"
    )

    entries = dict(_registry_entries())
    assert "viterna_corrigan_1982" in entries
    assert _flag(entries["viterna_corrigan_1982"], "implemented") == "False"
    assert "hoerner_1965" in entries
    assert _flag(entries["hoerner_1965"], "implemented") == "True"


def test_the_helmbold_slope_is_the_canonical_implementation() -> None:
    """The domain delegates to the kernel instead of re-deriving the slope."""
    import inspect

    from physics import aero_domain, aero_kernel

    source = inspect.getsource(aero_domain.helmbold_cl_alpha)
    assert "aero_kernel.helmbold_cl_alpha" in source, (
        "the domain re-implements the Helmbold slope instead of delegating"
    )
    for aspect, taper in ((0.5, 0.1), (2.5, 0.7), (4.0, 1.0)):
        assert aero_domain.helmbold_cl_alpha(aspect, taper) == pytest.approx(
            aero_kernel.helmbold_cl_alpha(aspect, taper), rel=0.0
        )


def test_the_scalar_stress_reduction_applies_the_dihedral_projection() -> None:
    """The reduction of the stress field has to carry the field's factors.

    The field projects the wing-root moment onto the body vertical by
    ``cos(dihedral)``.  A reduction that forgot the projection would report
    ``1 / cos(dihedral)`` more than the field -- 3.8e-3 at the default 5 deg
    -- and the GPU path and the 6-DoF path would disagree.
    """
    import math

    import numpy as np

    from physics import stress_domain
    from physics.config_schema import AircraftConfig, EnvironmentConfig

    cfg = AircraftConfig()
    env = EnvironmentConfig()
    derived = stress_domain.derived_properties(cfg)
    field = stress_domain.stress_field(
        cfg=cfg,
        dynamic_pressure_pa=30.0,
        load_factor=1.0,
        spanwise=3,
        chordwise=2,
        backend="numpy",
        gravity=abs(float(env.gravity)),
    )
    peak = float(
        np.asarray(
            stress_domain.peak_root_stress_pa(
                np,
                load_factor=np.asarray([1.0]),
                mass_kg=np.asarray([derived["mass_kg"]]),
                gravity=abs(float(env.gravity)),
                span_m=np.asarray([derived["ref_span_m"]]),
                taper_ratio=np.asarray([derived["taper_ratio"]]),
                root_chord_m=np.asarray(
                    [
                        stress_domain.root_chord_batch(
                            np,
                            derived["ref_area_m2"],
                            derived["ref_span_m"],
                            derived["taper_ratio"],
                        )
                    ]
                ),
                thickness_m=np.asarray(
                    [stress_domain._sheet_thickness(cfg.paper_gsm)]
                ),
                z_gain=np.asarray(
                    [
                        stress_domain.Z_SCALE_OFFSET
                        + stress_domain.Z_SCALE_GAIN
                        * stress_domain.effective_paper_stiffness(cfg)
                    ]
                ),
                dihedral_rad=np.asarray([math.radians(cfg.dihedral_deg)]),
                chord_scale=stress_domain.te_fold_chord_scale(
                    np, cfg.fold_trailing_deg
                ),
                tip_factor=np.asarray(
                    [abs(math.sin(math.radians(cfg.fold_tip_deg)))]
                ),
            )
        ).reshape(-1)[0]
    )
    assert peak == pytest.approx(float(field["max_stress_pa"]), rel=1e-12)
