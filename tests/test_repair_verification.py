"""Verification reports must expose failure and numerical non-convergence."""
from copy import deepcopy
import json
import pytest
from tools.verify_physics_repair import (target_assessment, convergence_assessment,
                                        still_air, config_from, write_outputs, detailed_phases)
from physics.config_schema import EnvironmentConfig, AircraftConfig


def run_summary(dt, altitude, duration, diverged=False):
    return {"settings":{"dt_max":dt}, "metrics":{"max_altitude_m":altitude,"flight_time_s":duration,"diverged":diverged,"landed":True}}


def test_missing_glide_diagnostics_cannot_mark_targets_achieved():
    metrics = {"max_altitude_m":16.0,"flight_time_s":31.0,"landed":True}
    assert target_assessment(metrics,{})["status"] == "unmet"
    phases = {"glide_time_s":26.0,"glide_start_time_s":3.0}
    assert target_assessment(metrics,phases)["status"] == "achieved"
    metrics["diverged"] = True
    assert target_assessment(metrics,phases)["status"] == "unmet"


def test_only_coarse_step_convergence_is_insufficient():
    runs = [run_summary(.02,16,30),run_summary(.01,16.05,30.05),run_summary(.005,18,32)]
    result = convergence_assessment(runs)
    assert result["comparisons"][0]["passed"] is True
    assert result["comparisons"][1]["passed"] is False
    assert result["status"] == "not_converged"


def test_diverged_results_cannot_converge_even_with_equal_metrics():
    runs = [run_summary(.02,16,30),run_summary(.01,16,30,True),run_summary(.005,16,30)]
    assert convergence_assessment(runs)["status"] == "not_converged"


def test_three_matching_refinements_converge():
    runs = [run_summary(.02,16,30),run_summary(.01,16.005,30.005),run_summary(.005,16.006,30.006)]
    assert convergence_assessment(runs)["status"] == "converged"
    assert convergence_assessment([])["status"] == "not_converged"


def test_still_air_is_explicit_reproducible_and_does_not_mutate_source():
    source = EnvironmentConfig(wind_speed=7,gust_intensity=.8,thermal_strength=2,turbulence_scale=.6)
    quiet = still_air(source)
    assert (quiet.wind_speed,quiet.gust_intensity,quiet.thermal_strength,quiet.turbulence_scale) == (0,0,0,0)
    assert quiet.turbulence_seed == 12345
    assert source.wind_speed == 7


def test_config_contract_keeps_new_defaults_and_ignores_report_metadata():
    cfg = config_from(AircraftConfig,{"mass_kg":.006,"report_notes":"old baseline"})
    assert cfg.mass_kg == .006
    assert cfg.wingspan_m == AircraftConfig().wingspan_m


def test_report_writer_preserves_existing_baseline(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"sentinel":true}',encoding="utf-8")
    payload = {"generated_at_utc":"2026-10-10T00:00:00+00:00", "convergence":{"status":"not_converged"},
               "convergence_runs":[],"preset_runs":[],"sensitivity_runs":[]}
    write_outputs(tmp_path,payload)
    assert baseline.read_text(encoding="utf-8") == '{"sentinel":true}'
    assert json.loads((tmp_path/"after.json").read_text(encoding="utf-8")) == payload
    assert "not_converged" in (tmp_path/"verification.md").read_text(encoding="utf-8")


def test_duration_cap_is_not_a_converged_flight_time():
    runs = [run_summary(.02,16,60),run_summary(.01,16,60),run_summary(.005,16,60)]
    for run in runs:
        run["metrics"]["landed"] = False
    assert convergence_assessment(runs)["status"] == "not_converged"


def phase_row(t, vz, pitch_rate=0.1):
    return {"time_s":t,"velocity_mps":[3,0,vz],"speed_mps":3.0,"alpha_deg":5.0,
            "pitch_rate_radps":pitch_rate,"altitude_m":10+t,"stalled":False}


def test_first_apex_is_velocity_crossing_not_later_altitude_maximum():
    log = [phase_row(0,-2),phase_row(1,2),phase_row(2,-1),phase_row(3,1)]
    result = detailed_phases(log)
    assert result["first_apex_time_s"] == pytest.approx(.5)
    assert result["first_apex_height_m"] == pytest.approx(10.5)


def test_rapid_pitch_rotation_breaks_stable_glide_segment():
    log = [phase_row(t, .5) for t in range(6)]
    log[3]["pitch_rate_radps"] = 1.0
    result = detailed_phases(log, "apex_glide")
    assert result["glide_time_s"] == 2.0
    assert result["glide_start_time_s"] == 0.0
    assert result["ascent_time_s"] == 0.0


def test_step_rollback_prevents_numerical_convergence_success():
    runs = [run_summary(.02,16,30),run_summary(.01,16,30),run_summary(.005,16,30)]
    runs[0]["metrics"]["rolled_back_steps"] = 1
    assert convergence_assessment(runs)["status"] == "not_converged"
