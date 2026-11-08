"""Issue #81: Stage 1 mean-wind screening error quantification.

BatchScreener is documented as an intentional mean-wind screener
(MEAN_WIND_SCREENER=True, no variable-wind guarantee); wind_rank_report
quantifies rank correlation + miss rate between Stage 1 and reference.
"""
from __future__ import annotations

from physics.ga2.stage1 import BatchScreener, rank_correlation, wind_rank_report


def test_screener_is_documented_mean_wind() -> None:
    assert BatchScreener.MEAN_WIND_SCREENER is True


def test_rank_correlation_bounds() -> None:
    assert rank_correlation([], []) == 0.0
    assert rank_correlation([1], [1]) == 0.0
    assert abs(rank_correlation([1, 2, 3, 4], [1, 2, 3, 4]) - 1.0) < 1e-9
    assert abs(rank_correlation([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-9


def test_wind_rank_report_no_miss_on_same_ranking() -> None:
    stage1 = [10.0, 9.0, 8.0, 7.0, 6.0]
    reference = [10.0, 9.0, 8.0, 7.0, 6.0]
    report = wind_rank_report(stage1, reference, top_k=3)
    assert abs(report["rank_correlation"] - 1.0) < 1e-9
    assert report["miss_rate"] == 0.0
    assert report["top_k"] == 3.0


def test_wind_rank_report_detects_reversal() -> None:
    stage1 = [5.0, 4.0, 3.0, 2.0, 1.0]
    reference = [1.0, 2.0, 3.0, 4.0, 5.0]
    report = wind_rank_report(stage1, reference, top_k=2)
    assert abs(report["rank_correlation"] + 1.0) < 1e-9
    assert report["miss_rate"] == 1.0


def test_wind_rank_report_partial_swap() -> None:
    # A swap that ejects a reference top-3 member shows up as a miss.
    stage1 = [5.0, 4.0, 3.0, 2.0, 1.0]
    reference = [5.0, 1.0, 4.0, 3.0, 2.0]
    report = wind_rank_report(stage1, reference, top_k=3)
    # Reference top-3 = idx 0,2,3 ; Stage1 top-3 = idx 0,1,2 -> miss idx 3.
    assert report["rank_correlation"] > 0.0
    assert report["miss_rate"] == 1.0 / 3.0
    assert report["top_k"] == 3.0
