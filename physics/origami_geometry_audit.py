"""折り紙実測幾何と解析翼型幾何の差分監査.

§A-14 を維持: 折り紙メッシュは表示専用であり, 空力・応力・慣性の
物理計算には取り込まない. 本モジュールは, 折り紙実測スパン・翼面積・
幾何AR と解析翼型 (derived_properties) の対応値の差分を関数として
定量的に公開するだけである.

物理側への書き込みや登録は行わない.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from physics.config_schema import AircraftConfig, derived_properties
from physics.origami_domain import ARCHETYPE_DART, ARCHETYPES, build_mesh

#: 翼展を測る境界箱の軸. ビューア (static/js/app.js) と
#: api/routes/origami.py と同一規約: z 軸方向 (spanwise).
_ORIGAMI_SPAN_AXIS = 2


def _mesh_span(mesh) -> float:
    """折り畳みメッシュの翼展 (m). z 軸方向の境界箱幅."""
    lo, hi = mesh.bounds()
    return max(hi[_ORIGAMI_SPAN_AXIS] - lo[_ORIGAMI_SPAN_AXIS], 0.0)


@dataclass(frozen=True)
class GeometryAudit:
    """折り紙実測幾何を解析翼型幾何と比較した結果."""

    archetype: str
    alpha: float
    span_m: Optional[float]
    #: 折り紙実測: 翼展 (m)
    origami_span_m: float
    #: 折り紙実測: 翼面積 (m^2)
    origami_area_m2: float
    #: 折り紙実測: 幾何 AR (= span^2 / area)
    origami_aspect_ratio: float
    #: 解析翼型: 翼展 (m)
    analysis_span_m: float
    #: 解析翼型: 翼面積 (m^2)
    analysis_area_m2: float
    #: 解析翼型: 幾何 AR
    analysis_aspect_ratio: float
    #: 差分 (折り紙 - 解析): 翼展 (m)
    span_diff_m: float
    #: 差分 (折り紙 - 解析): 翼面積 (m^2)
    area_diff_m2: float
    #: 差分 (折り紙 - 解析): 幾何 AR
    aspect_diff: float
    #: 差分 (折り紙 - 解析): 翼展 (%)
    span_diff_pct: float
    #: 差分 (折り紙 - 解析): 翼面積 (%)
    area_diff_pct: float
    #: 差分 (折り紙 - 解析): 幾何 AR (%)
    aspect_diff_pct: float


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
    orig_area = mesh.surface_area()
    orig_ar = (orig_span * orig_span) / orig_area if orig_area > 0.0 else 0.0

    cfg = config if config is not None else AircraftConfig()
    props = derived_properties(cfg)
    analysis_span = float(props["wingspan_m"])
    analysis_area = float(props["wing_area_m2"])
    analysis_ar = float(props["aspect_ratio"])

    span_diff = orig_span - analysis_span
    area_diff = orig_area - analysis_area
    aspect_diff = orig_ar - analysis_ar

    return GeometryAudit(
        archetype=archetype,
        alpha=alpha,
        span_m=span_m,
        origami_span_m=orig_span,
        origami_area_m2=orig_area,
        origami_aspect_ratio=orig_ar,
        analysis_span_m=analysis_span,
        analysis_area_m2=analysis_area,
        analysis_aspect_ratio=analysis_ar,
        span_diff_m=span_diff,
        area_diff_m2=area_diff,
        aspect_diff=aspect_diff,
        span_diff_pct=_percent(span_diff, analysis_span),
        area_diff_pct=_percent(area_diff, analysis_area),
        aspect_diff_pct=_percent(aspect_diff, analysis_ar),
    )
