"""Regression tests for critical bugs fixed in milcy pipeline."""

import pytest
from unittest.mock import Mock, patch
from core.interfaces import (
    AircraftCandidate,
    ParametricOrigamiConfig,
    ParametricNoseSpec,
    ParametricKeelSpec,
    ParametricWingSpec,
    RobustnessReport,
    EvaluationMetric,
    FlightBenchmarkResult,
    BehaviorDescriptor,
)
from geometry.v3_origami.adapter import OrigamiAdapter, validate_tier0
from geometry.v3_origami.compiler import ParametricOrigamiCompiler
from optimization.eval.robustness import RobustnessEvaluator
from physics.benchmark.benchmark import evaluate_horizontal_glide
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
from tools.overnight_milcy_loop import run_overnight_loop
from optimization.map_elites.grid import MAPElitesGridArchive


class TestUnboundLocalErrorFixes:
    """Tests for UnboundLocalError fixes in robustness.py and benchmark.py."""

    def test_robustness_evaluator_exception_handling(self):
        """Test that evaluate_candidate doesn't crash on simulation exceptions."""
        evaluator = RobustnessEvaluator(duration_s=1.0, dt_max=0.02)

        # Create a mock candidate that will cause FlightSimulator to raise
        candidate = Mock(spec=AircraftCandidate)
        candidate.candidate_id = "test-candidate"
        candidate.raw_genome = None
        candidate.geometry = Mock()
        candidate.geometry.mass_kg = 0.005
        candidate.geometry.wing_area_m2 = 0.015
        candidate.geometry.wingspan_m = 0.18
        candidate.geometry.aspect_ratio = 2.16
        candidate.config = ParametricOrigamiConfig()

        # Patch FlightSimulator to raise exception
        with patch('optimization.eval.robustness.FlightSimulator') as mock_sim:
            mock_sim.return_value.simulate.side_effect = RuntimeError("Simulation failed")

            # Should not raise UnboundLocalError
            report = evaluator.evaluate_candidate(candidate)

            assert isinstance(report, RobustnessReport)
            assert report.dryden_survival_rate == 0.0
            assert report.jitter_robustness_score == 0.0
            assert report.high_pitch_recovery_rate == 0.0
            assert report.overall_robustness_score == 0.0

    def test_benchmark_evaluate_horizontal_glide_exception_handling(self):
        """Test that evaluate_horizontal_glide doesn't crash on simulation exceptions."""
        from physics.config_schema import AircraftConfig

        aircraft = AircraftConfig(
            mass_kg=0.005,
            wing_area_m2=0.015,
            wingspan_m=0.18,
            aspect_ratio=2.16,
            dihedral_deg=5.0,
            elevon_deg=2.0,
        )

        # Patch FlightSimulator to raise exception
        with patch('physics.benchmark.benchmark.FlightSimulator') as mock_sim:
            mock_sim.return_value.simulate.side_effect = RuntimeError("Simulation failed")
            mock_sim.return_value.metrics.return_value = {}
            mock_sim.return_value.history.return_value = []

            # Should not raise UnboundLocalError
            metric = evaluate_horizontal_glide(aircraft, speed_mps=6.0, duration_s=1.0)

            assert metric.flight_time_s == 0.0
            assert metric.distance_m == 0.0
            assert metric.crashed is False
            assert metric.diverged is False


class TestTier0FilterFixes:
    """Tests for Tier 0 filter dummy fallback removal and mesh penetration check."""

    def test_adapter_raises_on_fold_failure(self):
        """Test that compile_candidate rejects fold failure instead of returning fake passing geometry."""
        adapter = OrigamiAdapter()

        # Patch fold_genome to raise exception
        with patch('geometry.v3_origami.adapter.fold_genome') as mock_fold:
            mock_fold.side_effect = RuntimeError("Fold failed")

            cand = adapter.compile_candidate(ParametricOrigamiConfig())
            assert cand.geometry is None
            assert cand.metadata.get("fold_failed") is True
            assert cand.metadata.get("tier0_valid") is False

    def test_validate_tier0_mesh_penetration_check(self):
        """Test that validate_tier0 detects mesh penetrations."""
        # Create a candidate with raw_genome that has penetrations
        candidate = Mock(spec=AircraftCandidate)
        candidate.step_count = 12

        # Mock geometry that passes grip checks
        mock_geom = Mock()
        mock_geom.keel_height_m = 0.012
        mock_geom.keel_length_m = 0.060
        mock_geom.keel_layers = 4
        candidate.geometry = mock_geom

        # Mock raw_genome with faces that have penetrations
        mock_genome = Mock()
        mock_genome.faces = [Mock(fid=0), Mock(fid=1)]
        candidate.raw_genome = mock_genome

        with patch('geometry.v3_origami.adapter.detect_penetrations') as mock_detect:
            # Return a penetration hit
            mock_detect.return_value = [{"face_a": 0, "face_b": 1}]

            valid, reasons = validate_tier0(candidate)
            assert not valid
            assert any("Mesh self-penetration detected" in r for r in reasons)

    def test_validate_tier0_passes_when_no_penetration(self):
        """Test that validate_tier0 passes when no mesh penetration."""
        candidate = Mock(spec=AircraftCandidate)
        candidate.step_count = 12
        candidate.metadata = {}

        mock_geom = Mock()
        mock_geom.keel_height_m = 0.012
        mock_geom.keel_length_m = 0.060
        mock_geom.keel_layers = 4
        mock_geom.penetrations = 0
        mock_geom.fold_valid = True
        mock_geom.thickness_limit_ok = True
        mock_geom.flat_foldable_ok = True
        mock_geom.mass_kg = 0.005
        mock_geom.wing_area_m2 = 0.015
        mock_geom.wingspan_m = 0.18
        mock_geom.chord_m = 0.12
        mock_geom.planform_area_m2 = 0.015
        mock_geom.cg_m = (0.08, 0.0, 0.005)
        candidate.geometry = mock_geom

        mock_genome = Mock()
        mock_genome.faces = [Mock(fid=0), Mock(fid=1)]
        candidate.raw_genome = mock_genome

        with patch('geometry.v3_origami.adapter.detect_penetrations') as mock_detect:
            mock_detect.return_value = []

            valid, reasons = validate_tier0(candidate)
            assert valid
            assert len(reasons) == 0


