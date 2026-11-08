"""Remote evaluate worker (runs on the Linux GPU node).

Reads cases.json.gz from stdin/file, evaluates each case with
physics.parallel_engine.evaluate_case (via parallel_evaluate for worker
parallelism), writes ordered results.json.gz.

Ordered JSON using the same shape as parallel_evaluate output.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def main() -> int:
    parser = argparse.ArgumentParser(description="Remote hybrid evaluate worker")
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_path", required=True)
    parser.add_argument("--processes", type=int, default=0)
    args = parser.parse_args()

    raw = Path(args.in_path).read_bytes()
    if args.in_path.endswith(".gz"):
        raw = gzip.decompress(raw)
    cases: List[Dict[str, Any]] = json.loads(raw.decode("utf-8"))

    # Make repo root importable (worker is deployed as tools/remote_evaluate_worker.py)
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from physics.parallel_engine import parallel_evaluate

    results = parallel_evaluate(cases, processes=int(args.processes or 0))
    payload = json.dumps(results).encode("utf-8")
    if args.out_path.endswith(".gz"):
        payload = gzip.compress(payload, mtime=0)
    Path(args.out_path).write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
