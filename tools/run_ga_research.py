"""Command line entry point of the autonomous genetic research engine.

The command line is one of the four places where the size of a run can be
decided (the YAML configuration file, this command line, the runtime API
``run_research`` and a checkpoint resume).  Every count is therefore an
option here, and none of them is written into the search logic.

The script refuses to run on a protected branch: the research phase of this
project lives on the ``genetic-project`` branch and must never touch
``master`` / ``main`` (see :mod:`tools.branch_guard`).

Examples
--------
A small distance run::

    python tools/run_ga_research.py --objective distance --max-trials 60 \
        --top-k 5 --tier1-trials 5 --tier2-trials 10 --tier3-trials 60 \
        --generations 3 --islands 2 --seed 7 --out runs/ga_distance

The same run resumed from a checkpoint with a smaller budget::

    python tools/run_ga_research.py --resume runs/ga_distance/checkpoints/latest.json \
        --max-trials 100 --top-k 10 --out runs/ga_distance
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the repository root importable when the script is run directly.
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from physics.ga_config import (  # noqa: E402
    GAConfig,
    OBJECTIVES,
    STAT_MODES,
    load_config_file,
)
from tools.branch_guard import (  # noqa: E402
    BranchViolation,
    assert_branch_allowed,
    describe_status,
)

#: The configuration file that is read when ``--config`` is not given.
DEFAULT_CONFIG_PATH = project_root / "config" / "genetic_research.yaml"


def build_parser() -> argparse.ArgumentParser:
    """The argument parser of the research runner."""
    parser = argparse.ArgumentParser(
        prog="run_ga_research",
        description="Autonomous genetic research on the paper aeroplane simulator.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    group = parser.add_argument_group("objective")
    group.add_argument(
        "--objective",
        choices=list(OBJECTIVES),
        default=None,
        help="distance (maximise the flight distance) or airtime (maximise the hang time)",
    )
    group.add_argument(
        "--stat-mode",
        choices=list(STAT_MODES),
        default=None,
        help="the population statistic used as the fitness aggregate",
    )

    group = parser.add_argument_group("budgets")
    group.add_argument("--max-trials", type=int, default=None, help="total flight evaluations of the run")
    group.add_argument("--top-k", type=int, default=None, help="number of elites kept in the hall of fame")
    group.add_argument("--tier1-trials", type=int, default=None, help="individuals promoted to the surrogate tier")
    group.add_argument("--tier2-trials", type=int, default=None, help="individuals promoted to the short 6-DoF tier")
    group.add_argument("--tier3-trials", type=int, default=None, help="individuals promoted to the turbulent 6-DoF tier")

    group = parser.add_argument_group("population")
    group.add_argument("--seed", type=int, default=None, help="seed of the search")
    group.add_argument("--generations", type=int, default=None, help="number of generations")
    group.add_argument("--islands", type=int, default=None, help="number of independent islands")
    group.add_argument("--population-size", type=int, default=None, help="individuals kept per island")
    group.add_argument("--max-fold-steps", type=int, default=None, help="maximum number of fold steps of a genome")
    group.add_argument("--cma-sigma0", type=float, default=None, help="initial step size of the CMA-ES")

    group = parser.add_argument_group("physics")
    group.add_argument("--tier2-duration", type=float, default=None, help="duration of the intermediate tier (s)")
    group.add_argument("--tier3-duration", type=float, default=None, help="duration of the full fidelity tier (s)")
    group.add_argument("--paper-gsm", type=float, default=None, help="areal density of the paper (g/m2)")
    group.add_argument("--launch-height", type=float, default=None, help="release height above the floor (m)")
    group.add_argument("--processes", type=int, default=None, help="worker processes of the batch evaluator")
    group.add_argument(
        "--tier3-wind-speed",
        type=float,
        default=None,
        help="mean wind speed of the full fidelity tier (m/s)",
    )
    group.add_argument(
        "--tier3-gust-intensity",
        type=float,
        default=None,
        help="gust intensity of the full fidelity tier (0..1)",
    )
    group.add_argument(
        "--tier3-thermal-strength",
        type=float,
        default=None,
        help="thermal strength of the full fidelity tier (0..1)",
    )
    group.add_argument(
        "--tier3-turbulence-scale",
        type=float,
        default=None,
        help="length scale of the 3-D turbulence of the full fidelity tier (m)",
    )

    group = parser.add_argument_group("meta control")
    group.add_argument(
        "--stagnation-generations",
        type=int,
        default=None,
        help="generations without improvement before the structure search shift fires",
    )
    group.add_argument("--migration-interval", type=int, default=None, help="generations between island migrations")
    group.add_argument("--migration-elites", type=int, default=None, help="elites migrated per island edge")
    group.add_argument(
        "--micro-flaps",
        dest="micro_flaps",
        action="store_true",
        default=None,
        help="enable the micro adjustment flaps of the genome representation",
    )

    group = parser.add_argument_group("input and output")
    group.add_argument("--config", default=None, help="path of the YAML configuration file")
    group.add_argument("--out", dest="out_dir", default=None, help="output directory of the run")
    group.add_argument("--checkpoint-dir", default=None, help="directory of the checkpoints")
    group.add_argument("--journal", dest="journal_path", default=None, help="path of the decision journal")
    group.add_argument("--resume", dest="resume_from", default=None, help="checkpoint to resume from")
    group.add_argument(
        "--print-config",
        action="store_true",
        help="print the effective configuration and exit without running",
    )
    return parser


def overrides_from_arguments(args: argparse.Namespace) -> Dict[str, Any]:
    """The configuration overrides the command line asks for.

    Only the options that were actually given are returned, so the
    configuration file keeps its authority over everything else.
    """
    names = (
        "objective",
        "stat_mode",
        "max_trials",
        "top_k",
        "tier1_trials",
        "tier2_trials",
        "tier3_trials",
        "seed",
        "generations",
        "islands",
        "population_size",
        "max_fold_steps",
        "cma_sigma0",
        "paper_gsm",
        "processes",
        "tier3_wind_speed",
        "tier3_gust_intensity",
        "tier3_thermal_strength",
        "tier3_turbulence_scale",
        "stagnation_generations",
        "migration_interval",
        "migration_elites",
        "micro_flaps",
        "out_dir",
        "checkpoint_dir",
        "journal_path",
        "resume_from",
    )
    overrides: Dict[str, Any] = {}
    for name in names:
        value = getattr(args, name, None)
        if value is not None:
            overrides[name] = value
    if getattr(args, "tier2_duration", None) is not None:
        overrides["tier2_duration_s"] = float(args.tier2_duration)
    if getattr(args, "tier3_duration", None) is not None:
        overrides["tier3_duration_s"] = float(args.tier3_duration)
    if getattr(args, "launch_height", None) is not None:
        overrides["launch_height_m"] = float(args.launch_height)
    return overrides


def load_configuration(args: argparse.Namespace) -> GAConfig:
    """The effective configuration of the run."""
    path = args.config or (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else None)
    overrides = overrides_from_arguments(args)
    if path:
        return load_config_file(str(path), overrides)
    return GAConfig().merged(overrides).validate()


def main(argv: Optional[List[str]] = None) -> int:
    """Run the research programme and print its summary as JSON."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # The branch guard runs *before* any work is done: the research phase
    # must never execute against a protected branch.
    try:
        status = assert_branch_allowed()
    except BranchViolation as exc:
        print(f"[branch-guard] {exc}", file=sys.stderr)
        return 2
    print(describe_status(status), file=sys.stderr)

    try:
        config = load_configuration(args)
    except (OSError, ValueError) as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        return 2

    if args.print_config:
        print(json.dumps(config.to_dict(), indent=2, sort_keys=True, default=str))
        return 0

    from physics.ga_domain import run_research

    overrides = overrides_from_arguments(args)
    try:
        # The configuration that was read from the YAML file is the *base*
        # of the run and the command line overrides are merged on top of
        # it, so the precedence is
        #
        #     command line override > YAML value > dataclass default
        #
        # Without the base configuration every value of the YAML file would
        # be silently dropped and only the command line would steer the run.
        summary = run_research(overrides, base_config=config)
    except Exception as exc:  # pragma: no cover - surfaced to the shell
        print(f"research run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
