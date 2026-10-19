"""Meta and system status routes."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from flask import Blueprint, jsonify, request

from physics.config_schema import (
    AIRCRAFT_SCHEMA,
    ALGORITHM_NAMES,
    DESIGN_VARIABLE_CATALOGUE,
    ENVIRONMENT_SCHEMA,
    LAUNCH_SCHEMA,
    OBJECTIVE_DIRECTIONS,
    as_dict,
    utc_now_iso,
)
from ..core import ServiceUnavailable, api_error, domain

log = logging.getLogger("paper_airplane")

meta_bp = Blueprint("meta", __name__)


@meta_bp.get("/api/v1/meta/schemas")
@meta_bp.get("/api/v1/meta/schema")
def meta_schemas():
    """Single source of truth for the UI sliders, objective list and ranges."""
    return jsonify(
        {
            "aircraft": AIRCRAFT_SCHEMA,
            "environment": ENVIRONMENT_SCHEMA,
            "launch": LAUNCH_SCHEMA,
            "objectives": [
                {"name": name, "direction": direction}
                for name, direction in OBJECTIVE_DIRECTIONS.items()
            ],
            "algorithms": list(ALGORITHM_NAMES),
            "design_variables": [as_dict(v) for v in DESIGN_VARIABLE_CATALOGUE],
            "design_variable_catalogue": [as_dict(v) for v in DESIGN_VARIABLE_CATALOGUE],
            "backend": "numpy",
        }
    )


@meta_bp.get("/api/v1/meta/physics-references")
def meta_physics_references():
    """Structured registry of academic citations, governing equations, and AeroSandbox references."""
    references = [
        {
            "id": "helmbold_1942",
            "domain": "aerodynamics",
            "model": "Helmbold Triangular Wing Lift Slope",
            "equation": "C_Lalpha = 2*pi*AR / (2 + sqrt(AR^2*(1 + lambda) + 4*AR/pi))",
            "authors": "Helmbold, H. B.",
            "year": 1942,
            "title": "Der unverwundene Dreiecksflügel als Trägerfläche höchsten Auftriebs",
            "publication": "Jahrbuch 1942 der deutschen Luftfahrtforschung, I, 111-113",
            "aerosandbox_reference": "aerosandbox.aerodynamics.buildup (AeroBuildup low-AR wing corrections)",
        },
        {
            "id": "polhamus_1966",
            "domain": "aerodynamics",
            "model": "Polhamus Suction Analogy Leading-Edge Vortex Lift",
            "equation": "C_L = K_p * sin(alpha) * cos^2(alpha) + K_v * sin^2(alpha) * cos(alpha)",
            "authors": "Polhamus, E. C.",
            "year": 1966,
            "title": "A Concept of the Vortex Lift of Sharp-Edge Delta Wings Based on a Leading-Edge-Suction Analogy",
            "publication": "NASA Technical Note NASA TN D-3767",
            "url": "https://ntrs.nasa.gov/citations/19670003058",
            "aerosandbox_reference": "aerosandbox.aerodynamics.buildup (slender delta wing vortex lift models)",
        },
        {
            "id": "viterna_corrigan_1982",
            "domain": "aerodynamics",
            "model": "Viterna-Corrigan Post-Stall Extrapolation",
            "equation": "C_L = A_1*sin(2*alpha) + A_2*cos^2(alpha)/sin(alpha); C_D = B_1*sin(alpha) + B_2*cos(alpha)",
            "authors": "Viterna, L. A., and Corrigan, R. D.",
            "year": 1982,
            "title": "Fixed Pitch Rotor Performance of Large Horizontal Axis Wind Turbines",
            "publication": "NASA Lewis Research Center, NASA TM-83021 / DOE/NASA/20320-35",
            "url": "https://ntrs.nasa.gov/citations/19830006272",
            "aerosandbox_reference": "aerosandbox.aerodynamics.airfoil (Airfoil post-stall Viterna method)",
        },
        {
            "id": "hoerner_1965",
            "domain": "aerodynamics",
            "model": "Hoerner Flat Plate Separated Crossflow and Base Drag",
            "equation": "C_N = 2.0 * sin(alpha) * cos^2(alpha)",
            "authors": "Hoerner, S. F.",
            "year": 1965,
            "title": "Fluid-Dynamic Drag: Practical Information on Aerodynamic Drag and Hydrodynamic Resistance",
            "publication": "Hoerner Fluid Dynamics, Brick Town, NJ",
            "aerosandbox_reference": "aerosandbox.aerodynamics.buildup (crossflow drag and flat-plate normal force)",
        },
        {
            "id": "wieselsberger_1922",
            "domain": "aerodynamics",
            "model": "Wieselsberger Wing Ground Effect Induced Drag Reduction",
            "equation": "phi_GE = 16*(h/b)^2 / (1 + 16*(h/b)^2)",
            "authors": "Wieselsberger, C.",
            "year": 1922,
            "title": "Wing Resistance Near the Ground",
            "publication": "ZFM, Vol. 12, pp. 145-147; translated in NACA TM 77 (1922)",
            "url": "https://ntrs.nasa.gov/citations/19930090888",
            "aerosandbox_reference": "aerosandbox.aerodynamics.buildup (ground effect reduction factor)",
        },
        {
            "id": "us_standard_atmosphere_1976",
            "domain": "atmosphere",
            "model": "U.S. Standard Atmosphere 1976 Geopotential Barometric Lapse Rate",
            "equation": "T(h) = T0 - L*h, p(h) = p0*(T/T0)^(g/(L*R)), rho = p / (R*T)",
            "authors": "NOAA, NASA, and USAF",
            "year": 1976,
            "title": "U.S. Standard Atmosphere, 1976",
            "publication": "NOAA-S/T 76-1562, NASA-TM-X-74335, Washington, D.C.",
            "url": "https://ntrs.nasa.gov/citations/19770009539",
            "aerosandbox_reference": "aerosandbox.atmosphere.Atmosphere",
        },
        {
            "id": "sutherland_1893",
            "domain": "atmosphere",
            "model": "Sutherland Temperature-Dependent Dynamic Viscosity",
            "equation": "mu = mu0 * (T/T0)^1.5 * (T0 + S) / (T + S)",
            "authors": "Sutherland, W.",
            "year": 1893,
            "title": "The Viscosity of Gases and Molecular Force",
            "publication": "Philosophical Magazine, 5(36), pp. 507-531",
            "doi": "10.1080/14786449308620508",
            "aerosandbox_reference": "aerosandbox.atmosphere.Atmosphere (dynamic_viscosity calculation)",
        },
        {
            "id": "prandtl_glauert_1928",
            "domain": "aerodynamics",
            "model": "Prandtl-Glauert Subsonic Compressibility Correction",
            "equation": "beta = sqrt(1 - M^2), C_p = C_p0 / beta, dCL/dalpha = (dCL/dalpha)_0 / beta",
            "authors": "Glauert, H.",
            "year": 1928,
            "title": "The Effect of Compressibility on the Lift of an Aerofoil",
            "publication": "Proceedings of the Royal Society of London, Series A, 118(779), pp. 113-119",
            "doi": "10.1098/rspa.1928.0039",
            "aerosandbox_reference": "aerosandbox.aerodynamics.buildup (compressibility scaling)",
        },
    ]
    return jsonify({
        "status": "ok",
        "count": len(references),
        "references": references,
        "timestamp": utc_now_iso(),
    })


@meta_bp.get("/api/v1/meta/test-fault")
def meta_test_fault():
    """Diagnostic fault endpoint for verifying unhandled exception handling."""
    if not (os.environ.get("FLASK_ENV") == "development" or os.environ.get("TESTING") == "1"):
        return api_error("test fault endpoint is disabled outside test environments", 403)
    raise RuntimeError("Simulated internal secret crash at /sensitive/source/path.py:99")




@meta_bp.get("/api/v1/meta/health")
def meta_health():
    domains = {}
    for name in (
        "aero_domain",
        "atmosphere_domain",
        "wind_domain",
        "stress_domain",
        "mesh_domain",
        "flight_sim",
        "parallel_engine",
        "optimizer_domain",
        "job_manager",
        "storage_domain",
    ):
        try:
            domain(name)
            domains[name] = "ok"
        except ServiceUnavailable as exc:
            domains[name] = str(exc)
    gpu_info = domain("parallel_engine").get_gpu_info()
    return jsonify({
        "status": "ok",
        "domains": domains,
        "gpu": gpu_info,
        "timestamp": utc_now_iso(),
    })


@meta_bp.get("/api/v1/system/gpu")
def system_gpu():
    parallel = domain("parallel_engine")
    return jsonify(parallel.get_gpu_info())


_LAST_BROWSER_LAUNCH: float = 0.0
_BROWSER_LAUNCH_LOCK = threading.Lock()


@meta_bp.post("/api/v1/system/force-gpu-render")
def force_gpu_render():
    """Configure Windows DirectX UserGpuPreferences to force discrete high-performance GPU (RTX 4060)
    for browsers, and optionally launch or re-launch browser with high-performance flags.
    Restricted to loopback address and explicit environment variable.
    """
    global _LAST_BROWSER_LAUNCH
    if request.remote_addr not in ("127.0.0.1", "::1", "localhost"):
        return api_error("forbidden: endpoint restricted to local loopback", 403)
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if origin and not any(origin.startswith(h) for h in ("http://127.0.0.1:", "http://localhost:", "https://127.0.0.1:", "https://localhost:")):
        return api_error("forbidden: cross-origin request disallowed", 403)
    if not os.environ.get("PAPER_ALLOW_SYSTEM_CONFIG") and os.environ.get("ENV") == "production":
        return api_error("system configuration endpoint is disabled in production", 403)

    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "configure")

    registered = []
    error_msg = None

    try:
        import winreg
        create_key = getattr(winreg, "CreateKey")
        hkey_cu = getattr(winreg, "HKEY_CURRENT_USER")
        set_val = getattr(winreg, "SetValueEx")
        reg_sz = getattr(winreg, "REG_SZ")
        close_key = getattr(winreg, "CloseKey")
        key_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
        key = create_key(hkey_cu, key_path)
        browser_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            browser_paths.extend([
                os.path.join(local_app, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(local_app, r"Microsoft\Edge\Application\msedge.exe"),
            ])

        for bp in browser_paths:
            if os.path.exists(bp):
                set_val(key, bp, 0, reg_sz, "GpuPreference=2;AutoHDREnable=0;")
                registered.append(bp)
        close_key(key)
    except Exception:
        log.exception("force_gpu_render failed to configure DirectX UserGpuPreferences")
        error_msg = "failed to configure GPU preferences"

    launched = False
    if action in ("launch_chrome", "launch_edge"):
        with _BROWSER_LAUNCH_LOCK:
            now = time.monotonic()
            if (now - _LAST_BROWSER_LAUNCH) < 30.0:
                return api_error("rate limit: browser launch is limited to once every 30 seconds", 429)
            _LAST_BROWSER_LAUNCH = now
        target_exe = (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if action == "launch_chrome"
            else r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        )
        if os.path.exists(target_exe):
            flags = [
                target_exe,
                "--force_high_performance_gpu",
                "--gpu-preference=high-performance",
                "--enable-gpu-rasterization",
                "--ignore-gpu-blocklist",
                "http://127.0.0.1:5000/",
            ]
            try:
                subprocess.Popen(flags)
                launched = True
            except Exception:
                log.exception("force_gpu_render failed to launch browser process")
                error_msg = "failed to launch browser process"

    return jsonify({
        "status": "success" if not error_msg else "partial_error",
        "registered_paths": registered,
        "launched": launched,
        "error": error_msg,
        "gpu_preference": 2,
        "preference_name": "High Performance (NVIDIA dGPU)",
    })