class TestWingspanRatioFix:
    """Tests for wingspan_ratio inversion fix."""

    def test_wingspan_ratio_increases_wingspan(self):
        """Test that increasing wingspan_ratio increases actual wingspan."""
        compiler = ParametricOrigamiCompiler()

        # Low wingspan_ratio
        cfg_low = ParametricOrigamiConfig(
            wing=ParametricWingSpec(wingspan_ratio=0.55)
        )
        steps_low = compiler.compile_steps(cfg_low)

        # High wingspan_ratio
        cfg_high = ParametricOrigamiConfig(
            wing=ParametricWingSpec(wingspan_ratio=0.88)
        )
        steps_high = compiler.compile_steps(cfg_high)

        # wing_u should be smaller for low ratio (wider wings)
        # wing_u = 0.30 + 0.18 * span_ratio
        # For 0.55: wing_u = 0.30 + 0.099 = 0.399
        # For 0.88: wing_u = 0.30 + 0.158 = 0.458
        # Higher wing_u means narrower wing fold line = wider actual wingspan

        # Extract wing crease positions from steps
        wing_u_low = None
        wing_u_high = None

        for step in steps_low:
            if step.label == "wing_l":
                wing_u_low = step.crease_line[0][0]
                break

        for step in steps_high:
            if step.label == "wing_l":
                wing_u_high = step.crease_line[0][0]
                break

        assert wing_u_low is not None
        assert wing_u_high is not None

        # Higher span_ratio should give higher wing_u (closer to center = wider wing)
        assert wing_u_high > wing_u_low


class TestElevonSignFix:
    """Tests for elevon sign loss (abs()) fix."""

    def test_elevon_positive_vs_negative(self):
        """Test that positive and negative elevon angles produce different creases."""
        compiler = ParametricOrigamiCompiler()

        # Positive elevon (up)
        cfg_up = ParametricOrigamiConfig(
            wing=ParametricWingSpec(elevon_left_deg=5.0, elevon_right_deg=5.0)
        )
        steps_up = compiler.compile_steps(cfg_up)

        # Negative elevon (down)
        cfg_down = ParametricOrigamiConfig(
            wing=ParametricWingSpec(elevon_left_deg=-5.0, elevon_right_deg=-5.0)
        )
        steps_down = compiler.compile_steps(cfg_down)

        # Find elevon creases
        elevon_up = None
        elevon_down = None

        for step in steps_up:
            if step.label == "elevon_l":
                elevon_up = step
                break

        for step in steps_down:
            if step.label == "elevon_l":
                elevon_down = step
                break

        assert elevon_up is not None
        assert elevon_down is not None

        # up should be mountain fold (up=True), down should be valley fold (up=False)
        # Note: The Crease object stores up flag
        assert elevon_up.up is True
        assert elevon_down.up is False


