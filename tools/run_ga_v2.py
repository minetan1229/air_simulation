"""Command line entry point of the second generation genetic search.

The command line is where the size of a run is decided: the tier of the
hardware profile, the population, the number of generations and the budget
of every stage.  None of these numbers is written into the search logic --
they all live in :class:`physics.ga2.scaling.ScalingProfile`, and the
command line only overrides the fields of that profile.

The script refuses to run on a protected branch: the research phase of this
project lives on the ``genetic-project`` branch and must never touch
``master`` / ``main`` (see :mod:`tools.branch_guard`).

Examples
--------
A short run on the laptop tier::

    python tools/run_ga_v2.py --tier small_long --population 200 \
        --generations 3 --out runs/ga_v2_small

The production profile, continued from a checkpoint of the laptop::

    python tools/run_ga_v2.py --tier cluster_20m --generations 1000 \
        --resume runs/ga_v2_small/generation_000003.json \
        --out runs/ga_v2_small
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

from physics.ga2.scaling import (  # noqa: E402
    TIERS,
    ScalingProfile,
    detect_profile,
)
from tools.branch_guard import (  # noqa: E402
    BranchViolation,
    assert_branch_allowed,
    describe_status,
)


def build_parser() -> argparse.ArgumentParser:
    """The command line of the search."""
    parser = argparse.ArgumentParser(
        description="Run the second generation genetic search for paper airplane fold shapes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--tier",
        choices=("auto",) + TIERS,
        default="auto",
        help="the hardware tier of the run; 'auto' detects the CUDA devices of the host",
    )
    parser.add_argument("--population", type=int, default=0, help="individuals per generation")
    parser.add_argument("--generations", type=int, default=1, help="generations to run")
    parser.add_argument("--islands", type=int, default=0, help="number of sub populations")
    parser.add_argument("--seed", type=int, default=20251021, help="seed of the random generator")
    parser.add_argument("--out", default="runs/ga_v2", help="directory of the checkpoints")
    parser.add_argument("--resume", default="", help="checkpoint to continue from")
    parser.add_argument("--gsm", type=float, default=100.0, help="grammage of the paper")
    parser.add_argument(
        "--stage3",
        dest="stage3",
        action="store_true",
        default=True,
        help="run the flow solver stage",
    )
    parser.add_argument(
        "--no-stage3",
        dest="stage3",
        action="store_false",
        help="skip the flow solver stage",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="print the resolved profile and exit",
    )
    return parser


def resolve_profile(args: argparse.Namespace) -> ScalingProfile:
    """The profile of the run, with the command line overrides applied."""
    if args.tier == "auto":
        profile = detect_profile()
    else:
        profile = ScalingProfile.for_tier(args.tier)
    overrides: Dict[str, Any] = {}
    if args.population > 0:
        overrides["population_size"] = int(args.population)
    if args.islands > 0:
        overrides["islands"] = int(args.islands)
    return profile.with_overrides(**overrides).validate()


def main(argv: Optional[List[str]] = None) -> int:
    """Run the search and print the summary of every generation as JSON."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        status = assert_branch_allowed()
    except BranchViolation as exc:
        print(f"[branch-guard] {exc}", file=sys.stderr)
        return 2
    print(describe_status(status), file=sys.stderr)

    profile = resolve_profile(args)
    if args.print_config:
        print(json.dumps(profile.to_dict(), indent=2, sort_keys=True, default=str))
        return 0

    from physics.ga2.runner import EvolutionRunner

    runner = EvolutionRunner(
        profile=profile,
        out_dir=args.out,
        seed=int(args.seed),
        paper_gsm=float(args.gsm),
        stage3_enabled=bool(args.stage3),
    )
    if args.resume:
        try:
            runner.load(args.resume)
        except (OSError, ValueError) as exc:
            print(f"cannot resume from {args.resume}: {exc}", file=sys.stderr)
            return 2

    for report in runner.run(int(args.generations)):
        print(json.dumps(report.to_dict(), sort_keys=True, default=str))
    best = runner.archive.best()
    if best is not None:
        print(
            json.dumps(
                {"best": best.to_dict()},
                sort_keys=True,
                default=str,
            ),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
