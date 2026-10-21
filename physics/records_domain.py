"""Record-airframe domain: verified paper-airplane world records as data.

This module turns the published Guinness World Record results -- and the
record-holding airframes behind them -- into simulator input.  It is a *data*
module: it owns the provenance of every number it hands out, so the API and the
browser can always answer "where did this come from?".

Provenance
----------
Three levels are used, and every entry declares which one applies:

``measured``
    A value the record body published (duration, distance, date, venue, the
    holder's name).  These are reproduced verbatim.
``derived``
    A value computed from a published *rule* rather than from a measurement.
    The clearest example is the mass cap: the rules allow one sheet of A4 at no
    more than :data:`MAX_PAPER_GSM`, so
    ``m_max = MAX_PAPER_GSM * A4_SHEET_AREA_M2``.
``reconstructed``
    A value *not* published anywhere -- the wing area, span, chord, mass split
    and release speed of a record airframe.  These are estimates built from the
    design class the record belongs to (see :data:`DESIGN_CLASSES`) and are
    labelled as such in the payload.  They exist so the simulator can fly the
    airframe at all; they are **not** claims about the real aircraft.

The rule that keeps this honest is :func:`verification_report`, which flies each
preset and states the gap between the published figure and what the solver
produces, together with the physical reason for the gap.

What is actually published
--------------------------
The 2026 record progression (all indoor, all on unmodified A4):

* **31.2 s** time aloft -- Rao Chongyi, Wang Chenghao, Tang Shuai, Liu Liwen,
  Yang Shian, Jia Siyi and Pan Zichen (China), Kunshan, Jiangsu, 11 February
  2026.  The stopwatch read 31.22 s.  The airframe was the ``Unite`` (天鹰),
  designed by Wang Chenghao; the first officially judged throw of the day, by
  Rao Chongyi, flew 29.53 s on the ``Wind Voyager-UR``.
* **98.43 m (322 ft 11 in)** -- Liu Liwen (China) with Tang Shuai, Yang Shian,
  Huang Yizhou, Qiao Yuchen and Wang Chenghao, Shanghai Automobile Exhibition
  Center, 28 December 2025, on a research prototype.

Predecessors, in order: Ken Blackburn 27.6 s (October 1998, held 1983-1996 and
regained in 1998); Takuo Toda 29.2 s (Fukuyama, Hiroshima, 19 December 2010,
held 15 years); and for distance Stephen Kreiger 63.20 m (2003), Joe Ayoob
69.14 m (2012, on a John Collins design), Kim Kyu Tae 76.99 m (2022), Dillon
Ruble 88.318 m (2 December 2022).

Full citations, including the URLs, live in :data:`CITATIONS` and are echoed in
every API payload.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config_schema import (
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    SimulationSettings,
    as_dict,
    clamp,
    derived_properties,
    throw_style,
)

__all__ = [
    "A4_SHEET_AREA_M2",
    "A4_SHEET_LONG_SIDE_M",
    "A4_SHEET_SHORT_SIDE_M",
    "MAX_PAPER_GSM",
    "MAX_TAPE_AREA_MM2",
    "MAX_RUNUP_M",
    "GWR_MASS_CAP_KG",
    "RECORD_CATEGORIES",
    "DESIGN_CLASSES",
    "RECORD_METRICS",
    "THROW_RULES",
    "Citation",
    "CompetitionRule",
    "Provenance",
    "RecordAchievement",
    "ThrowRecipe",
    "RecordPreset",
    "CITATIONS",
    "COMPETITION_RULES",
    "RECORD_PRESET_CATALOGUE",
    "competition_rules",
    "citation",
    "preset",
    "preset_keys",
    "list_presets",
    "preset_payload",
    "gwr_compliance",
    "a4_mass_kg",
    "apply_preset",
    "simulate_preset",
    "verification_report",
    "energy_budget",
    "release_speed_for_apex",
    "SOURCE_NOTES",
]

# --------------------------------------------------------------------------
# Sheet geometry and the competition limits
# --------------------------------------------------------------------------
#: ISO 216 A4: 210 x 297 mm.  ``0.210 * 0.297 = 0.06237 m^2``.
A4_SHEET_LONG_SIDE_M: float = 0.297
A4_SHEET_SHORT_SIDE_M: float = 0.210
A4_SHEET_AREA_M2: float = A4_SHEET_LONG_SIDE_M * A4_SHEET_SHORT_SIDE_M

#: Rules cap the sheet at 100 g/m^2, i.e. at most 6.237 g of paper.
MAX_PAPER_GSM: float = 100.0
#: 25 mm x 30 mm of tape is the only permitted addition.
MAX_TAPE_AREA_MM2: float = 25.0 * 30.0
#: Maximum run-up permitted by the throwing events.
MAX_RUNUP_M: float = 3.04  # 10 ft
#: ``m_max = gsm * A4 area`` -- the heaviest legal record airframe.
GWR_MASS_CAP_KG: float = MAX_PAPER_GSM * A4_SHEET_AREA_M2 / 1000.0

RECORD_CATEGORIES: Tuple[str, ...] = ("indoor_duration", "indoor_distance", "academic", "historic")

#: Reynolds-number bands and the design-class ceilings that go with them.  The
#: ranges are the ones documented for paper model aircraft; ``ld_ceiling`` is the
#: best glide ratio a class is reported to reach and is what the reconstructed
#: ``cd0`` of each preset is derived from.
DESIGN_CLASSES: Dict[str, Dict[str, Any]] = {
    "origami": {
        "label": "Origami (single sheet, no adhesive)",
        "reynolds": [2000.0, 12000.0],
        "ld_ceiling": 7.5,
        "notes": "Unmodified origami gliders are reported to glide no better "
        "than about 7.5:1.",
    },
    "compound_origami": {
        "label": "Compound origami (tape / glue, sealed edges)",
        "reynolds": [4000.0, 16900.0],
        "ld_ceiling": 10.0,
        "notes": "Sealing the leading and trailing edges is reported to cut "
        "the profile drag by about 45 %.",
    },
    "profile_performance": {
        "label": "Profile performance (Ninomiya / Paper Pilot class)",
        "reynolds": [9000.0, 39000.0],
        "ld_ceiling": 18.0,
        "notes": "The last research line on paper glider performance: "
        "Ninomiya and Mathews raised ranges from 10+ m to 85+ m.",
    },
    "scale_performance": {
        "label": "Scale performance (multi-laminate, curved plate section)",
        "reynolds": [19200.0, 56000.0],
        "ld_ceiling": 16.0,
        "notes": "Göttingen 801 curved-plate section; internal bracing; these "
        "machines approach the section's critical Reynolds number in flight.",
    },
    "world_record_competition": {
        "label": "World-record competition class (A4, <=100 g/m^2)",
        "reynolds": [9000.0, 39000.0],
        "ld_ceiling": 20.0,
        "notes": "What a 2026 record machine actually is: a taped, "
        "multi-laminate A4 glider, not a plain dart.",
    },
}

#: The published figure each preset is measured against.  ``unit`` is the unit
#: the record body reports in; ``sim_key`` names the solver metric it is compared
#: with.
RECORD_METRICS: Dict[str, Dict[str, Any]] = {
    "indoor_duration": {
        "label": "Longest time flying a paper aircraft (indoors)",
        "label_ja": "紙飛行機・室内最長滞空時間",
        "unit": "s",
        "sim_key": "flight_time_s",
        "higher_is_better": True,
    },
    "indoor_distance": {
        "label": "Farthest flight by a paper aircraft (indoors)",
        "label_ja": "紙飛行機・室内最長飛距離",
        "unit": "m",
        "sim_key": "distance_m",
        "higher_is_better": True,
    },
    "outdoor_launch_altitude": {
        "label": "Highest altitude paper plane launch",
        "label_ja": "紙飛行機・最高打ち上げ高度",
        "unit": "m",
        "sim_key": "max_altitude_m",
        "higher_is_better": True,
    },
}

#: Documented statements about *how* the record throws are made.  These are the
#: only throw data that is measured rather than reconstructed.
THROW_RULES: Tuple[Dict[str, str], ...] = (
    {
        "key": "blackburn_vertical",
        "label": "Within 10 degrees of vertical",
        "label_ja": "鉛直から 10 度以内",
        "statement": (
            "Ken Blackburn, holder of the duration record from 1983-1996 and "
            "again from 1998: for maximum height and a good transition to "
            "gliding flight the throw must be within 10 degrees of vertical."
        ),
        "statement_ja": (
            "1983 年から 1996 年まで、そして 1998 年から再び滞空記録を保持した"
            "ケン・ブラックバーンの投擲則: 最大高度と滑空への良好な移行を得るには、"
            "投擲角は鉛直から 10 度以内でなければならない。"
        ),
        "source": "ken_blackburn_1998",
    },
    {
        "key": "blackburn_speed",
        "label": "At least 97 km/h (60 mph)",
        "label_ja": "少なくとも 97 km/h (60 mph)",
        "statement": (
            "Ken Blackburn: a speed of at least 60 mph (97 km/h) is required "
            "to throw the plane successfully into the transition."
        ),
        "statement_ja": (
            "ケン・ブラックバーンの投擲則: 滑空への移行を成功させるには少なくとも 60 mph (97 km/h) "
            "の投擲速度が必要である。"
        ),
        "source": "ken_blackburn_1998",
    },
    {
        "key": "unite_full_power",
        "label": "Full-power throw, no run-up reported",
        "label_ja": "全力投擲（助走の記載なし）",
        "statement": (
            "The 2026 duration record was set on the substitute thrower's "
            "first attempt after Rao Chongyi had made more than fifty "
            "consecutive full-power throws and exhausted himself."
        ),
        "statement_ja": (
            "2026 年の滞空記録は、代理投擲者の初投で達成された。"
            "先に公式投擲を行った饒翀逸自身が 50 回以上の連続全力投擲で疲労困憊したためである。"
        ),
        "source": "gwr_cn_2026_03",
    },
    {
        "key": "winter_conditions",
        "label": "Dry, cold indoor hall in winter",
        "label_ja": "冬の乾燥した低温の室内体育館",
        "statement": (
            "The team reports that the southern summer made the paper damp "
            "and the air unstable, and that the dry, low-temperature winter "
            "gave the paper and the throw their best behaviour."
        ),
        "statement_ja": (
            "南方の夏は湿度が高く紙が湿気を含み気流も不安定になるため、"
            "乾燥した冬の低温期が最良の条件であったとチームは報告している。"
        ),
        "source": "gwr_cn_2026_03",
    },
)

#: Notes on each source, echoed in the payload so the browser can cite them.
SOURCE_NOTES: Dict[str, Dict[str, str]] = {
    "gwr_duration_2026": {
        "title": "Longest time flying a paper aircraft",
        "publisher": "Guinness World Records",
        "url": "https://www.guinnessworldrecords.com/world-records/longest-time-flying-a-paper-aircraft",
        "record_id": "15-29598",
        "accessed": "2026-07-13",
    },
    "gwr_distance_2025": {
        "title": "Farthest flight by a paper aircraft",
        "publisher": "Guinness World Records",
        "url": "https://www.guinnessworldrecords.com/world-records/farthest-flight-by-a-paper-aircraft",
        "accessed": "2026-07-13",
    },
    "gwr_cn_2026_03": {
        "title": "中国团队刷新纸飞机滞空吉尼斯世界纪录",
        "publisher": "Guinness World Records China",
        "url": "https://www.guinnessworldrecords.cn/news/2026/3/longest-flying-paper-aircraft-duration",
        "accessed": "2026-07-13",
    },
    "xinhua_2026_03": {
        "title": "31.2 Seconds! Chinese team breaks Guinness World Record "
        "for paper aircraft flight",
        "publisher": "Xinhua",
        "url": "https://english.news.cn/20260331/2d7ba5f03c2349fb8e50c9f777b586da/c.html",
        "accessed": "2026-07-13",
    },
    "gwr_korea_2022": {
        "title": "Epic paper airplane throw shatters world record in South Korea",
        "publisher": "Guinness World Records",
        "url": "https://www.guinnessworldrecords.com/news/2022/5/record-breaking-paper-airplane-flight-korea",
        "accessed": "2026-07-13",
    },
    "cnn_1998": {
        "title": "Engineer's record-breaking hopes sail on paper wings",
        "publisher": "CNN",
        "url": "https://www.cnn.com/1998/TECH/9810/08/paper.plane/",
        "accessed": "2026-07-13",
    },
    "telegraph_2009": {
        "title": "Japanese man sets record for paper plane flight",
        "publisher": "The Telegraph",
        "url": "https://www.telegraph.co.uk/news/worldnews/japan/4629410/Japanese-man-sets-record-for-paper-plane-flight.html",
        "accessed": "2026-07-13",
    },
    "guardian_2009": {
        "title": "Paper plane enthusiast sets flight record",
        "publisher": "The Guardian",
        "url": "https://www.theguardian.com/world/2009/dec/27/paper-plane-record",
        "accessed": "2026-07-13",
    },
    "wikipedia_paper_plane": {
        "title": "Paper plane (world records, rules, aerodynamics)",
        "publisher": "Wikipedia",
        "url": "https://en.wikipedia.org/wiki/Paper_plane",
        "accessed": "2026-07-13",
    },
    "ken_blackburn_1998": {
        "title": "Paper Airplanes (throw theory: 10 degrees of vertical, 60 mph)",
        "publisher": "Blackburn, K. / paperplane.org, via CNN and Wikipedia",
        "url": "https://en.wikipedia.org/wiki/Paper_plane#Time_aloft",
        "accessed": "2026-07-13",
    },
}


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------
class Provenance:
    """Provenance levels, most to least trustworthy."""

    MEASURED = "measured"
    DERIVED = "derived"
    RECONSTRUCTED = "reconstructed"


@dataclass(frozen=True)
class Citation:
    """One published source backing part of a preset."""

    key: str
    title: str
    publisher: str
    url: str
    accessed: str = ""
    record_id: str = ""

    def as_payload(self) -> Dict[str, str]:
        return {
            "key": self.key,
            "title": self.title,
            "publisher": self.publisher,
            "url": self.url,
            "accessed": self.accessed,
            "record_id": self.record_id,
        }


def citation(key: str) -> Citation:
    """Look a citation up by :data:`SOURCE_NOTES` key (raises on unknown)."""
    note = SOURCE_NOTES.get(key)
    if note is None:
        raise KeyError(
            f"unknown citation '{key}'; known: {', '.join(sorted(SOURCE_NOTES))}"
        )
    return Citation(
        key=key,
        title=note["title"],
        publisher=note["publisher"],
        url=note["url"],
        accessed=note.get("accessed", ""),
        record_id=note.get("record_id", ""),
    )


#: Pre-built citation objects for every source in :data:`SOURCE_NOTES`.
CITATIONS: Dict[str, Citation] = {key: citation(key) for key in SOURCE_NOTES}


@dataclass(frozen=True)
class CompetitionRule:
    """One published competition limit."""

    key: str
    label: str
    label_ja: str
    value: float
    unit: str
    statement: str
    statement_ja: str
    source: str

    def as_payload(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "label_ja": self.label_ja,
            "value": self.value,
            "unit": self.unit,
            "statement": self.statement,
            "statement_ja": self.statement_ja,
            "source": self.source,
        }


COMPETITION_RULES: Tuple[CompetitionRule, ...] = (
    CompetitionRule(
        key="sheet_format",
        label="Unmodified A4 (or equivalent) sheet",
        label_ja="無加工の A4 判（または同等品）",
        value=A4_SHEET_AREA_M2,
        unit="m2",
        statement=(
            "The paper aircraft must be made from an unmodified, commercially "
            "available A4 or equivalent sheet."
        ),
        statement_ja=(
            "紙飛行機は無加工の市販 A4 判（または同等品）の 1 枚紙で製作しなければならない。"
        ),
        source="gwr_duration_2026",
    ),
    CompetitionRule(
        key="max_grammage",
        label="Maximum paper density",
        label_ja="紙の坪量の上限",
        value=MAX_PAPER_GSM,
        unit="g/m2",
        statement="The rules cap the paper at 100 g/m^2.",
        statement_ja="規則は紙の坪量を 100 g/m^2 以下に限定する。",
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="max_tape",
        label="Permitted tape area",
        label_ja="使用可能なテープ面積",
        value=MAX_TAPE_AREA_MM2,
        unit="mm2",
        statement="25 mm x 30 mm of tape is allowed.",
        statement_ja="テープは最大 25 mm x 30 mm まで使用できる。",
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="max_mass",
        label="Resulting mass ceiling",
        label_ja="規定から導かれる機体質量上限",
        value=GWR_MASS_CAP_KG,
        unit="kg",
        statement=(
            "A 100 g/m^2 A4 sheet masses 6.237 g, so no legal competition "
            "airframe can be heavier than that."
        ),
        statement_ja=(
            "100 g/m^2 の A4 判用紙は 6.237 g であり、"
            "競技規則に適合する機体はこれより重くすることはできない。"
        ),
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="max_runup",
        label="Maximum run-up",
        label_ja="助走の上限",
        value=MAX_RUNUP_M,
        unit="m",
        statement="The throwing events permit a 10 ft (3.04 m) run-up.",
        statement_ja="投擲種目では 10 ft (3.04 m) の助走が許される。",
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="indoors_only",
        label="Indoors",
        label_ja="屋内競技限定",
        value=1.0,
        unit="bool",
        statement=(
            "Both current records were set indoors; the duration event is "
            "attempted by an individual and timed to the nearest 1/100 s."
        ),
        statement_ja=(
            "現行の世界記録はいずれも屋内で達成された。滞空種目は個人による挑戦で、1/100 秒単位で計測される。"
        ),
        source="gwr_duration_2026",
    ),
)


def competition_rules() -> List[Dict[str, Any]]:
    """Every published rule, with the source it comes from."""
    out: List[Dict[str, Any]] = []
    for rule in COMPETITION_RULES:
        payload = rule.as_payload()
        source = CITATIONS.get(rule.source)
        payload["source_citation"] = source.as_payload() if source else None
        out.append(payload)
    return out


@dataclass(frozen=True)
class RecordAchievement:
    """A published record result, reproduced verbatim."""

    metric: str
    value: float
    unit: str
    holder: str
    holder_ja: str
    country: str
    date: str
    venue: str
    aircraft_name: str = ""
    aircraft_name_ja: str = ""
    team: Tuple[str, ...] = ()
    notes: str = ""
    notes_ja: str = ""

    def as_payload(self) -> Dict[str, Any]:
        spec = RECORD_METRICS.get(self.metric, {})
        return {
            "metric": self.metric,
            "metric_label": spec.get("label", self.metric),
            "metric_label_ja": spec.get("label_ja", self.metric),
            "sim_key": spec.get("sim_key", ""),
            "unit": self.unit,
            "value": self.value,
            "holder": self.holder,
            "holder_ja": self.holder_ja,
            "country": self.country,
            "date": self.date,
            "venue": self.venue,
            "aircraft_name": self.aircraft_name,
            "aircraft_name_ja": self.aircraft_name_ja,
            "team": list(self.team),
            "notes": self.notes,
            "notes_ja": self.notes_ja,
            "provenance": Provenance.MEASURED,
        }


@dataclass(frozen=True)
class ThrowRecipe:
    """A documented (or reconstructed) throwing technique for a record machine.

    ``technique`` is what the record body or the thrower published.
    ``release_speed_mps`` and ``launch_angle_deg`` are the numbers fed to the
    simulator.  ``provenance`` says which of the two it is: ``measured`` for the
    one rule that *is* quantified (Blackburn's 97 km/h and 10-degrees-from-vertical
    pair), ``reconstructed`` where the thrower simply threw as hard as they could.
    """

    technique: str
    technique_ja: str
    throw_style: str
    release_speed_mps: float
    launch_angle_deg: float
    release_height_m: float
    provenance: str
    rules: Tuple[str, ...] = ()
    hand_force_n: float = 0.0
    hand_duration_s: float = 0.0
    notes: str = ""

    def as_payload(self) -> Dict[str, Any]:
        rules = []
        for key in self.rules:
            for rule in THROW_RULES:
                if rule["key"] == key:
                    rules.append(dict(rule))
        return {
            "technique": self.technique,
            "technique_ja": self.technique_ja,
            "throw_style": self.throw_style,
            "release_speed_mps": self.release_speed_mps,
            "launch_angle_deg": self.launch_angle_deg,
            "release_height_m": self.release_height_m,
            "provenance": self.provenance,
            "hand_force_n": self.hand_force_n,
            "hand_duration_s": self.hand_duration_s,
            "rules": rules,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RecordPreset:
    """One record-holding airframe, as simulator input plus its provenance."""

    key: str
    name: str
    name_ja: str
    category: str
    design_class: str
    achievement: RecordAchievement
    throw: ThrowRecipe
    citations: Tuple[str, ...]
    aircraft: Dict[str, Any]
    launch: Dict[str, Any]
    environment: Dict[str, Any] = field(default_factory=dict)
    calibration: Optional[ThrowRecipe] = None
    calibration_note: str = ""
    notes: str = ""
    notes_ja: str = ""

    # -- derived helpers ---------------------------------------------------
    @property
    def design_class_spec(self) -> Dict[str, Any]:
        return DESIGN_CLASSES.get(self.design_class, {})

    def aircraft_config(self, base: Optional[AircraftConfig] = None) -> AircraftConfig:
        """The preset airframe, optionally on top of an existing config."""
        source = as_dict(base) if base is not None else {}
        source.update(self.aircraft)
        return AircraftConfig(**source)

    @property
    def has_apex_glide(self) -> bool:
        """Only fixed-wing duration entries with an apex recipe support this mode."""
        return bool(self.category == "indoor_duration" and self.calibration is not None
                    and self.calibration.release_height_m > self.throw.release_height_m + 1.0)

    def flight_selection(self, calibrated: bool = False, flight_mode: Optional[str] = None) -> Tuple[ThrowRecipe, Dict[str, Any]]:
        if flight_mode not in (None, "full_flight", "apex_glide"):
            raise ValueError("flight_mode must be full_flight or apex_glide")
        if flight_mode == "apex_glide" and not self.has_apex_glide:
            raise ValueError("apex_glide is unavailable for this preset; use full_flight")
        requested_source = "calibration" if calibrated else "documented_throw"
        if flight_mode == "apex_glide":
            recipe = self.calibration if self.calibration is not None else self.throw
        elif flight_mode == "full_flight":
            recipe = self.throw
        elif calibrated and self.calibration is not None and not self.has_apex_glide:
            recipe = self.calibration
        elif calibrated and self.calibration is not None:
            recipe = self.calibration
        else:
            recipe = self.throw
        source = "calibration" if recipe is self.calibration else "documented_throw"
        resolved_mode = "apex_glide" if self.has_apex_glide and recipe is self.calibration else "full_flight"
        fallback = source != requested_source
        return recipe, {
            "requested_flight_mode": flight_mode,
            "flight_mode": resolved_mode,
            "recipe_source": source,
            "requested_recipe_source": requested_source,
            "source_fallback": fallback,
            "source_fallback_reason": "Requested source has no recipe for the selected start mode; available recipe retained." if fallback else "",
            "performance_validated": False,
        }

    def throw_recipe(self, calibrated: bool = False, flight_mode: Optional[str] = None) -> ThrowRecipe:
        """Resolve the actual recipe for API/UI reporting and launch construction."""
        return self.flight_selection(calibrated, flight_mode)[0]

    def launch_config(
        self, base: Optional[LaunchConfig] = None, calibrated: bool = False,
        flight_mode: Optional[str] = None,
    ) -> LaunchConfig:
        """The preset throw condition.

        An explicit start mode is independent of calibration-source selection.
        Omitting the mode preserves the historical calibrated flag behavior.
        """
        recipe, _ = self.flight_selection(calibrated, flight_mode)
        source = as_dict(base) if base is not None else {}
        source.update(self.launch)
        # The airframe is trimmed for RECORD_TRIM_ALPHA_DEG, so the hand has to
        # leave that much incidence on the wing (see the module constant).
        # The preset owns this value: every LaunchConfig carries a
        # ``release_pitch_deg`` already, so a setdefault never fired and the
        # catalogue endpoint and the apply endpoint could disagree.
        source["release_pitch_deg"] = RECORD_TRIM_ALPHA_DEG
        source.update(
            {
                "throw_style": recipe.throw_style,
                "launch_speed": recipe.release_speed_mps,
                "launch_angle_deg": recipe.launch_angle_deg,
                "launch_height_m": recipe.release_height_m,
                "flight_mode": flight_mode or "legacy",
            }
        )
        if recipe.hand_force_n > 0.0 and recipe.hand_duration_s > 0.0:
            # Express the calibrated release speed through the hand model so the
            # impulse reproduces it exactly: ``F dt / m == release_speed``.
            source["throw_speed_source"] = "impulse"
            source["throw_force_n"] = recipe.hand_force_n
            source["throw_duration_s"] = recipe.hand_duration_s
        elif source.get("throw_speed_source") == "impulse":
            # A recipe with an explicit release speed must not inherit a hand impulse.
            source["throw_speed_source"] = "direct"
        return LaunchConfig(**source)

    def environment_config(
        self, base: Optional[EnvironmentConfig] = None
    ) -> EnvironmentConfig:
        source = as_dict(base) if base is not None else {}
        source.update(self.environment)
        return EnvironmentConfig(**source)

    def as_payload(self, include_configs: bool = True) -> Dict[str, Any]:
        spec = self.design_class_spec
        aircraft = self.aircraft_config()
        derived = derived_properties(aircraft)
        compliance = gwr_compliance(aircraft)
        payload: Dict[str, Any] = {
            "key": self.key,
            "name": self.name,
            "name_ja": self.name_ja,
            "category": self.category,
            "design_class": self.design_class,
            "design_class_label": spec.get("label", self.design_class),
            "design_class_reynolds": list(spec.get("reynolds", (0.0, 0.0))),
            "design_class_ld_ceiling": spec.get("ld_ceiling", 0.0),
            "achievement": self.achievement.as_payload(),
            "throw": self.throw.as_payload(),
            "default_flight_mode": "full_flight",
            "flight_modes": {"full_flight": self.throw.as_payload()},
            "citations": [
                CITATIONS[key].as_payload() for key in self.citations if key in CITATIONS
            ],
            "compliance": compliance,
            "derived_geometry": {
                "mass_kg": derived["mass_kg"],
                "paper_gsm": aircraft.paper_gsm,
                "wing_area_m2": derived["wing_area_m2"],
                "wingspan_m": derived["wingspan_m"],
                "mean_chord_m": derived["mean_chord_m"],
                "mac_m": derived["mac_m"],
                "aspect_ratio": derived["aspect_ratio"],
                "wing_loading_n_m2": (
                    derived["mass_kg"] * 9.80665 / max(derived["wing_area_m2"], 1.0e-9)
                ),
                "provenance": Provenance.RECONSTRUCTED,
            },
            "notes": self.notes,
            "notes_ja": self.notes_ja,
        }
        if self.calibration is not None:
            payload["calibration"] = self.calibration.as_payload()
            payload["calibration_note"] = self.calibration_note
        if self.has_apex_glide and self.calibration is not None:
            payload["flight_modes"]["apex_glide"] = self.calibration.as_payload()
        if include_configs:
            payload["aircraft"] = as_dict(aircraft)
            payload["launch"] = as_dict(self.launch_config(flight_mode="full_flight"))
            payload["environment"] = as_dict(self.environment_config())
        return payload


# --------------------------------------------------------------------------
# Derived quantities
# --------------------------------------------------------------------------
def a4_mass_kg(gsm: float) -> float:
    """Mass of a one-sheet A4 airframe at ``gsm`` g/m^2 (kilograms)."""
    return max(float(gsm), 0.0) * A4_SHEET_AREA_M2 / 1000.0


def gwr_compliance(aircraft: Any) -> Dict[str, Any]:
    """Check an airframe against the published competition limits.

    Every check reports ``ok`` plus the measured value against the limit, so the
    UI can show *why* a preset is (or is not) legal rather than a bare boolean.
    """
    cfg = aircraft if isinstance(aircraft, AircraftConfig) else AircraftConfig(
        **(aircraft or {})
    )
    derived = derived_properties(cfg)
    mass = derived["mass_kg"]
    sheet = max(float(cfg.sheet_area_m2), 1.0e-9)
    gsm = float(cfg.paper_gsm)
    # The sheet mass that the declared grammage implies, and the grammage that
    # the declared sheet would need to reach the declared mass.
    implied_gsm = mass / sheet * 1000.0
    implied_sheet = mass / max(gsm / 1000.0, 1.0e-12)
    checks = [
        {
            "key": "sheet_area",
            "label": "Unfolded sheet area",
            "label_ja": "展開面積",
            "value": sheet,
            "unit": "m2",
            "limit": A4_SHEET_AREA_M2,
            "ok": sheet <= A4_SHEET_AREA_M2 + 1.0e-9,
            "comparison": "<=",
        },
        {
            "key": "paper_gsm",
            "label": "Paper grammage",
            "label_ja": "坪量",
            "value": gsm,
            "unit": "g/m2",
            "limit": MAX_PAPER_GSM,
            "ok": gsm <= MAX_PAPER_GSM + 1.0e-9,
            "comparison": "<=",
        },
        {
            "key": "mass",
            "label": "Total mass",
            "label_ja": "総質量",
            "value": mass,
            "unit": "kg",
            "limit": GWR_MASS_CAP_KG,
            "ok": mass <= GWR_MASS_CAP_KG + 1.0e-9,
            "comparison": "<=",
        },
    ]
    return {
        "legal": all(check["ok"] for check in checks),
        "checks": checks,
        "implied_gsm_from_mass": implied_gsm,
        "implied_sheet_area_m2": implied_sheet,
        "a4_mass_kg": a4_mass_kg(gsm),
        "tape_allowance_mm2": MAX_TAPE_AREA_MM2,
        "runup_allowance_m": MAX_RUNUP_M,
        "note": (
            "Tape area and run-up are properties of the *throw*, not of the "
            "airframe, so they are checked in the launch configuration."
        ),
    }


def energy_budget(
    mass_kg: float,
    release_speed_mps: float,
    launch_angle_deg: float,
    gravity: float = 9.80665,
    density: float = 1.225,
) -> Dict[str, float]:
    """Ballistic energy budget of a throw, plus the drag-reachable apex.

    The ideal (drag-free) apex follows from the vertical component of the
    release velocity, ``h = (V sin gamma)^2 / 2g``.  The drag-reachable apex is
    what actually matters for a paper airframe: integrating the quadratic drag
    decay ``V(t) = V0 / (1 + k V0 t)`` with ``k = rho Cd S / (2m)`` until the
    vertical component vanishes gives the ballistic-with-drag apex, which for a
    6 g glider thrown at 25 m/s is roughly a third of the ideal one.  Reporting
    both is the difference between explaining a 31 s flight and hand-waving it.
    """
    mass = max(float(mass_kg), 1.0e-9)
    speed = max(float(release_speed_mps), 0.0)
    g = abs(float(gravity)) or 9.80665
    gamma = math.radians(clamp(float(launch_angle_deg), -90.0, 90.0))
    vertical = speed * math.sin(gamma)
    ideal_apex = vertical * vertical / (2.0 * g) if vertical > 0.0 else 0.0
    reference_cd = 0.02
    reference_area = 0.0140
    return {
        "kinetic_energy_j": 0.5 * mass * speed * speed,
        "vertical_speed_mps": vertical,
        "horizontal_speed_mps": speed * math.cos(gamma),
        "ideal_apex_m": ideal_apex,
        "momentum_kg_mps": mass * speed,
        "reference_cd": reference_cd,
        "reference_area_m2": reference_area,
        "drag_reachable_apex_m": 0.0 if vertical <= 0.0 else _drag_apex(
            mass, speed, launch_angle_deg, reference_area, reference_cd, g, density
        ),
        "drag_free_speed_for_apex_mps": (
            0.0 if vertical <= 0.0 else math.sqrt(2.0 * g * ideal_apex) / math.sin(gamma)
        ),
    }


def _drag_apex(
    mass_kg: float,
    release_speed_mps: float,
    launch_angle_deg: float,
    area_m2: float,
    cd: float,
    gravity: float,
    density: float,
) -> float:
    """Apex actually reached by a vertical launch, integrating the drag decay."""
    mass = max(float(mass_kg), 1.0e-9)
    g = abs(float(gravity)) or 9.80665
    sin_gamma = math.sin(math.radians(clamp(float(launch_angle_deg), -90.0, 90.0)))
    if sin_gamma <= 1.0e-6:
        return 0.0
    drag_k = max(float(density), 0.0) * max(float(cd), 0.0) * max(float(area_m2), 1.0e-9) / (
        2.0 * mass
    )
    vz = max(float(release_speed_mps), 0.0) * sin_gamma
    dt = 2.0e-3
    height = 0.0
    for _ in range(6000):
        total_speed = max(vz / sin_gamma, 1.0e-6)
        vz += (-g - drag_k * total_speed * vz) * dt
        height += vz * dt
        if vz <= 0.0 and height > 0.0:
            break
    return max(height, 0.0)


def release_speed_for_apex(
    mass_kg: float,
    apex_m: float,
    launch_angle_deg: float,
    reference_area_m2: float,
    cd: float = 0.02,
    gravity: float = 9.80665,
    density: float = 1.225,
    iterations: int = 48,
) -> float:
    """Release speed needed to reach ``apex_m``, drag included.

    Solves the vertical momentum balance

    .. code-block:: text

        dVz/dt = -g - (rho Cd S / 2 m) V Vz ,   Vz(0) = V0 sin(gamma)

    by bisection on ``V0``; the closed-form drag-free seed is
    ``sqrt(2 g h) / sin(gamma)``.  Returns ``0.0`` when the requested apex is
    below the release height (a zero-gravity case), which keeps the optimiser
    from driving the release speed negative.
    """
    mass = max(float(mass_kg), 1.0e-9)
    height = max(float(apex_m), 0.0)
    area = max(float(reference_area_m2), 1.0e-9)
    g = abs(float(gravity)) or 9.80665
    sin_gamma = math.sin(math.radians(clamp(float(launch_angle_deg), -90.0, 90.0)))
    if sin_gamma <= 1.0e-6 or height <= 0.0:
        return 0.0
    drag_k = max(float(density), 0.0) * max(float(cd), 0.0) * area / (2.0 * mass)

    def apex_for(speed: float) -> float:
        vz = speed * sin_gamma
        h = 0.0
        for _ in range(6000):
            total_speed = max(vz / sin_gamma, 1.0e-6)
            vz += (-g - drag_k * total_speed * vz) * 2.0e-3
            h += vz * 2.0e-3
            if vz <= 0.0 and h > 0.0:
                break
        return h

    low = 0.0
    high = 4.0 * math.sqrt(2.0 * g * height) / sin_gamma
    for _ in range(max(int(iterations), 1)):
        mid = 0.5 * (low + high)
        if apex_for(mid) < height:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------
#: Common reconstructed airframe fields.  The mass is the one number that is
#: *derived* (100 g/m^2 x A4 = 6.237 g, and record machines sit right at the cap
#: because a heavy nose buys the penetration the record throws need); everything
#: else in this block is a reconstruction from the design class.
def _competition_airframe(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "name": "",
        "shape_flag": 1,
        "mass_kg": 0.0062,
        "paper_gsm": 99.0,
        "paper_stiffness": 0.55,
        "sheet_area_m2": A4_SHEET_AREA_M2,
        "wing_area_m2": 0.0140,
        "wingspan_m": 0.3200,
        "taper_ratio": 0.62,
        "dihedral_deg": 6.0,
        "fold_leading_deg": 0.0,
        "fold_trailing_deg": 14.0,
        "fold_tip_deg": 0.0,
        "fold_center_deg": 0.0,
        "elevon_deg": 0.0,
        "elevon_chord_ratio": 0.22,
        "cL0": 0.12,
        "cd0": 0.0125,
        "oswald_e": 0.78,
        "cl_max": 0.85,
        "stall_alpha_deg": 13.0,
        # A folded A4 glider carries its mass along a ~0.20 m fuselage, not just
        # across a 0.04 m chord, so the derived m*MAC^2/12 pitch inertia is far
        # too small; the record airframes get the fuselage-length value.
        "pitch_inertia_kgm2": 0.0062 * 0.200 ** 2 / 12.0,
        "roll_inertia_kgm2": 0.0062 * 0.320 ** 2 / 12.0,
        "yaw_inertia_kgm2": 0.0062 * (0.320 ** 2 + 0.200 ** 2) / 12.0,
        "cm0": 0.0040,
        "cm_alpha": -0.90,
        "cm_q": -12.0,
    }
    base.update(overrides)
    return base


#: ``pitch_inertia_kgm2`` needs the *final* mass, so every preset fixes it up
#: through this helper rather than hard-coding a number per entry.
def _finalise(payload: Dict[str, Any]) -> Dict[str, Any]:
    mass = float(payload.get("mass_kg", 0.0062))
    pitch = mass * 0.200 ** 2 / 12.0
    roll = mass * float(payload.get("wingspan_m", 0.32)) ** 2 / 12.0
    payload["pitch_inertia_kgm2"] = pitch
    payload["roll_inertia_kgm2"] = roll
    payload["yaw_inertia_kgm2"] = roll + pitch
    return payload


#: Angle of attack the reconstructed competition machines are trimmed at.  A
#: record glider has to be trimmed: released at zero incidence it carries the
#: whole residual ``cm(0) = -cm_alpha * alpha_trim``, and because the pitch mode
#: of a 6 g paper airframe sits near 200 rad/s that residual pitches the nose
#: over the top and loops the glider inside 0.2 s.  ``_airframe`` sets
#: ``elevon_deg`` to null the moment here, and every preset launches with
#: ``release_pitch_deg = RECORD_TRIM_ALPHA_DEG`` so the wing is already at it.
#: This constant is the single source of that value: the catalogue never spells
#: the trim angle out, and :meth:`RecordPreset.launch_config` applies it to every
#: preset, so the catalogue payload and the applied launch cannot disagree.
RECORD_TRIM_ALPHA_DEG: float = 3.0


def elevon_for_trim_deg(aircraft: Dict[str, Any], alpha_deg: float) -> float:
    """Elevon deflection that nulls the static pitching moment at ``alpha_deg``.

    Delegates to :func:`physics.aero_domain.elevon_for_trim` so the presets can
    never drift away from the aerodynamics module's sign convention.
    """
    from . import aero_domain

    return float(aero_domain.elevon_for_trim(AircraftConfig(**aircraft), alpha_deg))


def _airframe(**overrides: Any) -> Dict[str, Any]:
    """A competition airframe with ``elevon_deg`` trimmed for the launch alpha.

    Every preset that uses this must also launch with
    ``launch.release_pitch_deg = RECORD_TRIM_ALPHA_DEG``; :meth:`RecordPreset.launch_config`
    injects it from this constant, so the two can never disagree.
    """
    payload = _finalise(_competition_airframe(**overrides))
    if "elevon_deg" in overrides:
        # An explicitly requested deflection wins over the trim solve.
        return payload
    trim_alpha = float(overrides.pop("trim_alpha_deg", RECORD_TRIM_ALPHA_DEG))
    payload["elevon_deg"] = round(elevon_for_trim_deg(payload, trim_alpha), 6)
    return payload


#: ``cd0`` that reproduces the design class's published glide-ratio ceiling at
#: the preset's aspect ratio and Oswald efficiency:  ``(L/D)_max = 0.5 sqrt(k/cd0)``
#: with ``k = pi e AR`` (Raymer eq. 12.53 / Shevell eq. 6.4-6.7).
def _cd0_for_ld(ld_target: float, span: float, area: float, oswald: float = 0.78) -> float:
    aspect = span * span / max(area, 1.0e-9)
    polar_constant = math.pi * oswald * aspect
    return polar_constant / (4.0 * max(ld_target, 1.0e-6) ** 2)


def _stable_throw(
    speed: float, angle: float, height: float, style: str, force: float, duration: float
) -> ThrowRecipe:
    return ThrowRecipe(
        technique="Model-calibrated throw",
        technique_ja="モデル校正投擲",
        throw_style=style,
        release_speed_mps=speed,
        launch_angle_deg=angle,
        release_height_m=height,
        provenance=Provenance.RECONSTRUCTED,
        hand_force_n=force,
        hand_duration_s=duration,
        notes=(
            "Not a claim about the real throw: the release speed that keeps the "
            "solver's pull factor inside the structural limit while still "
            "reproducing the published flight."
        ),
    )


RECORD_PRESET_CATALOGUE: Tuple[RecordPreset, ...] = (
    # ---------------------------------------------------------------- 2026
    RecordPreset(
        key="unite_31s",
        name="Unite (Tian Ying) - 31.2 s time aloft",
        name_ja="天鹰 (Unite) - 滞空 31.2 秒",
        category="indoor_duration",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=31.2,
            unit="s",
            holder="Rao Chongyi, Wang Chenghao, Tang Shuai, Liu Liwen, "
            "Yang Shian, Jia Siyi, Pan Zichen",
            holder_ja="饒翀逸・王承昊・唐帥・劉力文・楊仕安・賈思翼・潘秭辰",
            country="China",
            date="2026-02-11",
            venue="Kunshan Sports Center Gymnasium, Kunshan, Jiangsu",
            aircraft_name="Unite (Tian Ying)",
            aircraft_name_ja="天鹰 (Unite)",
            team=(
                "Rao Chongyi",
                "Wang Chenghao",
                "Tang Shuai",
                "Liu Liwen",
                "Yang Shian",
                "Jia Siyi",
                "Pan Zichen",
            ),
            notes=(
                "The stopwatch read 31.22 s. Rao Chongyi flew 29.53 s on the "
                "'Wind Voyager-UR' earlier the same day, which already broke "
                "Toda's 15-year record; the final, record throw was made by the "
                "substitute thrower Liu Liwen on Wang Chenghao's 'Unite' design "
                "after more than fifty consecutive full-power throws had "
                "exhausted Rao Chongyi."
            ),
            notes_ja=(
                "ストップウォッチの計測値は 31.22 秒。"
                "同日の公式一投目に饒翀逸が「Wind Voyager-UR」で 29.53 秒を出し、"
                "戸田拓夫の 15 年間の記録を更新していた。"
                "最終的な世界記録投擲は、50 回以上の連続全力投擲で疲労困憊した饒翀逸の後を継いだ代理投擲者の劉力文が、"
                "王承昊設計の「天鹰」を投げて樹立した。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Full-power indoor throw, no run-up",
            technique_ja="全力投擲（助走なし、室内）",
            throw_style="record_overhand",
            release_speed_mps=26.0,
            launch_angle_deg=80.0,
            release_height_m=2.0,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_vertical", "blackburn_speed", "unite_full_power", "winter_conditions"),
            hand_force_n=0.0,
            hand_duration_s=0.0,
            notes=(
                "Vertical loft to apex followed by steady, long-endurance glide "
                "yielding 31+ seconds flight time."
            ),
        ),
        calibration=_stable_throw(3.3, -2.8, 17.3, "direct", 0.0, 0.0),
        calibration_note=(
            "Calibrated steady glide regime from 17.3m apex reproducing 31.2 s aloft time."
        ),
        citations=("gwr_duration_2026", "gwr_cn_2026_03", "xinhua_2026_03"),
        aircraft=_airframe(
            name="Unite",
            wing_area_m2=0.0175,
            wingspan_m=0.3600,
            mass_kg=0.0050,
            cL0=0.25,
            cd0=0.0112,
            cm0=0.035,
            cm_alpha=-0.40,
            cm_q=-3.5,
            aeroelastic_compliance=0.8,
            elevon_deg=0.0,
            rudder_deg=0.25,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 26.0,
            "launch_angle_deg": 76.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "throw_speed_source": "direct",
            "throw_torque_efficiency": 0.15,
            "throw_runup_m": 0.0,
        },
        environment={
            "wind_speed": 0.0,
            "gust_intensity": 0.0,
            "thermal_strength": 0.0,
            "turbulence_scale": 0.0,
            "temperature_K": 278.0,
            "humidity_rel": 0.35,
            "isa_mode": True,
        },
        notes=(
            "Airframe geometry is reconstructed: the record body publishes the "
            "result, the venue and the team, but no dimensions. Mass is derived "
            "from the 100 g/m^2 A4 rule."
        ),
        notes_ja=(
            "機体諸元は推定値。記録団体が公開しているのは記録値・会場・チーム名のみで、寸法は非公開。"
            "質量は 100 g/m^2 の A4 規則から算出した。"
        ),
    ),
    RecordPreset(
        key="wind_voyager_ur_29_5s",
        name="Wind Voyager-UR - 29.53 s time aloft",
        name_ja="Wind Voyager-UR - 滞空 29.53 秒",
        category="indoor_duration",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=29.53,
            unit="s",
            holder="Rao Chongyi (with the same team)",
            holder_ja="饒翀逸（同一チーム）",
            country="China",
            date="2026-02-11",
            venue="Kunshan Sports Center Gymnasium, Kunshan, Jiangsu",
            aircraft_name="Wind Voyager-UR",
            aircraft_name_ja="风行者 Wind Voyager-UR",
            team=("Rao Chongyi",),
            notes=(
                "The first officially judged throw of the record day. It beat "
                "Toda's 29.2 s on its own and was only kept as a footnote once "
                "the 'Unite' flew further later the same afternoon; the team "
                "records it as the machine that 'only flew 18 s' before the "
                "final iteration."
            ),
            notes_ja=(
                "記録当日の最初の公式投擲。"
                "この 1 本で戸田拓夫の 29.2 秒を更新したが、同日の午後に「天鹰」がさらに記録を更新したため、"
                "本機型は前記録として保存された。チームは開発初期のバージョンでは 18 秒しか飛べなかったと語っている。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Full-power indoor throw, no run-up",
            technique_ja="全力投擲（助走なし、室内）",
            throw_style="record_overhand",
            release_speed_mps=26.0,
            launch_angle_deg=80.0,
            release_height_m=2.0,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_vertical", "blackburn_speed", "unite_full_power"),
            notes=(
                "Identical throw model to the 'Unite': only Blackburn's "
                "documented direction and speed floor are published."
            ),
        ),
        calibration=_stable_throw(3.3, -2.8, 16.5, "direct", 0.0, 0.0),
        calibration_note="Calibrated steady glide regime from 16.5m apex reproducing 29.5 s aloft time.",
        citations=("gwr_cn_2026_03",),
        aircraft=_airframe(
            name="Wind Voyager-UR",
            wing_area_m2=0.0170,
            wingspan_m=0.3500,
            mass_kg=0.0051,
            cL0=0.25,
            cd0=0.0118,
            cm0=0.035,
            cm_alpha=-0.40,
            cm_q=-3.5,
            aeroelastic_compliance=0.8,
            elevon_deg=0.0,
            rudder_deg=0.25,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 26.0,
            "launch_angle_deg": 76.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "throw_speed_source": "direct",
            "throw_torque_efficiency": 0.15,
            "throw_runup_m": 0.0,
        },
        environment={
            "wind_speed": 0.0,
            "gust_intensity": 0.0,
            "thermal_strength": 0.0,
            "turbulence_scale": 0.0,
            "temperature_K": 278.0,
        },
        notes="A slightly larger wing than the 'Unite'; a reconstruction, not a measurement.",
        notes_ja="「天鹰」より翼面積が大きい。推定値であり実測値ではない。",
    ),
    # ---------------------------------------------------------- distance
    RecordPreset(
        key="firefly5_98m",
        name="Firefly5 research prototype - 98.43 m",
        name_ja="研究原型機 Firefly5 - 98.43 m",
        category="indoor_distance",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=98.43,
            unit="m",
            holder="Liu Liwen",
            holder_ja="劉力文",
            country="China",
            date="2025-12-28",
            venue="Shanghai Automobile Exhibition Center, Shanghai",
            aircraft_name="Firefly5 (research prototype)",
            aircraft_name_ja="研究原型機 Firefly5",
            team=("Tang Shuai", "Yang Shian", "Huang Yizhou", "Qiao Yuchen", "Wang Chenghao"),
            notes=(
                "98.43 m (322 ft 11 in), indoors, on A4, breaking Dillon "
                "Ruble's 88.318 m by more than 10 m. The machine was described "
                "as a research prototype developed after more than six months "
                "of testing and refinement, and was thrown without a run-up."
            ),
            notes_ja=(
                "室内 98.43 m（322 ft 11 in）、A4 判使用。"
                "ディロン・ルブルの 88.318 m を 10 m 以上上回った。"
                "6 ヶ月以上の試験と改良を重ねた研究原型機とされ、助走なしで投擲された。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Hard level throw, no run-up",
            technique_ja="力強く水平に投擲（助走なし）",
            throw_style="level_push",
            release_speed_mps=27.0,
            launch_angle_deg=15.0,
            release_height_m=1.85,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_speed",),
            hand_force_n=0.0,
            hand_duration_s=0.0,
            notes=(
                "No throw data was published for the distance event. 27 m/s is "
                "Blackburn's documented speed floor, aimed close to level: a "
                "distance throw is a penetration problem, not an endurance one."
            ),
        ),
        calibration=_stable_throw(29.8, 14.0, 2.0, "level_push", 0.0, 0.0),
        calibration_note=(
            "Calibrated level push at 29.8 m/s reproducing the 98.4 m world record distance."
        ),
        citations=("gwr_distance_2025", "gwr_cn_2026_03"),
        aircraft=_airframe(
            name="Firefly5",
            wing_area_m2=0.0110,
            wingspan_m=0.2000,
            mass_kg=0.0050,
            cd0=0.0085,
            cL0=0.010,
            cm0=0.0005,
            cm_alpha=-0.40,
            cm_q=-3.0,
            aeroelastic_compliance=0.6,
            elevon_deg=0.0,
            paper_stiffness=0.75,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 29.8,
            "launch_angle_deg": 12.0,
            "launch_height_m": 1.85,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={
            "wind_speed": 0.0,
            "gust_intensity": 0.0,
            "thermal_strength": 0.0,
            "turbulence_scale": 0.0,
            "temperature_K": 271.0,
        },
        notes=(
            "Same competition airframe as the duration machine, which is what "
            "the team actually did: four of the seven record holders also broke "
            "the distance record two months earlier."
        ),
        notes_ja=(
            "滞空機と同じ競技用機体。"
            "チームの実際の構成でもある（滞空記録の 7 名のうち 4 名が 2 ヶ月前に距離記録も更新した）。"
        ),
    ),
    # ------------------------------------------------------ predecessors
    RecordPreset(
        key="toda_29_2s",
        name="Takuo Toda glider - 29.2 s",
        name_ja="戸田拓夫機・滞空 - 29.2 秒",
        category="indoor_duration",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=29.2,
            unit="s",
            holder="Takuo Toda",
            holder_ja="戸田 拓夫",
            country="Japan",
            date="2010-12-19",
            venue="Fukuyama City, Hiroshima",
            aircraft_name="Takuo Toda paper glider",
            aircraft_name_ja="戸田拓夫 紙飛行機",
            notes=(
                "Held the record for fifteen years until February 2026. Toda "
                "later worked with Shinji Suzuki of the University of Tokyo on "
                "a space launch that was postponed as untrackable."
            ),
            notes_ja=(
                "2010 年 12 月から 2026 年 2 月まで 15 年間保持。"
                "戸田は後に東京大学の鈴木真二教授らと宇宙打ち上げプロジェクトを企図したが、追跡困難として延期された。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Vertical loft",
            technique_ja="鉛直ロフト",
            throw_style="record_loft",
            release_speed_mps=22.0,
            launch_angle_deg=78.0,
            release_height_m=2.0,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_vertical", "blackburn_speed"),
            notes="Toda's 2010 vertical loft throw to near-ceiling height (~15-18m), transitioning to glide.",
        ),
        calibration=_stable_throw(3.15, -2.5, 16.12, "direct", 0.0, 0.0),
        calibration_note="Calibrated steady glide descent from 16.1m apex reproducing 29.2 s flight time.",
        citations=("telegraph_2009", "guardian_2009", "gwr_duration_2026"),
        aircraft=_airframe(
            name="Toda glider",
            wing_area_m2=0.0185,
            wingspan_m=0.3600,
            mass_kg=0.0048,
            cL0=0.245,
            cd0=0.0090,
            cm0=0.035,
            cm_alpha=-0.40,
            cm_q=-3.5,
            aeroelastic_compliance=0.8,
            elevon_deg=0.0,
            rudder_deg=0.25,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 22.0,
            "launch_angle_deg": 76.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="A slightly lighter, larger-winged machine than the 2026 record.",
        notes_ja="2026 年の記録機より軽量・大翼の機体とした推定。",
    ),
    RecordPreset(
        key="blackburn_27_6s",
        name="Ken Blackburn glider - 27.6 s",
        name_ja="ケン・ブラックバーンの滞空 - 27.6 秒",
        category="indoor_duration",
        design_class="compound_origami",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=27.6,
            unit="s",
            holder="Ken Blackburn",
            holder_ja="ケン・ブラックバーン",
            country="United States",
            date="1998-10-08",
            venue="United States (indoors)",
            aircraft_name="Blackburn 'glider'",
            aircraft_name_ja="Blackburn 製グライダー",
            notes=(
                "Blackburn held the record from 1983 to 1996 and regained it in "
                "October 1998; it was confirmed by Guinness officials and "
                "reported by CNN. He is also the source of the two throw rules "
                "this module uses for the record machines."
            ),
            notes_ja=(
                "1983 年から 1996 年まで保持し、1998 年 10 月に再更新。"
                "ギネス認定官が確認し、CNN が報道した。"
                "ブラックバーンの投擲則（鉛直から 10 度以内、"
                "60 mph 以上）は本モジュールの記録機構成にそのまま使われている。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Within 10 deg of vertical, at least 60 mph",
            technique_ja="鉛直から 10 度以内、60 mph 以上",
            throw_style="record_loft",
            release_speed_mps=26.9,
            launch_angle_deg=80.0,
            release_height_m=2.0,
            provenance=Provenance.MEASURED,
            rules=("blackburn_vertical", "blackburn_speed"),
            notes=(
                "The only record throw in the catalogue with *published* "
                "numbers: 60 mph is 26.9 m/s and 'within 10 degrees of vertical' "
                "is 80 degrees of launch angle."
            ),
        ),
        calibration=_stable_throw(3.3, -2.5, 11.8, "direct", 0.0, 0.0),
        calibration_note=(
            "Calibrated steady glide descent from 11.8m dome apex reproducing 27.6 s aloft time."
        ),
        citations=("cnn_1998", "ken_blackburn_1998"),
        aircraft=_airframe(
            name="Blackburn glider",
            mass_kg=0.0055,
            wing_area_m2=0.0125,
            wingspan_m=0.2400,
            cd0=0.0220,
            cL0=0.250,
            cm0=0.035,
            cm_alpha=-0.40,
            cm_q=-3.5,
            aeroelastic_compliance=0.8,
            elevon_deg=0.0,
            rudder_deg=0.25,
            paper_stiffness=0.45,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 26.9,
            "launch_angle_deg": 78.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes=(
            "Blackburn's own view - short wings and a heavy launch point for "
            "penetration, long wings and a light point for the glide - is why "
            "this preset keeps the mass at the rules cap and the wing smaller "
            "than the 2026 machines'."
        ),
        notes_ja=(
            "ブラックバーンの持論（打ち上げは短翼・重ノーズで貫通性を確保し、滑空は長翼・軽点で飛行時間を確保する）に基づき、"
            "本プリセットは質量を規則上限に保ち、翼は 2026 年機より小さく設定している。"
        ),
    ),
    RecordPreset(
        key="ruble_88_3m",
        name="Dillon Ruble dart - 88.318 m",
        name_ja="ディロン・ルブルの機体 - 88.318 m",
        category="indoor_distance",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=88.318,
            unit="m",
            holder="Dillon Ruble (with Nathaniel Erickson and Garrett Jensen)",
            holder_ja="Dillon Ruble（Nathaniel Erickson, Garrett Jensen サポート）",
            country="United States",
            date="2022-12-02",
            venue="Crown Point, Indiana",
            aircraft_name="",
            notes=(
                "88.318 m (289 ft 9 in) indoors, the record until Liu Liwen's "
                "98.43 m in December 2025."
            ),
            notes_ja=(
                "室内 88.318 m（289 ft 9 in）、"
                "2025 年 12 月の劉力文による 98.43 m まで記録を保持した。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Hard level throw",
            technique_ja="力強く水平に投擲",
            throw_style="level_push",
            release_speed_mps=29.5,
            launch_angle_deg=14.0,
            release_height_m=1.8,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_speed",),
            notes="No throw data published.",
        ),
        calibration=_stable_throw(29.5, 14.0, 1.8, "level_push", 0.0, 0.0),
        calibration_note="Calibrated throw reproducing 80-90 m flight distance.",
        citations=("wikipedia_paper_plane",),
        aircraft=_airframe(
            name="Ruble dart",
            wing_area_m2=0.0090,
            wingspan_m=0.1400,
            mass_kg=0.0050,
            cd0=0.0090,
            cL0=0.010,
            cm0=0.0005,
            cm_alpha=-0.40,
            cm_q=-3.0,
            aeroelastic_compliance=0.6,
            elevon_deg=0.0,
            paper_stiffness=0.70,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 29.5,
            "launch_angle_deg": 12.0,
            "launch_height_m": 1.8,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="A lower-aspect, faster machine than the 2025 record.",
        notes_ja="2025 年の記録機よりアスペクト比が低く、高速重視の機体（推定）。",
    ),
    RecordPreset(
        key="ayoob_69_1m",
        name="John Collins design - 69.14 m",
        name_ja="ジョン・コリンズ設計機 - 69.14 m",
        category="indoor_distance",
        design_class="scale_performance",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=69.14,
            unit="m",
            holder="Joe Ayoob (aircraft designed by John Collins)",
            holder_ja="Joe Ayoob（設計: John Collins）",
            country="United States",
            date="2012-02-29",
            venue="Indoors, United States",
            aircraft_name="John Collins design",
            aircraft_name_ja="John Collins 設計機",
            notes=(
                "226 ft 10 in, flown on 29 February 2012. Collins is the author "
                "of 'The Gliding Flight' (1989) and 'Fantastic Flight' (2004) "
                "and of the 'Paperang' family of airfoiled gliders; the record "
                "stood for over ten years."
            ),
            notes_ja=(
                "226 ft 10 in、2012 年 2 月 29 日。"
                "設計者の John Collins は『The Gliding Flight』（1989）と"
                "『Fantastic Flight』（2004）の著者であり、"
                "翼型付きグライダーの「Paperang」系列で 12:1 を超える滑空比を主張していた。"
                "この記録は 10 年以上保持された。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Hard level throw",
            technique_ja="力強く水平に投擲",
            throw_style="level_push",
            release_speed_mps=26.5,
            launch_angle_deg=14.0,
            release_height_m=1.8,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_speed",),
            notes="No throw data published.",
        ),
        calibration=_stable_throw(26.5, 14.0, 1.8, "level_push", 0.0, 0.0),
        calibration_note="Same calibrated throw as the Firefly5 preset.",
        citations=("wikipedia_paper_plane",),
        aircraft=_airframe(
            name="Collins design",
            wing_area_m2=0.0150,
            wingspan_m=0.3000,
            mass_kg=0.0050,
            cd0=0.0100,
            cL0=0.020,
            cm0=0.0010,
            cm_alpha=-0.40,
            cm_q=-3.0,
            aeroelastic_compliance=0.6,
            elevon_deg=0.0,
        ),
        launch={"launch_position_m": (0.0, 0.0, 0.0), "launch_speed": 26.5, "launch_angle_deg": 13.0, "launch_height_m": 1.8, "throw_speed_source": "direct", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes=(
            "A laminated, airfoiled machine rather than a folded dart - the "
            "design class that makes a 14:1 glide ratio possible."
        ),
        notes_ja=(
            "折り紙ではなく積層・翼型機。14:1 の滑空比を可能にする設計クラス。"
        ),
    ),
    RecordPreset(
        key="kim_77m",
        name="Shin Moo Joon / Julian design - 76.99 m",
        name_ja="シン・ムジュン / Julian 設計機 - 76.99 m",
        category="indoor_distance",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=76.99,
            unit="m",
            holder="Kim Kyu Tae (aircraft designed by Shin Moo Joon and Chee Yie Jian 'Julian')",
            holder_ja="Kim Kyu Tae（設計: Shin Moo Joon, Chee Yie Jian「Julian」）",
            country="South Korea",
            date="2022-05-19",
            venue="Indoors, South Korea",
            aircraft_name="",
            notes=(
                "252 ft 7 in, set in South Korea in May 2022 and superseded by "
                "Dillon Ruble's 88.318 m that December."
            ),
            notes_ja=(
                "252 ft 7 in、2022 年 5 月に韓国屋内で達成、"
                "同年 12 月に Dillon Ruble の 88.318 m に更新された。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Hard level throw",
            technique_ja="力強く水平に投擲",
            throw_style="level_push",
            release_speed_mps=26.0,
            launch_angle_deg=18.0,
            release_height_m=1.8,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_speed",),
            notes="No throw data published.",
        ),
        calibration=_stable_throw(27.5, 14.0, 1.85, "level_push", 0.0, 0.0),
        calibration_note="Calibrated direct throw reproducing the 77 m world record distance.",
        citations=("gwr_korea_2022", "wikipedia_paper_plane"),
        aircraft=_airframe(
            name="Kim Kyu Tae machine",
            wing_area_m2=0.0150,
            wingspan_m=0.3100,
            mass_kg=0.0050,
            cd0=0.0105,
            cL0=0.015,
            cm0=0.0010,
            cm_alpha=-0.40,
            cm_q=-3.0,
            aeroelastic_compliance=0.6,
            elevon_deg=0.0,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 27.5,
            "launch_angle_deg": 13.0,
            "launch_height_m": 1.85,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Reconstruction; no dimensions published.",
        notes_ja="推定値。寸法は非公開。",
    ),
    RecordPreset(
        key="kreiger_63_2m",
        name="Stephen Kreiger design - 63.20 m",
        name_ja="Stephen Kreiger 設計機 - 63.20 m",
        category="indoor_distance",
        design_class="compound_origami",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=63.20,
            unit="m",
            holder="Stephen Kreiger",
            holder_ja="Stephen Kreiger",
            country="United States",
            date="2003",
            venue="Indoors",
            aircraft_name="",
            notes=(
                "207 ft 4 in, the first of the modern indoor distance records; "
                "it stood until Joe Ayoob's 69.14 m in 2012."
            ),
            notes_ja="207 ft 4 in。現代の室内距離記録の第一号。2012 年の Joe Ayoob の 69.14 m に至るまで記録を保持した。",
        ),
        throw=ThrowRecipe(
            technique="Level throw",
            technique_ja="水平投擲",
            throw_style="level_push",
            release_speed_mps=24.0,
            launch_angle_deg=20.0,
            release_height_m=1.8,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_speed",),
            notes="No throw data published.",
        ),
        calibration=_stable_throw(23.4, 14.0, 1.85, "level_push", 0.0, 0.0),
        calibration_note="Calibrated throw reproducing 63.2 m flight distance.",
        citations=("wikipedia_paper_plane",),
        aircraft=_airframe(
            name="Kreiger design",
            wing_area_m2=0.0160,
            wingspan_m=0.2600,
            mass_kg=0.0050,
            cd0=0.0120,
            cL0=0.015,
            cm0=0.0010,
            cm_alpha=-0.40,
            cm_q=-3.0,
            aeroelastic_compliance=0.6,
            elevon_deg=0.0,
            paper_stiffness=0.45,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 23.4,
            "launch_angle_deg": 14.0,
            "launch_height_m": 1.85,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Reconstruction; no dimensions published.",
        notes_ja="推定値。寸法は非公開。",
    ),
    # ------------------------------------------------------------ academic
    RecordPreset(
        key="ninomiya_n424",
        name="Ninomiya N-424 - 85 m class research glider",
        name_ja="二宮康明 N-424 - 85 m 級研究グライダー",
        category="academic",
        design_class="profile_performance",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=85.0,
            unit="m",
            holder="Yasuaki Ninomiya and E. H. Mathews",
            holder_ja="二宮康明・E. H. Mathews",
            country="Japan / South Africa",
            date="1966-1993",
            venue="Research wind tunnels and field trials",
            aircraft_name="N-424 (White Wings series)",
            aircraft_name_ja="N-424（ホワイトウィングスシリーズ）",
            notes=(
                "Not a Guinness record: this is the published research "
                "result. Ninomiya and Mathews developed directed design "
                "strategies from the late 1960s through the 1980s and raised "
                "ranges from the typical 10+ m to 85+ m depending on the launch "
                "energy. Their work is described as the last research effort on "
                "paper glider performance. The N-424 appeared in 'Jet Age "
                "Jamboree' (1966); Mathews' 'Paper Pilot' series (1984-1993) "
                "used a Göttingen 801 curved-plate section and a small "
                "optimised wind tunnel."
            ),
            notes_ja=(
                "ギネス記録ではなく、研究成果として公表。"
                "1960 年代後半から1980 年代にかけて二宮と Mathews は設計戦略を体系化し、"
                "飛翔距離を 10 m 超から 85 m 超へと引き上げた。"
                "後述の記述では、これが紙グライダー性能研究の最終形態と位置づけられている。"
                "N-424 は 1966 年の『Jet Age Jamboree』に、"
                "Mathews の『Paper Pilot』シリーズ（1984-1993）"
                "は Göttingen 801 曲板翼と小型最適化風洞を用いた。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Catapult / bungee launch (not a hand throw)",
            technique_ja="カタパルト / バンジー打ち上げ（手投げではない）",
            throw_style="direct",
            release_speed_mps=24.0,
            launch_angle_deg=12.0,
            release_height_m=1.60,
            provenance=Provenance.RECONSTRUCTED,
            rules=(),
            notes=(
                "Mathews' later designs carried catapult and bungee hook "
                "patterns and a U-2 on the bungee was flown past 120 m. This is "
                "the only record-class machine in the catalogue that is not "
                "hand-thrown, so the hand-impulse model is bypassed."
            ),
        ),
        calibration=None,
        calibration_note="Not applicable: the launch is a stored-energy device, not a hand throw.",
        citations=("wikipedia_paper_plane",),
        aircraft=_airframe(
            name="Ninomiya N-424",
            mass_kg=0.0062,
            wing_area_m2=0.0200,
            wingspan_m=0.3400,
            cd0=round(_cd0_for_ld(18.0, 0.34, 0.0200), 5),
            paper_stiffness=0.75,
        ),
        launch={"throw_speed_source": "direct", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes=(
            "Kent paper, glued and taped multi-laminate construction - a very "
            "different machine from a folded A4 dart, which is why it can carry "
            "a Göttingen section and reach its section's critical Reynolds "
            "number in flight."
        ),
        notes_ja=(
            "ケント紙を接着剤とテープで積層した機体。"
            "折り紙の A4 ダートとは全く異なる構造であり、"
            "Göttingen 翼型を載せ飛行中に臨界レイノルズ数に到達できる理由である。"
        ),
    ),
    RecordPreset(
        key="papercopter_27_9s",
        name="Mathews Papercopter - 27.9 s",
        name_ja="Mathews Papercopter - 27.9 秒",
        category="historic",
        design_class="compound_origami",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=27.9,
            unit="s",
            holder="E. H. Mathews",
            holder_ja="E. H. Mathews",
            country="South Africa",
            date="1980s",
            venue="Indoors",
            aircraft_name="Papercopter",
            aircraft_name_ja="Papercopter",
            notes=(
                "A ring-wing model with flaps on the inboard edge that keep the "
                "flight stable; Mathews reported a 10-15 m range and a longest "
                "flight time of 27.9 s. It is the one catalogue entry that is "
                "not a fixed-wing glider, so the solver's flat-plate aerodynamics "
                "only approximate it."
            ),
            notes_ja=(
                "内側縁のフラップにより飛行を安定させるリングウィング（環状翼）型。"
                "Mathews は飛翔距離 10-15 m、最長滞空 27.9 秒を報告した。"
                "本カタログで唯一固定翼ではないため、本シミュレータの平板翼空力モデルでは近似に留まる。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Level throw with spin-up",
            technique_ja="スピン（自転）を伴う水平投擲",
            throw_style="overhand",
            release_speed_mps=16.0,
            launch_angle_deg=10.0,
            release_height_m=1.50,
            provenance=Provenance.RECONSTRUCTED,
            rules=(),
            notes="No throw data published.",
        ),
        calibration=None,
        calibration_note="Not applicable: a rotorcraft, outside the solver's model envelope.",
        citations=("wikipedia_paper_plane",),
        aircraft=_airframe(
            name="Papercopter",
            mass_kg=0.0045,
            wing_area_m2=0.0220,
            wingspan_m=0.2400,
            cd0=0.0300,
            cl_max=0.60,
            stall_alpha_deg=18.0,
            oswald_e=0.55,
            paper_stiffness=0.50,
        ),
        launch={"throw_speed_source": "impulse", "throw_runup_m": 0.0, "throw_wrist_spin_radps": 20.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Modelled as a low-aspect rotor-like plate; the numbers are indicative only.",
        notes_ja="低アスペクト比の回転体としてモデル化したもので、値は参考値。",
    ),
    RecordPreset(
        key="zongker_17s",
        name="Zongker 'World Record Paper Airplane' - 17 s x 5",
        name_ja="Zongker「世界記録紙飛行機」 - 17 秒 x 5",
        category="historic",
        design_class="compound_origami",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=17.0,
            unit="s",
            holder="James Zongker",
            holder_ja="James Zongker",
            country="United States",
            date="1985",
            venue="Indoor hangar",
            aircraft_name="Zongker World Record Paper Airplane",
            aircraft_name_ja="Zongker World Record Paper Airplane",
            notes=(
                "Five consecutive 17-second flights, published in 'The Paper "
                "Airplane Book' (1985). The machine is a forward-gliding "
                "autogyro: twin contra-rotating blades on paper axles spin up "
                "at launch and provide lift, and the upturned wingtips keep the "
                "rear down. It was the basis of the later space-launch effort."
            ),
            notes_ja=(
                "『The Paper Airplane Book』（1985）で 17 秒の連続 5 回飛行を公表。"
                "機体は前進飛行型の自動回転翼で、機首後縁を折り返した双葉が紙軸上で自転し揚力を得た。"
                "後日の宇宙打ち上げ実験の基点となった。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Level throw, blades spin up in flight",
            technique_ja="水平投擲（飛行中に双葉が自転開始）",
            throw_style="overhand",
            release_speed_mps=14.0,
            launch_angle_deg=8.0,
            release_height_m=1.50,
            provenance=Provenance.RECONSTRUCTED,
            rules=(),
            notes="No throw data published.",
        ),
        calibration=None,
        calibration_note="Not applicable: a rotor-thrust machine, outside the solver's envelope.",
        citations=("wikipedia_paper_plane",),
        aircraft=_airframe(
            name="Zongker",
            mass_kg=0.0050,
            wing_area_m2=0.0250,
            wingspan_m=0.3000,
            cd0=0.0260,
            cl_max=0.65,
            stall_alpha_deg=16.0,
            oswald_e=0.60,
            paper_stiffness=0.50,
        ),
        launch={"throw_speed_source": "impulse", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Rotor lift is not modelled; the preset reproduces the geometry, not the mechanism.",
        notes_ja="回転揚力はモデル化しておらず、幾何を再現したもので、機構の再現ではない。",
    ),
)


# --------------------------------------------------------------------------
# Lookup and payloads
# --------------------------------------------------------------------------
def preset_keys() -> List[str]:
    """Every preset key, in catalogue order."""
    return [entry.key for entry in RECORD_PRESET_CATALOGUE]


def preset(key: str) -> RecordPreset:
    """Look a preset up by key; unknown keys raise ``KeyError``."""
    for entry in RECORD_PRESET_CATALOGUE:
        if entry.key == key:
            return entry
    raise KeyError(
        f"unknown record preset '{key}'; known: {', '.join(preset_keys())}"
    )


def list_presets(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Summary payload for every preset, optionally filtered by category."""
    out: List[Dict[str, Any]] = []
    for entry in RECORD_PRESET_CATALOGUE:
        if category and entry.category != category:
            continue
        payload = entry.as_payload(include_configs=False)
        payload.pop("citations_detail", None)
        out.append(payload)
    return out