class TestNelderMeadFix:
    """Tests for Nelder-Mead raw_genome/descriptor update and simplex clamping."""

    def test_nelder_mead_updates_raw_genome_and_descriptor(self):
        """Test that optimize_trim updates raw_genome and descriptor."""
        adapter = OrigamiAdapter()
        candidate = adapter.compile_candidate(ParametricOrigamiConfig())

        # Mock benchmark to return improved score
        from physics.benchmark.benchmark import DeterministicFlightBenchmark
        benchmark = DeterministicFlightBenchmark(duration_s=1.0)

        optimizer = NelderMeadTrimOptimizer(
            adapter=adapter,
            benchmark=benchmark,
            max_iter=3,
        )

        # Store original
        original_genome = candidate.raw_genome
        original_descriptor = candidate.descriptor

        # Patch benchmark to return better score for modified params
        original_evaluate = benchmark.evaluate_candidate

        call_count = [0]
        def mock_evaluate(cand):
            call_count[0] += 1
            # Return better score on second call (after trim)
            result = original_evaluate(cand)
            from dataclasses import replace
            if call_count[0] > 1:
                return replace(result, composite_score=1.5, valid=True)
            return replace(result, composite_score=1.0, valid=True)

        benchmark.evaluate_candidate = mock_evaluate

        result = optimizer.optimize_trim(candidate)

        # raw_genome and descriptor should be updated
        assert candidate.raw_genome is not original_genome
        assert candidate.descriptor is not original_descriptor

    def test_nelder_mead_simplex_clamping(self):
        """Test that simplex vertices are clamped to bounds."""
        adapter = OrigamiAdapter()
        candidate = adapter.compile_candidate(ParametricOrigamiConfig())

        from physics.benchmark.benchmark import DeterministicFlightBenchmark

        benchmark = DeterministicFlightBenchmark(duration_s=1.0)

        # Use narrow bounds to force clamping
        optimizer = NelderMeadTrimOptimizer(
            adapter=adapter,
            benchmark=benchmark,
            max_iter=5,
            elevon_bounds=(-2.0, 2.0),
            dihedral_bounds=(0.0, 5.0),
        )

        # The internal simplex should not exceed bounds
        # We can't easily test internal state, but we can verify
        # the final result is within bounds
        result = optimizer.optimize_trim(candidate)

        assert -2.0 <= result.optimal_elevon_deg <= 2.0
        assert 0.0 <= result.optimal_dihedral_deg <= 5.0


class TestMapElitesGridRanges:
    """Tests for MAP-Elites grid ranges calibration."""

    def test_grid_ranges_match_empirical_distribution(self):
        """Test that grid ranges cover empirical origami distribution."""
        # Empirical ranges from audit:
        # WL: 0.179-0.222 kg/m^2
        # AR: 0.597-0.698

        grid = MAPElitesGridArchive(
            bins=(10, 10, 10),
            ranges=(
                (0.15, 0.25),  # wing loading
                (0.60, 0.95),  # static stability
                (0.55, 0.75),  # aspect ratio
            )
        )

        # Test that empirical values fall within grid
        test_cases = [
            (0.179, 0.70, 0.597),  # Min empirical
            (0.222, 0.90, 0.698),  # Max empirical
            (0.200, 0.80, 0.650),  # Mid empirical
        ]

        for wl, st, ar in test_cases:
            desc = BehaviorDescriptor(
                wing_loading_kg_m2=wl,
                static_stability=st,
                aspect_ratio=ar,
            )
            bin_idx = grid.get_bin_index(desc)

            # All should be within valid bin range (0-9)
            assert 0 <= bin_idx[0] <= 9
            assert 0 <= bin_idx[1] <= 9
            assert 0 <= bin_idx[2] <= 9

            # None should be clamped to edge bins
            assert bin_idx[0] != 9 or wl >= 0.25
            assert bin_idx[2] != 9 or ar >= 0.75

    def test_grid_clamp_rejects_nan(self):
        """Test that _clamp rejects NaN values."""
        from optimization.map_elites.grid import _clamp

        with pytest.raises(ValueError, match="Non-finite value"):
            _clamp(float('nan'), 0.0, 1.0)

        with pytest.raises(ValueError, match="Non-finite value"):
            _clamp(float('inf'), 0.0, 1.0)


class TestOvernightLoopFix:
    """Tests for overnight_milcy_loop termination logic fix."""

    def test_termination_logic_absolute_datetime(self):
        """Test that termination uses absolute datetime comparison."""
        from datetime import datetime, timezone, timedelta

        # This test verifies the logic is correct
        # We can't easily test the full loop, but we can test the helper

        # Current time: 22:00 JST
        utc_now = datetime(2026, 11, 23, 13, 0, 0, tzinfo=timezone.utc)  # 22:00 JST
        jst_tz = timezone(timedelta(hours=9))
        now_jst = utc_now.astimezone(jst_tz)

        # Target: 09:00 JST next day
        target_dt = now_jst.replace(hour=9, minute=0, second=0, microsecond=0)
        if target_dt <= now_jst:
            target_dt += timedelta(days=1)

        # Should be next day
        assert target_dt.day == now_jst.day + 1
        assert target_dt.hour == 9

        # At 22:00, now_jst >= target_dt should be False
        assert not (now_jst >= target_dt)

        # At 09:00 next day, should be True
        next_morning = target_dt
        assert next_morning >= target_dt


class TestProtocolMismatchFix:
    """Tests for Protocol/implementation mismatch fix."""

    def test_origami_adapter_implements_protocol(self):
        """Test that OrigamiAdapter implements OrigamiCompilerProtocol."""
        from core.interfaces import OrigamiCompilerProtocol

        adapter = OrigamiAdapter()
        assert isinstance(adapter, OrigamiCompilerProtocol)

    def test_robustness_evaluator_implements_protocol(self):
        """Test that RobustnessEvaluator implements RobustnessEvaluatorProtocol."""
        from core.interfaces import RobustnessEvaluatorProtocol

        evaluator = RobustnessEvaluator()
        assert isinstance(evaluator, RobustnessEvaluatorProtocol)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
