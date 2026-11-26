"""Re-export all Milcy Iteration 3 falsification tests for the audit suite."""

from tests.milcy_falsification.w_iter3_physics import TestWIter3Physics
from tests.milcy_falsification.w_iter3_origami import TestIter3OrigamiFalsification

__all__ = [
    "TestWIter3Physics",
    "TestIter3OrigamiFalsification",
]
