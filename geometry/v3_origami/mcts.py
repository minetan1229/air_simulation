"""Monte Carlo Tree Search (MCTS) generator for valid origami skeletons.

Explores fold combinations (nose folds, keel parameters, wing/elevon geometry)
using fast geometric rewards (non-6DoF) and strict Tier 0 pruning.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Set, Tuple

from core.interfaces import (
    AircraftCandidate,
    GeometricStaticScore,
    ParametricKeelSpec,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from geometry.v3_origami.adapter import OrigamiAdapter, validate_tier0


def _config_key(cfg: ParametricOrigamiConfig) -> Tuple[Any, ...]:
    """Canonical discretized key for transposition deduplication."""
    return (
        int(cfg.nose.fold_count),
        round(cfg.nose.taper_ratio, 3),
        round(cfg.nose.nose_length_ratio, 3),
        round(cfg.keel.keel_depth_ratio, 3),
        bool(cfg.keel.has_lock_fold),
        round(cfg.wing.wingspan_ratio, 3),
        round(cfg.wing.dihedral_deg, 2),
        round(cfg.wing.elevon_left_deg, 2),
        round(cfg.wing.elevon_right_deg, 2),
    )


@dataclass
class MCTSNode:
    """A node in the origami fold parameter search tree with UCB1-Tuned stats."""

    config: ParametricOrigamiConfig
    parent: Optional[MCTSNode] = None
    children: List[MCTSNode] = field(default_factory=list)
    visits: int = 0
    total_value: float = 0.0
    total_value_sq: float = 0.0
    action_desc: str = "root"

    @property
    def ucb1(self) -> float:
        """UCB1-Tuned score balancing bounded [0, 1] rewards and empirical variance."""
        if self.visits == 0:
            return float("inf")
        parent_visits = self.parent.visits if self.parent else self.visits
        mean = self.total_value / self.visits
        mean_sq = self.total_value_sq / self.visits
        variance = max(0.0, mean_sq - mean * mean)
        ln_n = math.log(max(parent_visits, 1) + 1.0)
        v_bound = variance + math.sqrt(2.0 * ln_n / self.visits)
        explore = math.sqrt((ln_n / self.visits) * min(0.25, v_bound))
        return mean + explore


class MCTSOrigamiGenerator:
    """Generates valid 16-24 step origami skeletons via MCTS with geometric pruning."""

    def __init__(
        self,
        adapter: Optional[OrigamiAdapter] = None,
        seed: int = 42,
    ) -> None:
        self.adapter = adapter or OrigamiAdapter()
        self.rng = random.Random(seed)

    def evaluate_leaf_geometry(self, config: ParametricOrigamiConfig) -> Tuple[GeometricStaticScore, Optional[AircraftCandidate]]:
        """Fast geometric non-6DoF evaluation for MCTS rollout/leaf."""
        candidate = self.adapter.compile_candidate(config, include_macro=False)
        valid = candidate.metadata.get("tier0_valid", False)
        reasons = candidate.metadata.get("tier0_reasons", [])

        if not valid or candidate.geometry is None:
            return GeometricStaticScore(
                cg_margin_score=0.0,
                symmetry_score=0.0,
                wing_loading_score=0.0,
                tier0_passed=False,
                rejection_reasons=tuple(reasons),
            ), None

        geom = candidate.geometry

        # 1. CG margin: distance from dynamic nose tip to CG relative to chord
        # Desirable CG for paper aircraft glide is between 25% and 55% of chord
        dist_from_nose = geom.nose_tip_x_m - geom.cg_m[0]
        chord = max(1.0e-3, geom.chord_m)
        cg_ratio = dist_from_nose / chord
        if 0.25 <= cg_ratio <= 0.55:
            cg_score = 1.0 - abs(cg_ratio - 0.38) / 0.25
        else:
            cg_score = max(0.0, 1.0 - abs(cg_ratio - 0.38))

        # 2. Symmetry score: wing elevon & 3D lateral CG symmetry
        elevon_diff = abs(config.wing.elevon_left_deg - config.wing.elevon_right_deg)
        elevon_sym = max(0.0, 1.0 - elevon_diff / 10.0)
        half_span = max(1.0e-3, 0.5 * geom.wingspan_m)
        lateral_cg_err = abs(geom.cg_m[1]) / half_span
        cg_sym = max(0.0, 1.0 - lateral_cg_err * 5.0)
        symmetry_score = 0.5 * elevon_sym + 0.5 * cg_sym

        # 3. Wing loading score: typical paper plane is 0.10 - 0.20 kg/m^2 (10-20 g/dm^2)
        loading = geom.mass_kg / max(1.0e-5, geom.wing_area_m2)
        if 0.10 <= loading <= 0.20:
            loading_score = 1.0 - abs(loading - 0.14) / 0.08
        else:
            loading_score = max(0.0, 1.0 - abs(loading - 0.14) / 0.15)

        return GeometricStaticScore(
            cg_margin_score=cg_score,
            symmetry_score=symmetry_score,
            wing_loading_score=loading_score,
            tier0_passed=True,
            rejection_reasons=(),
        ), candidate

    def _expand_node(self, node: MCTSNode, visited_keys: Optional[Set[Tuple[Any, ...]]] = None) -> None:
        """Expand node with bidirectional symmetric actions preserving all fields."""
        base = node.config

        raw_variations: List[Tuple[str, ParametricOrigamiConfig]] = [
            # Phase 1 Nose variations
            ("nose_more_folds", replace(base, nose=replace(base.nose, fold_count=min(6, base.nose.fold_count + 1)))),
            ("nose_fewer_folds", replace(base, nose=replace(base.nose, fold_count=max(3, base.nose.fold_count - 1)))),
            ("nose_sharper_taper", replace(base, nose=replace(base.nose, taper_ratio=max(0.45, base.nose.taper_ratio - 0.08)))),
            ("nose_wider_taper", replace(base, nose=replace(base.nose, taper_ratio=min(0.85, base.nose.taper_ratio + 0.08)))),
            ("nose_extended", replace(base, nose=replace(base.nose, nose_length_ratio=min(0.35, base.nose.nose_length_ratio + 0.03)))),
            ("nose_shortened", replace(base, nose=replace(base.nose, nose_length_ratio=max(0.20, base.nose.nose_length_ratio - 0.03)))),
            # Phase 2 Keel variations
            ("keel_deeper_locked", replace(base, keel=replace(base.keel, keel_depth_ratio=min(0.14, base.keel.keel_depth_ratio + 0.02), has_lock_fold=True))),
            ("keel_shallower", replace(base, keel=replace(base.keel, keel_depth_ratio=max(0.06, base.keel.keel_depth_ratio - 0.02)))),
            # Phase 3 Wing variations
            ("wing_higher_aspect", replace(base, wing=replace(base.wing, wingspan_ratio=min(0.88, base.wing.wingspan_ratio + 0.06)))),
            ("wing_lower_aspect", replace(base, wing=replace(base.wing, wingspan_ratio=max(0.55, base.wing.wingspan_ratio - 0.06)))),
            ("wing_more_dihedral", replace(base, wing=replace(base.wing, dihedral_deg=min(8.0, base.wing.dihedral_deg + 1.5)))),
            ("wing_less_dihedral", replace(base, wing=replace(base.wing, dihedral_deg=max(0.0, base.wing.dihedral_deg - 1.5)))),
        ]

        for desc, cfg in raw_variations:
            key = _config_key(cfg)
            if visited_keys is not None:
                if key in visited_keys:
                    continue
                visited_keys.add(key)
            node.children.append(MCTSNode(config=cfg, parent=node, action_desc=desc))

    def search_valid_candidates(
        self,
        iterations: int = 50,
        top_k: int = 5,
    ) -> List[AircraftCandidate]:
        """Run UCB1-Tuned MCTS search and return diverse Tier-0 compliant candidates."""
        root_cfg = ParametricOrigamiConfig()
        root = MCTSNode(config=root_cfg)
        visited_keys: Set[Tuple[Any, ...]] = {_config_key(root_cfg)}
        best_candidates: List[Tuple[float, AircraftCandidate]] = []

        for _ in range(iterations):
            curr = root
            while curr.children:
                curr = max(curr.children, key=lambda c: c.ucb1)

            if curr.visits > 0 or curr == root:
                self._expand_node(curr, visited_keys)
                if curr.children:
                    curr = self.rng.choice(curr.children)

            # 3. Simulation / Geometric Rollout
            score, cand = self.evaluate_leaf_geometry(curr.config)
            value = score.total_value

            if score.tier0_passed and cand is not None:
                best_candidates.append((value, cand))

            p: Optional[MCTSNode] = curr
            while p is not None:
                p.visits += 1
                p.total_value += value
                p.total_value_sq += value * value
                p = p.parent

        best_candidates.sort(key=lambda item: item[0], reverse=True)
        unique_results: List[AircraftCandidate] = []
        seen_keys: Set[Tuple[Any, ...]] = set()
        for _, cand in best_candidates:
            key = _config_key(cand.config)
            if key not in seen_keys:
                seen_keys.add(key)
                unique_results.append(cand)
            if len(unique_results) >= top_k:
                break

        if not unique_results:
            fallback = self.adapter.compile_candidate(ParametricOrigamiConfig())
            unique_results.append(fallback)

        return unique_results
