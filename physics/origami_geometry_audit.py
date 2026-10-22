"""折り紙実測幾何と解析翼型幾何の差分監査.

§A-14 を維持: 折り紙メッシュは表示専用であり, 空力・応力・慣性の
物理計算には取り込まない. 本モジュールは, 折り紙実測スパン・翼面積・
幾何AR と解析翼型 (derived_properties) の対応値の差分を関数として
定量的に公開するだけである.

**面積の定義（重要）**: 解析翼型の ``wing_area_m2`` は**平面形面積**
（翼が真上から覆う面積）である. 折り紙側でこれと対比できる量も平面形
面積でなければならない. 展開紙面（両面 + 折り返し層を数えた
``OrigamiMesh.surface_area()``）は別の量であり, 平面形面積の約 3.8 倍に
なるため**直接比較してはならない**. 平面形面積は折り畳みソリッドを
翼弦-翼展平面へ射影した**シルエット**として求め, 展開紙面面積は
``origami_sheet_area_m2`` として別項目で公開する.

物理側への書き込みや登録は行わない.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from physics.config_schema import AircraftConfig, derived_properties
from physics.origami_domain import ARCHETYPE_DART, ARCHETYPES, build_mesh

#: 翼展を測る境界箱の軸. ビューア (static/js/app.js) と
#: api/routes/origami.py と同一規約: z 軸方向 (spanwise).
_ORIGAMI_SPAN_AXIS = 2

#: 翼弦方向の軸. 平面形 (planform) は翼弦-翼展平面への射影である.
_ORIGAMI_CHORD_AXIS = 0

#: 平面形面積のラスタ分解能 (m). 階段近似の誤差は周囲長 x 分 解能で
#: 抑えられ, dart の平面形に対して約 1 % である.
_PLANFORM_CELL_M = 5.0e-4


def _mesh_span(mesh) -> float:
    """折り畳みメッシュの翼展 (m). z 軸方向の境界箱幅."""
    lo, hi = mesh.bounds()
    return max(hi[_ORIGAMI_SPAN_AXIS] - lo[_ORIGAMI_SPAN_AXIS], 0.0)


def _planform_area_m2(mesh, cell_m: float = _PLANFORM_CELL_M) -> float:
    """折り畳みメッシュの平面形面積 (m^2).

    平面形は, 飛行軸に平行に射影した**シルエット**: 真上から見たときに
    機体が覆う領域の面積である. 解析翼型の ``wing_area_m2`` が持つ意味と
    同じ量であり, そのまま対比できる.

    展開紙面 (``mesh.surface_area()``) とは別の量である. 展開紙面は
    紙の両面と折り返し層をすべて数えるので, 平面形の約 3.8 倍になる.

    シルエットは走査線 (scanline) でラスタライズする. 各行について三角形の
    辺との交点を集め, 差分配列で被覆セルを数えるため, 折り返しで重なる
    層は 1 回だけ数えられる.
    """
    positions = np.asarray(mesh.positions, dtype=np.float64)
    indices = np.asarray(mesh.indices, dtype=np.int64)
    if positions.ndim != 2 or positions.shape[0] < 3 or indices.size == 0:
        return 0.0
    if not np.all(np.isfinite(positions)):
        return 0.0

    projected = positions[:, (_ORIGAMI_CHORD_AXIS, _ORIGAMI_SPAN_AXIS)]
    triangles = projected[indices]
    if triangles.size == 0:
        return 0.0

    lo = projected.min(axis=0)
    hi = projected.max(axis=0)
    nx = max(int(math.ceil((hi[0] - lo[0]) / cell_m)), 1)
    nz = max(int(math.ceil((hi[1] - lo[1]) / cell_m)), 1)

    covered = np.zeros((nz, nx), dtype=bool)
    triangle_count = triangles.shape[0]
    for row in range(nz):
        level = lo[1] + (row + 0.5) * cell_m
        crossings = np.full((triangle_count, 3), np.nan, dtype=np.float64)
        for edge in range(3):
            a = triangles[:, edge, :]
            b = triangles[:, (edge + 1) % 3, :]
            dz = b[:, 1] - a[:, 1]
            spans = ((a[:, 1] <= level) & (b[:, 1] > level)) | (
                (b[:, 1] <= level) & (a[:, 1] > level)
            )
            safe = np.where(np.abs(dz) > 0.0, dz, 1.0)
            x = a[:, 0] + (level - a[:, 1]) / safe * (b[:, 0] - a[:, 0])
            crossings[:, edge] = np.where(spans, x, np.nan)
        finite = np.isfinite(crossings)
        x_low = np.where(finite, crossings, np.inf).min(axis=1)
        x_high = np.where(finite, crossings, -np.inf).max(axis=1)
        hit = np.isfinite(x_low) & np.isfinite(x_high)
        if not hit.any():
            continue
        first = np.floor((x_low[hit] - lo[0]) / cell_m).astype(np.int64)
        last = np.floor((x_high[hit] - lo[0]) / cell_m).astype(np.int64)
        np.clip(first, 0, nx - 1, out=first)
        np.clip(last, 0, nx - 1, out=last)
        difference = np.zeros(nx + 1, dtype=np.int32)
        np.add.at(difference, first, 1)
        np.add.at(difference, last + 1, -1)
        covered[row] = np.cumsum(difference[:nx]) > 0
    return float(covered.sum()) * cell_m * cell_m


@dataclass(frozen=True)
class GeometryAudit:
    """折り紙実測幾何を解析翼型幾何と比較した結果."""

    archetype: str
    alpha: float
    span_m: Optional[float]
    #: 折り紙実測: 翼展 (m)
    origami_span_m: float
    #: 折り紙実測: 平面形面積 (m^2). 翼弦-翼展平面への射影シルエット.
    origami_planform_area_m2: float
    #: 折り紙実測: 展開紙面面積 (m^2). 両面と折り返し層を数えた値.
    origami_sheet_area_m2: float
    #: 折り紙実測: 幾何 AR (= span^2 / 平面形面積)
    origami_aspect_ratio: float
    #: 解析翼型: 翼展 (m)
    analysis_span_m: float
    #: 解析翼型: 翼面積 (m^2). 平面形面積.
    analysis_area_m2: float
    #: 解析翼型: 幾何 AR
    analysis_aspect_ratio: float
    #: 解析翼型: 展開紙面面積 (m^2)
    analysis_sheet_area_m2: float
    #: 差分 (折り紙 - 解析): 翼展 (m)
    span_diff_m: float
    #: 差分 (折り紙 - 解析): 平面形面積 (m^2)
    area_diff_m2: float
    #: 差分 (折り紙 - 解析): 幾何 AR
    aspect_diff: float
    #: 差分 (折り紙 - 解析): 展開紙面面積 (m^2)
    sheet_area_diff_m2: float
    #: 差分 (折り紙 - 解析): 翼展 (%)
    span_diff_pct: float
    #: 差分 (折り紙 - 解析): 平面形面積 (%)
    area_diff_pct: float
    #: 差分 (折り紙 - 解析): 幾何 AR (%)
    aspect_diff_pct: float
    #: 差分 (折り紙 - 解析): 展開紙面面積 (%)
    sheet_area_diff_pct: float


def _percent(diff: float, base: float) -> float:
    return (diff / base * 100.0) if base > 0.0 else 0.0


def audit_geometry(
    archetype: str = ARCHETYPE_DART,
    alpha: float = 1.0,
    span_m: Optional[float] = None,
    config: Optional[AircraftConfig] = None,
) -> GeometryAudit:
    """折り紙実測幾何を解析翼型幾何と比較して差分を返す.

    ``span_m`` を指定すると, 折り紙メッシュをその翼展に合わせて
    2 段階スケーリングする (api/routes/origami.py と同一規約).
    指定しなければシート原尺で実測する. ``config`` を省略すると
    ``AircraftConfig()`` の既定値を解析翼型として用いる.

    折り紙形状を物理へ取り込むことはしない (§A-14 の据え置き).
    """
    if archetype not in ARCHETYPES:
        raise ValueError(
            f"unknown archetype '{archetype}'; expected one of {', '.join(ARCHETYPES)}"
        )
    alpha = max(0.0, min(1.0, float(alpha)))

    mesh = build_mesh(archetype, alpha, span_m=span_m)
    orig_span = _mesh_span(mesh)
    orig_area = _planform_area_m2(mesh)
    orig_sheet_area = mesh.surface_area()
    orig_ar = (orig_span * orig_span) / orig_area if orig_area > 0.0 else 0.0

    cfg = config if config is not None else AircraftConfig()
    props = derived_properties(cfg)
    analysis_span = float(props["wingspan_m"])
    analysis_area = float(props["wing_area_m2"])
    analysis_ar = float(props["aspect_ratio"])
    analysis_sheet_area = float(props["sheet_area_m2"])

    span_diff = orig_span - analysis_span
    area_diff = orig_area - analysis_area
    aspect_diff = orig_ar - analysis_ar
    sheet_area_diff = orig_sheet_area - analysis_sheet_area

    return GeometryAudit(
        archetype=archetype,
        alpha=alpha,
        span_m=span_m,
        origami_span_m=orig_span,
        origami_planform_area_m2=orig_area,
        origami_sheet_area_m2=orig_sheet_area,
        origami_aspect_ratio=orig_ar,
        analysis_span_m=analysis_span,
        analysis_area_m2=analysis_area,
        analysis_aspect_ratio=analysis_ar,
        analysis_sheet_area_m2=analysis_sheet_area,
        span_diff_m=span_diff,
        area_diff_m2=area_diff,
        aspect_diff=aspect_diff,
        sheet_area_diff_m2=sheet_area_diff,
        span_diff_pct=_percent(span_diff, analysis_span),
        area_diff_pct=_percent(area_diff, analysis_area),
        aspect_diff_pct=_percent(aspect_diff, analysis_ar),
        sheet_area_diff_pct=_percent(sheet_area_diff, analysis_sheet_area),
    )
