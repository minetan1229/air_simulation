"""Replace the GPU batch body with the shared 6-DoF batch.

One-shot maintenance helper: it replaces the hand-written 3-DoF surrogate of
:func:`physics.parallel_engine.gpu_batch_evaluate_cases` with the vectorised
13-state rigid body of :mod:`physics.gpu_dynamics`.  Kept as a tool so the
transformation is reviewable and repeatable instead of a giant hand edit.
"""

from __future__ import annotations

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "physics" / "parallel_engine.py"
BLOCK = ROOT / ".tmp_gpu_batch_block.txt"

START = "    try:\n        import cupy as xp\n    except Exception:\n        return gpu_batch_evaluate_cases(cases, progress_cb=progress_cb, force_cpu=True)\n"
END = "    if progress_cb is not None:\n        try:\n            progress_cb(N, N, backend)\n        except Exception:\n            pass\n\n    return results\n"


def main() -> int:
    source = io.open(TARGET, encoding="utf-8").read()
    start = source.find(START)
    if start < 0:
        print("start marker not found -- already applied?")
        return 0
    end = source.find(END, start)
    if end < 0:
        print("end marker not found")
        return 1
    end += len(END)
    block = io.open(BLOCK, encoding="utf-8").read()
    io.open(
        TARGET, "w", encoding="utf-8", newline="\n"
    ).write(source[:start] + block + source[end:])
    print(f"replaced {end - start} characters with the shared 6-DoF batch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