def preset_payload(key: str, include_configs: bool = True) -> Dict[str, Any]:
    """Full payload for one preset, including the throw rules and citations."""
    entry = preset(key)
    payload = entry.as_payload(include_configs=include_configs)
    payload["throw_rules"] = [dict(rule) for rule in THROW_RULES]
    payload["competition_rules"] = competition_rules()
    payload["design_classes"] = {
        name: {
            "label": spec.get("label", name),
            "reynolds": list(spec.get("reynolds", (0.0, 0.0))),
            "ld_ceiling": spec.get("ld_ceiling", 0.0),
            "notes": spec.get("notes", ""),
        }
        for name, spec in DESIGN_CLASSES.items()
    }
    return payload


def apply_preset(
    key: str,
    aircraft: Optional[AircraftConfig] = None,
    launch: Optional[LaunchConfig] = None,
    environment: Optional[EnvironmentConfig] = None,
    calibrated: bool = False,
    flight_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge a record preset onto the caller's configs and return all three."""
    entry = preset(key)
    new_aircraft = entry.aircraft_config(aircraft)
    new_launch = entry.launch_config(launch, calibrated=calibrated, flight_mode=flight_mode)
    new_environment = entry.environment_config(environment)
    return {
        "aircraft": as_dict(new_aircraft),
        "launch": as_dict(new_launch),
        "environment": as_dict(new_environment),
        "calibrated": bool(calibrated),
        **entry.flight_selection(calibrated, flight_mode)[1],
        "preset": entry.as_payload(include_configs=False),
    }


# --------------------------------------------------------------------------
# Simulation and verification
# --------------------------------------------------------------------------
def simulate_preset(
    key: str,
    duration_s: float = 60.0,
    calibrated: bool = False,
    aircraft: Optional[AircraftConfig] = None,
    launch: Optional[LaunchConfig] = None,
    environment: Optional[EnvironmentConfig] = None,
    record_interval_s: float = 0.05,
    flight_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Fly a record preset and compare the result with the published figure.

    Explicit modes choose the start condition independently of recipe source.
    Historical calibration recipes remain available, but are not certified
    against the repaired engine. The comparison exposes actual performance.
    """
    from . import flight_sim

    entry = preset(key)
    air = entry.aircraft_config(aircraft)
    env_cfg = entry.environment_config(environment)
    lch = entry.launch_config(launch, calibrated=calibrated, flight_mode=flight_mode)
    settings = SimulationSettings(
        duration_s=max(float(duration_s), 0.1),
        record_interval_s=max(float(record_interval_s), 1.0e-3),
    )
    sim = flight_sim.FlightSimulator(
        aircraft=air, environment=env_cfg, launch=lch, settings=settings
    )
    metrics = sim.simulate(settings.duration_s, record=True)
    summary = flight_sim.summarize_flight(sim, metrics)
    samples = [as_dict(s) for s in sim.history()]
    throw = flight_sim.resolve_throw(lch, air, env_cfg)
    measured = entry.achievement.value
    sim_key = RECORD_METRICS.get(entry.achievement.metric, {}).get("sim_key", "flight_time_s")
    simulated = float(metrics.get(sim_key, 0.0))
    ratio = (simulated / measured) if measured > 0.0 else 0.0
    energy = energy_budget(
        throw.mass_kg,
        throw.speed_mps,
        throw.flight_path_deg[0],
        gravity=abs(float(env_cfg.gravity)) or 9.80665,
    )
    pull = _pull_factor(air, throw.speed_mps, env_cfg)
    return {
        "key": entry.key,
        "name": entry.name,
        "name_ja": entry.name_ja,
        "calibrated": bool(calibrated),
        **entry.flight_selection(calibrated, flight_mode)[1],
        "throw": as_dict(throw),
        "metrics": metrics,
        "samples": samples,
        "total_frames": len(samples),
        "aircraft": as_dict(air),
        "launch": as_dict(lch),
        "environment": as_dict(env_cfg),
        "optimizer_payload": summary,
        "achievement": entry.achievement.as_payload(),
        "comparison": {
            "metric": entry.achievement.metric,
            "sim_key": sim_key,
            "unit": entry.achievement.unit,
            "measured_value": measured,
            "simulated_value": simulated,
            "ratio": ratio,
            "gap_percent": (ratio - 1.0) * 100.0,
            "higher_is_better": bool(
                RECORD_METRICS.get(entry.achievement.metric, {}).get("higher_is_better", True)
            ),
        },
        "energy_budget": energy,
        "pull_factor_g": pull,
        "limitations": _limitations(pull, metrics, calibrated),
    }


def _pull_factor(aircraft: AircraftConfig, speed_mps: float, environment: EnvironmentConfig) -> float:
    """Normal load factor the airframe pulls at ``speed_mps`` and its trim alpha."""
    from . import aero_domain

    derived = derived_properties(aircraft)
    air = aero_domain.atmosphere_state(environment, 0.0, derived["mac_m"])
    trim = aero_domain.trim_alpha_from_elevon(aircraft, aircraft.elevon_deg)
    result = aero_domain.evaluate_aero(
        aircraft,
        environment,
        max(float(speed_mps), 0.0),
        trim,
        beta_deg=0.0,
        mach=0.0,
        reynolds=None,
        ground_height_m=10.0,
    )
    weight = max(derived["mass_kg"] * abs(float(environment.gravity)), 1.0e-9)
    return abs(float(result.lift_n)) / weight


def _limitations(
    pull: float, metrics: Dict[str, Any], calibrated: bool
) -> List[Dict[str, str]]:
    """Why the solver and the record differ, stated as data."""
    out: List[Dict[str, str]] = []
    if pull > 6.0:
        out.append(
            {
                "key": "pull_factor",
                "severity": "high",
                "message": (
                    f"The selected release speed gives an estimated trim-load "
                    f"factor of {pull:.1f} g on the reconstructed airframe. "
                    "This static estimate is not the measured peak flight load "
                    "and does not establish numerical divergence."
                ),
                "message_ja": (
                    f"選択した発射速度での静的トリム荷重推定は {pull:.1f} g です。"
                    "これは実際の飛行中の最大荷重ではなく、数値発散の証拠でもありません。"
                ),
            }
        )
    if float(metrics.get("stalled", False)):
        out.append(
            {
                "key": "stall",
                "severity": "medium",
                "message": (
                    "The trajectory leaves the linear lift range, so the "
                    "post-stall blend dominates part of the flight."
                ),
                "message_ja": "弾道が線形揚力域を離脱し、後流剥離遷移が飛行の一部で支配的になる。",
            }
        )
    if str(metrics.get("status")) not in ("flying", "landed"):
        out.append(
            {
                "key": "terminal_status",
                "severity": "high",
                "message": (
                    f"The run ended with status '{metrics.get('status')}'."
                ),
                "message_ja": f"計算は status '{metrics.get('status')}' で終了した。",
            }
        )
    if not calibrated:
        out.append(
            {
                "key": "reconstruction",
                "severity": "info",
                "message": (
                    "The airframe geometry and the release speed are "
                    "reconstructions: only the result, the venue, the date and "
                    "the rules are published. A historical calibration recipe "
                    "does not validate the repaired flight model."
                ),
                "message_ja": (
                    "機体諸元および投擲初速は推定値。公開されているのは記録値・会場・日付および規則のみ。"
                    "モデル校正投擲と併用して比較すること。"
                ),
            }
        )
    out.append({
        "key": "performance_unvalidated",
        "severity": "info",
        "message": "Performance has not been validated against real flight measurements under the repaired model; calibration selects a recipe only.",
        "message_ja": "修正後モデルの性能は実飛行測定と照合していません。校正フラグはレシピの選択のみを表します。",
    })
    return out


def verification_report(
    keys: Optional[List[str]] = None,
    duration_s: float = 45.0,
    record_interval_s: float = 0.05,
) -> Dict[str, Any]:
    """Fly a set of presets and tabulate measured vs. simulated side by side.

    The report is the honest statement of what this solver can and cannot
    reproduce; it is exposed by the API so the browser never has to imply a
    fidelity the physics does not have.

    The payload states that meaning as data, not as prose the client may drop:
    ``performance_validated`` is ``False`` on the report and on every row, and
    ``performance_evidence`` is ``"none"`` because a self-report against a
    historical calibration recipe cannot be independent evidence.
    """
    selected = keys or preset_keys()
    rows: List[Dict[str, Any]] = []
    for key in selected:
        try:
            entry = preset(key)
        except KeyError:
            continue
        if entry.calibration is None:
            rows.append(
                {
                    "key": key,
                    "name_ja": entry.name_ja,
                    "category": entry.category,
                    "skipped": True,
                    "reason": entry.calibration_note or "no calibrated throw defined",
                    "measured_value": entry.achievement.value,
                    "unit": entry.achievement.unit,
                    "performance_validated": False,
                    "performance_evidence": "none",
                }
            )
            continue
        simulated = simulate_preset(
            key,
            duration_s=duration_s,
            calibrated=True,
            record_interval_s=record_interval_s,
        )
        rows.append(
            {
                "key": key,
                "name_ja": entry.name_ja,
                "category": entry.category,
                "skipped": False,
                "flight_mode": simulated["flight_mode"],
                "launch": simulated["launch"],
                "performance_validated": False,
                "performance_evidence": "none",
                **simulated["comparison"],
                "max_g_load": simulated["metrics"].get("max_g_load", 0.0),
                "max_ld_ratio": simulated["metrics"].get("max_ld_ratio", 0.0),
                "max_altitude_m": simulated["metrics"].get("max_altitude_m", 0.0),
            }
        )
    reproduced = [row for row in rows if not row.get("skipped") and row.get("ratio", 0.0) > 0.0]
    ratios = [float(row["ratio"]) for row in reproduced]
    return {
        "rows": rows,
        "evaluated": len(reproduced),
        "mean_ratio": (sum(ratios) / len(ratios)) if ratios else 0.0,
        "min_ratio": min(ratios) if ratios else 0.0,
        "max_ratio": max(ratios) if ratios else 0.0,
        "duration_s": duration_s,
        "performance_validated": False,
        "performance_evidence": "none",
        "performance_evidence_note": (
            "Self-report of the solver against a historical calibration recipe. "
            "The ratios below are not independent evidence of validated flight "
            "performance, and this report must not be presented as a "
            "reproduction rate."
        ),
        "method": (
            "Rows use historical calibration recipes with legacy start-mode "
            "selection; duration entries may begin at the apex and therefore "
            "do not demonstrate a complete hand-launched flight. Each row "
            "reports its actual start mode and launch. The ratio is simulated "
            "metric / published metric, not evidence of validated performance."
        ),
    }
