"""Monte Carlo Tree Search (MCTS) generator for valid origami skeletons.

Explores fold combinations (nose folds, keel parameters, wing/elevon geometry)
using fast geometric rewards (non-6DoF) and strict Tier 0 pruning.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from v3.interfaces import (
    AircraftCandidate,
    GeometricStaticScore,
    ParametricKeelSpec,
    ParametricNoseSpec,
    ParametricOrigamiConfig,
    ParametricWingSpec,
)
from v3.origami.adapter import OrigamiAdapter, validate_tier0


@dataclass
class MCTSNode:
    """A node in the origami fold parameter search tree."""

    config: ParametricOrigamiConfig
    parent: Optional[MCTSNode] = None
    children: List[MCTSNode] = field(default_factory=list)
    visits: int = 0
    total_value: float = 0.0
    action_desc: str = "root"

    @property
    def ucb1(self) -> float:
        """Upper Confidence Bound 1 for tree selection."""
        if self.visits == 0:
            return float("inf")
        c = 1.414  # Exploration constant
        parent_visits = self.parent.visits if self.parent else self.visits
        exploit = self.total_value / self.visits
        explore = c * math.sqrt(math.log(parent_visits + 1) / self.visits)
        return exploit + explore


class MCTSOrigamiGenerator:
    """Generates valid 16-24 step origami skeletons via MCTS with geometric pruning."""

    def __init__(
        self,
        adapter: Optional[OrigamiAdapter] = None,
        seed: int = 42,
    ) -> None:
        self.adapter = adapter or OrigamiAdapter()
        self.rng = random.Random(seed)

    def evaluate_leaf_geometry(self, config: ParametricOrigamiConfig) -> GeometricStaticScore:
        """Fast geometric non-6DoF evaluation for MCTS rollout/leaf."""
        candidate = self.adapter.compile_candidate(config)
        valid, reasons = validate_tier0(candidate)

        if not valid or candidate.geometry is None:
            return GeometricStaticScore(
                cg_margin_score=0.0,
                symmetry_score=0.0,
                wing_loading_score=0.0,
                tier0_passed=False,
                rejection_reasons=tuple(reasons),
            )

        geom = candidate.geometry

        # 1. CG margin: distance from nose tip (0.1485) to CG relative to chord
        # Desirable CG for paper aircraft glide is between 25% and 55% of chord
        dist_from_nose = 0.1485 - geom.cg_m[0]
        chord = max(1.0e-3, geom.chord_m)
        cg_ratio = dist_from_nose / chord
        if 0.25 <= cg_ratio <= 0.55:
            cg_score = 1.0 - abs(cg_ratio - 0.38) / 0.25
        else:
            cg_score = max(0.0, 1.0 - abs(cg_ratio - 0.38))

        # 2. Symmetry score: wing elevon & winglet symmetry
        elevon_diff = abs(config.wing.elevon_left_deg - config.wing.elevon_right_deg)
        symmetry_score = max(0.0, 1.0 - elevon_diff / 10.0)

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
        )

    def _expand_node(self, node: MCTSNode) -> None:
        """Expand node with possible parameter variations across Phases 1, 2, and 3."""
        base = node.config

        variations = [
            # Phase 1 Nose variations
            (
                "nose_more_folds",
                ParametricOrigamiConfig(
                    nose=ParametricNoseSpec(
                        fold_count=min(6, base.nose.fold_count + 1),
                        taper_ratio=base.nose.taper_ratio,
                    ),
                    keel=base.keel,
                    wing=base.wing,
                ),
            ),
            (
                "nose_sharper_taper",
                ParametricOrigamiConfig(
                    nose=ParametricNoseSpec(
                        fold_count=base.nose.fold_count,
                        taper_ratio=max(0.45, base.nose.taper_ratio - 0.1),
                    ),
                    keel=base.keel,
                    wing=base.wing,
                ),
            ),
            # Phase 2 Keel variations
            (
                "keel_deeper",
                ParametricOrigamiConfig(
                    nose=base.nose,
                    keel=ParametricKeelSpec(
                        keel_depth_ratio=min(0.14, base.keel.keel_depth_ratio + 0.02),
                        has_lock_fold=True,
                    ),
                    wing=base.wing,
                ),
            ),
            # Phase 3 Wing variations
            (
                "wing_higher_aspect",
                ParametricOrigamiConfig(
                    nose=base.nose,
                    keel=base.keel,
                    wing=ParametricWingSpec(
                        wingspan_ratio=min(0.88, base.wing.wingspan_ratio + 0.08),
                        dihedral_deg=base.wing.dihedral_deg,
                    ),
                ),
            ),
            (
                "wing_more_dihedral",
                ParametricOrigamiConfig(
                    nose=base.nose,
                    keel=base.keel,
                    wing=ParametricWingSpec(
                        wingspan_ratio=base.wing.wingspan_ratio,
                        dihedral_deg=min(8.0, base.wing.dihedral_deg + 2.0),
                    ),
                ),
            ),
        ]

        for desc, cfg in variations:
            child = MCTSNode(config=cfg, parent=node, action_desc=desc)
            node.children.append(child)

    def search_valid_candidates(
        self,
        iterations: int = 50,
        top_k: int = 5,
    ) -> List[AircraftCandidate]:
        """Run MCTS search and return top Tier-0 compliant aircraft candidates."""
        root = MCTSNode(config=ParametricOrigamiConfig())
        best_candidates: List[Tuple[float, AircraftCandidate]] = []

        for _ in range(iterations):
            # 1. Selection
            curr = root
            while curr.children:
                curr = max(curr.children, key=lambda c: c.ucb1)

            # 2. Expansion
            if curr.visits > 0 or curr == root:
                self._expand_node(curr)
                if curr.children:
                    curr = self.rng.choice(curr.children)

            # 3. Simulation / Geometric Rollout
            score = self.evaluate_leaf_geometry(curr.config)
            value = score.total_value

            if score.tier0_passed:
                cand = self.adapter.compile_candidate(curr.config)
                best_candidates.append((value, cand))

            # 4. Backpropagation
            p: Optional[MCTSNode] = curr
            while p is not None:
                p.visits += 1
                p.total_value += value
                p = p.parent

        # Sort and deduplicate top candidates
        best_candidates.sort(key=lambda item: item[0], reverse=True)
        unique_results: List[AircraftCandidate] = []
        seen_keys = set()
        for _, cand in best_candidates:
            key = (
                cand.config.nose.fold_count,
                round(cand.config.nose.taper_ratio, 2),
                round(cand.config.keel.keel_depth_ratio, 2),
                round(cand.config.wing.wingspan_ratio, 2),
            )
            if key not in seen_keys:
                seen_keys.add(key)
                unique_results.append(cand)
            if len(unique_results) >= top_k:
                break

        # Fallback if no candidate passed
        if not unique_results:
            fallback = self.adapter.compile_candidate(ParametricOrigamiConfig())
            unique_results.append(fallback)

        return unique_results
