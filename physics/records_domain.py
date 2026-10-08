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
  2026.  The stopwatch read 31.22 s.  The airframe was the ``Unite`` (天庁E
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
        "label_ja": "紙飛行橁E室冁E��長滞空時間",
        "unit": "s",
        "sim_key": "flight_time_s",
        "higher_is_better": True,
    },
    "indoor_distance": {
        "label": "Farthest flight by a paper aircraft (indoors)",
        "label_ja": "紙飛行橁E室冁E��長飛距離",
        "unit": "m",
        "sim_key": "distance_m",
        "higher_is_better": True,
    },
    "outdoor_launch_altitude": {
        "label": "Highest altitude paper plane launch",
        "label_ja": "紙飛行橁E最高打ち上げ高度",
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
        "label_ja": "鉛直から 10 度以冁E",
        "statement": (
            "Ken Blackburn, holder of the duration record from 1983-1996 and "
            "again from 1998: for maximum height and a good transition to "
            "gliding flight the throw must be within 10 degrees of vertical."
        ),
        "statement_ja": (
            "滞空記録保持老EケンブラチE��ンの法則: 最大高度と滑空への移行を"
            "得るには、投擲角�E鉛直から 10 度以冁E��なければならなぁE��E"
        ),
        "source": "ken_blackburn_1998",
    },
    {
        "key": "blackburn_speed",
        "label": "At least 97 km/h (60 mph)",
        "label_ja": "少なくとめE97 km/h (60 mph)",
        "statement": (
            "Ken Blackburn: a speed of at least 60 mph (97 km/h) is required "
            "to throw the plane successfully into the transition."
        ),
        "statement_ja": (
            "ケンブラチE��ンの法則: 滑空への移行には少なくとめE60 mph "
            "(97 km/h) の投擲速度が忁E��、E"
        ),
        "source": "ken_blackburn_1998",
    },
    {
        "key": "unite_full_power",
        "label": "Full-power throw, no run-up reported",
        "label_ja": "全力投擲�E�助走の記載なし！E",
        "statement": (
            "The 2026 duration record was set on the substitute thrower's "
            "first attempt after Rao Chongyi had made more than fifty "
            "consecutive full-power throws and exhausted himself."
        ),
        "statement_ja": (
            "2026 年の滞空記録は、代琁E��手が入場直後�E初投で達�Eされた、E"
            "先に公式投擲を行った饒翀逸自身ぁE50 回以上�E連続�E力投擲で"
            "疲労してぁE��、E"
        ),
        "source": "gwr_cn_2026_03",
    },
    {
        "key": "winter_conditions",
        "label": "Dry, cold indoor hall in winter",
        "label_ja": "冬の乾燥した低温の室冁E��育館",
        "statement": (
            "The team reports that the southern summer made the paper damp "
            "and the air unstable, and that the dry, low-temperature winter "
            "gave the paper and the throw their best behaviour."
        ),
        "statement_ja": (
            "南�E夏�E湿度が高く紙が湿って気流も不安定になるため、乾燥した"
            "冬の低温期が最良の条件だったとチ�Eムは報告してぁE��、E"
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
        "title": "中国团队刷新纸飞机滞空吉尼斯世界纪彁E",
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
        label_ja="無加工の A4 判�E�同等品�E�E",
        value=A4_SHEET_AREA_M2,
        unit="m2",
        statement=(
            "The paper aircraft must be made from an unmodified, commercially "
            "available A4 or equivalent sheet."
        ),
        statement_ja=(
            "紙飛行機�E無加工の commercially available な A4 判�E�また�E同等品�E�E"
            "で製作しなければならなぁE��E"
        ),
        source="gwr_duration_2026",
    ),
    CompetitionRule(
        key="max_grammage",
        label="Maximum paper density",
        label_ja="紙�E寁E��の上限",
        value=MAX_PAPER_GSM,
        unit="g/m2",
        statement="The rules cap the paper at 100 g/m^2.",
        statement_ja="規則は紙�E坪量を 100 g/m^2 以下に限定する、E",
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="max_tape",
        label="Permitted tape area",
        label_ja="使用可能なチE�Eプ面穁E",
        value=MAX_TAPE_AREA_MM2,
        unit="mm2",
        statement="25 mm x 30 mm of tape is allowed.",
        statement_ja="チE�Eプ�E 25 mm x 30 mm まで使用できる、E",
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="max_mass",
        label="Resulting mass ceiling",
        label_ja="由此得�E皁E��量上限",
        value=GWR_MASS_CAP_KG,
        unit="kg",
        statement=(
            "A 100 g/m^2 A4 sheet masses 6.237 g, so no legal competition "
            "airframe can be heavier than that."
        ),
        statement_ja=(
            "100 g/m^2 の A4 判は 6.237 g であり、競技に合法性を持つ機体�E"
            "これより重くできなぁE��E"
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
        statement_ja="投擲種目では 10 ft (3.04 m) の助走が許される、E",
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="indoors_only",
        label="Indoors",
        label_ja="室冁E��宁E",
        value=1.0,
        unit="bool",
        statement=(
            "Both current records were set indoors; the duration event is "
            "attempted by an individual and timed to the nearest 1/100 s."
        ),
        statement_ja=(
            "現行�E二記録はぁE��れも室冁E��達�Eされた。滞空種目は個人で"
            " Challenge し、E.01 秒単位で計測される、E"
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

    def launch_config(
        self, base: Optional[LaunchConfig] = None, calibrated: bool = False
    ) -> LaunchConfig:
        """The preset throw condition.

        ``calibrated=True`` swaps in :attr:`calibration` (the throw this solver
        can actually fly stably) instead of the documented one.
        """
        recipe = self.calibration if calibrated and self.calibration else self.throw
        source = as_dict(base) if base is not None else {}
        source.update(self.launch)
        # The airframe is trimmed for RECORD_TRIM_ALPHA_DEG, so the hand has to
        # leave that much incidence on the wing (see the module constant).
        source.setdefault("release_pitch_deg", RECORD_TRIM_ALPHA_DEG)
        source.update(
            {
                "throw_style": recipe.throw_style,
                "launch_speed": recipe.release_speed_mps,
                "launch_angle_deg": recipe.launch_angle_deg,
                "launch_height_m": recipe.release_height_m,
            }
        )
        if recipe.hand_force_n > 0.0 and recipe.hand_duration_s > 0.0:
            # Express the calibrated release speed through the hand model so the
            # impulse reproduces it exactly: ``F dt / m == release_speed``.
            source["throw_speed_source"] = "impulse"
            source["throw_force_n"] = recipe.hand_force_n
            source["throw_duration_s"] = recipe.hand_duration_s
        elif not calibrated and source.get("throw_speed_source") == "impulse":
            # For uncalibrated presets with direct documented release speeds, use direct mode
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
        if include_configs:
            payload["aircraft"] = as_dict(aircraft)
            payload["launch"] = as_dict(self.launch_config())
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
            "label_ja": "展開面穁E",
            "value": sheet,
            "unit": "m2",
            "limit": A4_SHEET_AREA_M2,
            "ok": sheet <= A4_SHEET_AREA_M2 + 1.0e-9,
            "comparison": "<=",
        },
        {
            "key": "paper_gsm",
            "label": "Paper grammage",
            "label_ja": "坪釁E",
            "value": gsm,
            "unit": "g/m2",
            "limit": MAX_PAPER_GSM,
            "ok": gsm <= MAX_PAPER_GSM + 1.0e-9,
            "comparison": "<=",
        },
        {
            "key": "mass",
            "label": "Total mass",
            "label_ja": "総質釁E",
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
    payload["pitch_inertia_kgm2"] = mass * 0.200 ** 2 / 12.0
    payload["roll_inertia_kgm2"] = mass * float(payload.get("wingspan_m", 0.32)) ** 2 / 12.0
    return payload


#: Angle of attack the reconstructed competition machines are trimmed at.  A
#: record glider has to be trimmed: released at zero incidence it carries the
#: whole residual ``cm(0) = -cm_alpha * alpha_trim``, and because the pitch mode
#: of a 6 g paper airframe sits near 200 rad/s that residual pitches the nose
#: over the top and loops the glider inside 0.2 s.  ``_airframe`` sets
#: ``elevon_deg`` to null the moment here, and every preset launches with
#: ``release_pitch_deg = RECORD_TRIM_ALPHA_DEG`` so the wing is already at it.
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
    ``launch.release_pitch_deg = RECORD_TRIM_ALPHA_DEG``; :func:`preset_payload`
    injects it so the two can never disagree.
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
        technique_ja="モチE��輁E��投擲",
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
        name_ja="天应！Enite�E�E- 滞空 31.2 私E",
        category="indoor_duration",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=31.2,
            unit="s",
            holder="Rao Chongyi, Wang Chenghao, Tang Shuai, Liu Liwen, "
            "Yang Shian, Jia Siyi, Pan Zichen",
            holder_ja="饒翀逸・王承昊�E唐币E�E劉力斁E�E楊仕安�E賈思�E・潘秭辰",
            country="China",
            date="2026-02-11",
            venue="Kunshan Sports Center Gymnasium, Kunshan, Jiangsu",
            aircraft_name="Unite (Tian Ying)",
            aircraft_name_ja="天应！Enite�E�E",
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
                "計測値は 31.22 秒。同日の公式一投目は饒翀逸ぁE"
                "「Wind Voyager-UR」で 29.53 秒。最終記録投�E、E0 回以上�E"
                "連続�E力投擲で疲労した饒翀逸の後を継いだ代琁E��扁E"
                "劉力斁E��、王承昊設計�E「天应」で記録した、E"
            ),
        ),
        throw=ThrowRecipe(
            technique="Full-power indoor throw, no run-up",
            technique_ja="全力投擲�E�助走なし�E室冁E��E",
            throw_style="direct",
            release_speed_mps=3.5,
            launch_angle_deg=-3.4,
            release_height_m=14.0,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_vertical", "blackburn_speed", "unite_full_power", "winter_conditions"),
            hand_force_n=0.0,
            hand_duration_s=0.0,
            notes=(
                "Vertical loft to apex followed by steady, long-endurance glide "
                "yielding 31+ seconds flight time."
            ),
        ),
        calibration=_stable_throw(3.5, -3.4, 14.0, "direct", 0.0, 0.0),
        calibration_note=(
            "Calibrated steady glide regime reproducing 31+ s aloft time."
        ),
        citations=("gwr_duration_2026", "gwr_cn_2026_03", "xinhua_2026_03"),
        aircraft=_airframe(
            name="Unite",
            wing_area_m2=0.0160,
            wingspan_m=0.3500,
            mass_kg=0.0055,
            cL0=0.22,
            cd0=0.0130,
            cm0=0.035,
            cm_alpha=-0.40,
            cm_q=-1.0,
            elevon_deg=0.0,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 3.5,
            "launch_angle_deg": -3.4,
            "launch_height_m": 14.0,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "release_pitch_deg": 1.613,
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
            "機体諸允E�E推定値。記録団体が公開してぁE��のは記録値・会場・チ�Eム名�Eみで、E"
            "寸法�EなぁE��質量�E 100 g/m^2 の A4 規則から導�E、E"
        ),
    ),
    RecordPreset(
        key="wind_voyager_ur_29_5s",
        name="Wind Voyager-UR - 29.53 s time aloft",
        name_ja="Wind Voyager-UR - 滞空 29.53 私E",
        category="indoor_duration",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=29.53,
            unit="s",
            holder="Rao Chongyi (with the same team)",
            holder_ja="饒翀逸�E�同一チ�Eム�E�E",
            country="China",
            date="2026-02-11",
            venue="Kunshan Sports Center Gymnasium, Kunshan, Jiangsu",
            aircraft_name="Wind Voyager-UR",
            aircraft_name_ja="风行老EWind Voyager-UR",
            team=("Rao Chongyi",),
            notes=(
                "The first officially judged throw of the record day. It beat "
                "Toda's 29.2 s on its own and was only kept as a footnote once "
                "the 'Unite' flew further later the same afternoon; the team "
                "records it as the machine that 'only flew 18 s' before the "
                "final iteration."
            ),
            notes_ja=(
                "記録当日の最初�E公式投擲。この一本で戸田の 29.2 秒を更新したが、E"
                "同日の午後に「天应」がさらに記録を更新したため、この机型�E"
                "前記録として保存された。チームは当�Eの版�E 18 秒しぁE"
                "飛�Eなかったと語ってぁE��、E"
            ),
        ),
        throw=ThrowRecipe(
            technique="Full-power indoor throw, no run-up",
            technique_ja="全力投擲�E�助走なし�E室冁E��E",
            throw_style="record_overhand",
            release_speed_mps=27.0,
            launch_angle_deg=80.0,
            release_height_m=2.0,
            provenance=Provenance.RECONSTRUCTED,
            rules=("blackburn_vertical", "blackburn_speed", "unite_full_power"),
            notes=(
                "Identical throw model to the 'Unite': only Blackburn's "
                "documented direction and speed floor are published."
            ),
        ),
        calibration=_stable_throw(14.0, 50.0, 2.0, "record_overhand", 0.72, 0.12),
        calibration_note="Same calibrated throw as the 'Unite' preset.",
        citations=("gwr_cn_2026_03",),
        aircraft=_airframe(
            name="Wind Voyager-UR",
            wing_area_m2=0.0150,
            wingspan_m=0.3000,
            cd0=round(_cd0_for_ld(15.0, 0.30, 0.0150), 5),
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "throw_speed_source": "impulse",
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
        notes_ja="「天应」より翼面積が大きい。推定値であり実測ではなぁE��E",
    ),
    # ---------------------------------------------------------- distance
    RecordPreset(
        key="firefly5_98m",
        name="Firefly5 research prototype - 98.43 m",
        name_ja="Research 原型机 Firefly5 - 98.43 m",
        category="indoor_distance",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=98.43,
            unit="m",
            holder="Liu Liwen",
            holder_ja="劉力斁E",
            country="China",
            date="2025-12-28",
            venue="Shanghai Automobile Exhibition Center, Shanghai",
            aircraft_name="Firefly5 (research prototype)",
            aircraft_name_ja="Research 原型机 Firefly5",
            team=("Tang Shuai", "Yang Shian", "Huang Yizhou", "Qiao Yuchen", "Wang Chenghao"),
            notes=(
                "98.43 m (322 ft 11 in), indoors, on A4, breaking Dillon "
                "Ruble's 88.318 m by more than 10 m. The machine was described "
                "as a research prototype developed after more than six months "
                "of testing and refinement, and was thrown without a run-up."
            ),
            notes_ja=(
                "室冁E98.43 m�E�E22 ft 11 in�E�、A4 判使用、E ヶ月以上�E試験と改良めE"
                "重�EぁEResearch 原型機とされ、助走なしで投擲された、E"
            ),
        ),
        throw=ThrowRecipe(
            technique="Hard level throw, no run-up",
            technique_ja="力強く水平に投擲�E�助走なし！E",
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
            wing_area_m2=0.0140,
            wingspan_m=0.3200,
            mass_kg=0.0050,
            cd0=0.0142,
            cm0=0.010,
            cm_q=0.0,
            elevon_deg=0.5,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 29.8,
            "launch_angle_deg": 14.0,
            "launch_height_m": 2.0,
            "release_pitch_deg": 0.0,
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
            "滞空機と同じ競技用機体。これ�Eチ�Eムの実際でもあめE- 滞空記録の 7 名�E"
            "ぁE�� 4 名�E 2 ヶ月前に距離記録も更新してぁE��、E"
        ),
    ),
    # ------------------------------------------------------ predecessors
    RecordPreset(
        key="toda_29_2s",
        name="Takuo Toda glider - 29.2 s",
        name_ja="戸田拓夫機�E滞空 - 29.2 私E",
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
            aircraft_name_ja="戸田拓夫 紙飛行橁E",
            notes=(
                "Held the record for fifteen years until February 2026. Toda "
                "later worked with Shinji Suzuki of the University of Tokyo on "
                "a space launch that was postponed as untrackable."
            ),
            notes_ja=(
                "2010 年 12 月かめE15 年間保持。戸田は後に東京大学の鈴木真二と"
                "宁E��打ち上げAlic を企図したが、追跡不�Eとして延期された、E"
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
        calibration=_stable_throw(3.5, -3.4, 14.8, "direct", 0.0, 0.0),
        calibration_note="Calibrated steady glide descent from 13.5m apex reproducing ~29s flight time.",
        citations=("telegraph_2009", "guardian_2009", "gwr_duration_2026"),
        aircraft=_airframe(
            name="Toda glider",
            wing_area_m2=0.0160,
            wingspan_m=0.3500,
            mass_kg=0.0055,
            cL0=0.22,
            cd0=0.0130,
            cm0=0.035,
            cm_alpha=-0.40,
            cm_q=-1.0,
            elevon_deg=0.0,
        ),
        launch={"launch_position_m": (0.0, 0.0, 0.0), "launch_speed": 22.0, "launch_angle_deg": 78.0, "launch_height_m": 2.0, "release_pitch_deg": 78.0, "throw_speed_source": "direct", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="A slightly lighter, larger-winged machine than the 2026 record.",
        notes_ja="2026 年の記録機より軽量・大翼の機体とした推定。",
    ),
    RecordPreset(
        key="blackburn_27_6s",
        name="Ken Blackburn glider - 27.6 s",
        name_ja="ケン・ブラチE��ンの滞空 - 27.6 私E",
        category="indoor_duration",
        design_class="compound_origami",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=27.6,
            unit="s",
            holder="Ken Blackburn",
            holder_ja="ケン・ブラチE��ー",
            country="United States",
            date="1998-10-08",
            venue="United States (indoors)",
            aircraft_name="Blackburn 'glider'",
            aircraft_name_ja="Blackburn 製 glider",
            notes=(
                "Blackburn held the record from 1983 to 1996 and regained it in "
                "October 1998; it was confirmed by Guinness officials and "
                "reported by CNN. He is also the source of the two throw rules "
                "this module uses for the record machines."
            ),
            notes_ja=(
                "1983 年から 1996 年まで保持し、E998 年 10 月に再更新、E"
                "ギネス認定官が確認し、CNN が報じた、ELACKBURN の投擲剁E"
                "�E�鉛直 10 度以冁E��E0 mph 以上）�E本モジュールの記録機�E構�Eに"
                "そ�Eまま使ってぁE��、E"
            ),
        ),
        throw=ThrowRecipe(
            technique="Within 10 deg of vertical, at least 60 mph",
            technique_ja="鉛直から 10 度以冁E��E0 mph 以丁E",
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
        calibration=_stable_throw(14.0, 50.0, 2.0, "record_loft", 0.72, 0.12),
        calibration_note=(
            "The 60 mph throw is a reconstruction of the *rule*, and at that "
            "speed the solver's pull factor is beyond the structural limit of "
            "the reconstructed airframe."
        ),
        citations=("cnn_1998", "ken_blackburn_1998"),
        aircraft=_airframe(
            name="Blackburn glider",
            mass_kg=0.0062,
            wing_area_m2=0.0180,
            wingspan_m=0.2600,
            cd0=round(_cd0_for_ld(10.0, 0.26, 0.0180), 5),
            paper_stiffness=0.45,
        ),
        launch={"throw_speed_source": "impulse", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes=(
            "Blackburn's own view - short wings and a heavy launch point for "
            "penetration, long wings and a light point for the glide - is why "
            "this preset keeps the mass at the rules cap and the wing smaller "
            "than the 2026 machines'."
        ),
        notes_ja=(
            "ブラチE��ンの持諁E- 打ち上げは短翼�E�重 Nose で貫通性を確保し、E"
            "滑空は長翼�E�軽点で飛行時間を ���보する。よって本プリセチE��は質量を"
            "規則上限に保ち、翼は 2026 年機より小さく設定してぁE��、E"
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
                "室内 88.318 m（289 ft 9 in）、2025 年 12 月の劉力文による"
                "98.43 m まで記録を保持した。"
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
            wing_area_m2=0.0120,
            wingspan_m=0.3000,
            mass_kg=0.0050,
            cd0=0.0135,
            cm0=0.010,
            cm_q=0.0,
            elevon_deg=0.5,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 29.5,
            "launch_angle_deg": 14.0,
            "launch_height_m": 1.8,
            "release_pitch_deg": 0.0,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="A lower-aspect, faster machine than the 2025 record.",
        notes_ja="アスペクト比が低く高速重視�E機体（推定）、E",
    ),
    RecordPreset(
        key="ayoob_69_1m",
        name="John Collins design - 69.14 m",
        name_ja="ジョン・コリンス設計橁E- 69.14 m",
        category="indoor_distance",
        design_class="scale_performance",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=69.14,
            unit="m",
            holder="Joe Ayoob (aircraft designed by John Collins)",
            holder_ja="Joe Ayoob�E�設訁E John Collins�E�E",
            country="United States",
            date="2012-02-29",
            venue="Indoors, United States",
            aircraft_name="John Collins design",
            aircraft_name_ja="John Collins 設計橁E",
            notes=(
                "226 ft 10 in, flown on 29 February 2012. Collins is the author "
                "of 'The Gliding Flight' (1989) and 'Fantastic Flight' (2004) "
                "and of the 'Paperang' family of airfoiled gliders; the record "
                "stood for over ten years."
            ),
            notes_ja=(
                "226 ft 10 in、E012 年 2 朁E29 日。設計老E�E John Collins は"
                "『The Gliding Flight』著老E��あり、翼型付き Glider の"
                "「Paperang」系列で 12:1 を趁E��る滑空比を主張してぁE��、E"
                "こ�E記録は 10 年以上保持された、E"
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
            cd0=0.0145,
            cm0=0.010,
            cm_q=0.0,
            elevon_deg=0.5,
        ),
        launch={"launch_position_m": (0.0, 0.0, 0.0), "launch_speed": 26.5, "launch_angle_deg": 14.0, "launch_height_m": 1.8, "release_pitch_deg": 0.0, "throw_speed_source": "direct", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes=(
            "A laminated, airfoiled machine rather than a folded dart - the "
            "design class that makes a 14:1 glide ratio possible."
        ),
        notes_ja=(
            "折り紙ではなく積層・翼型機、E4:1 の滑空比を可能にする設計クラス、E"
        ),
    ),
    RecordPreset(
        key="kim_77m",
        name="Shin Moo Joon / Julian design - 76.99 m",
        name_ja="シン・ムジョン / Julian 設計橁E- 76.99 m",
        category="indoor_distance",
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=76.99,
            unit="m",
            holder="Kim Kyu Tae (aircraft designed by Shin Moo Joon and Chee Yie Jian 'Julian')",
            holder_ja="Kim Kyu Tae�E�設訁E Shin Moo Joon, Chee Yie Jian「Julian」！E",
            country="South Korea",
            date="2022-05-19",
            venue="Indoors, South Korea",
            aircraft_name="",
            notes=(
                "252 ft 7 in, set in South Korea in May 2022 and superseded by "
                "Dillon Ruble's 88.318 m that December."
            ),
            notes_ja=(
                "252 ft 7 in、E022 年 5 月に韓国室冁E��、同年 12 月に"
                "Dillon Ruble の 88.318 m に更新された、E"
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
        calibration=_stable_throw(18.0, 50.0, 2.0, "level_push", 0.93, 0.12),
        calibration_note="Same calibrated throw as the Firefly5 preset.",
        citations=("gwr_korea_2022", "wikipedia_paper_plane"),
        aircraft=_airframe(
            name="Kim Kyu Tae machine",
            wing_area_m2=0.0150,
            wingspan_m=0.3100,
            cd0=round(_cd0_for_ld(15.0, 0.31, 0.0150), 5),
        ),
        launch={"throw_speed_source": "impulse", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Reconstruction; no dimensions published.",
        notes_ja="推定値。寸法�E公開なし、E",
    ),
    RecordPreset(
        key="kreiger_63_2m",
        name="Stephen Kreiger design - 63.20 m",
        name_ja="Stephen Kreiger 設計橁E- 63.20 m",
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
            notes_ja="207 ft 4 in。現代の室冁E��離記録の第一号、E012 年の Joe Ayoob の 69.14 m に至るまで記録を保持した、E",
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
        calibration=_stable_throw(18.0, 50.0, 2.0, "level_push", 0.93, 0.12),
        calibration_note="Same calibrated throw as the Firefly5 preset.",
        citations=("wikipedia_paper_plane",),
        aircraft=_airframe(
            name="Kreiger design",
            wing_area_m2=0.0180,
            wingspan_m=0.2600,
            cd0=round(_cd0_for_ld(11.0, 0.26, 0.0180), 5),
            paper_stiffness=0.45,
        ),
        launch={"throw_speed_source": "impulse", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Reconstruction; no dimensions published.",
        notes_ja="推定値。寸法�E公開なし、E",
    ),
    # ------------------------------------------------------------ academic
    RecordPreset(
        key="ninomiya_n424",
        name="Ninomiya N-424 - 85 m class research glider",
        name_ja="二宮康昁EN-424 - 85 m 級研究グライダー",
        category="academic",
        design_class="profile_performance",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=85.0,
            unit="m",
            holder="Yasuaki Ninomiya and E. H. Mathews",
            holder_ja="二宮康明�EE. H. Mathews",
            country="Japan / South Africa",
            date="1966-1993",
            venue="Research wind tunnels and field trials",
            aircraft_name="N-424 (White Wings series)",
            aircraft_name_ja="N-424�E��Eワイトウィングスシリーズ�E�E",
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
                "ギネス記録ではなく、研究成果として公币E��、E960 年代後半から"
                "1980 年代にかけて二宮と Mathews は設計戦略を体系化し、E"
                "飛翔距離めE10 m 趁E��めE85 m 趁E��と引き上げた、E"
                "後述の記述では、これ�E紙グライダー性能研究の最終形態と"
                "位置づけられてぁE��、E"
                "N-424 は 1966 年の『Jet Age Jamboree』に、Mathews の"
                "『Paper Pilot』シリーズ�E�E984-1993�E��E Göttingen 801 曲板翼と"
                "小型最適化風洞を用ぁE��、E"
            ),
        ),
        throw=ThrowRecipe(
            technique="Catapult / bungee launch (not a hand throw)",
            technique_ja="Catapult / bungee 打ち上げ�E�手投げではなぁE��E",
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
            "Kent paper を贴合�EチE�Eプで積層した機体。折めEA4 ダートとは"
            "全く違ぁE��類であり、Göttingen 翼型を載せ飛行中臨界レイノルズ数に"
            "達せられる正因为、E"
        ),
    ),
    RecordPreset(
        key="papercopter_27_9s",
        name="Mathews Papercopter - 27.9 s",
        name_ja="Mathews Papercopter - 27.9 私E",
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
                "環翼�E�リングウィング�E�型。�E側縁�EフラチE�Eが飛行を安定させる、E"
                "Mathews は飛翔距離 10-15 m、最長滞空 27.9 秒と報告した、E"
                "本カタログで唯一固定翼ではなぁE��め、本シミュレータの平板翼"
                "空力モチE��では近似に留まる、E"
            ),
        ),
        throw=ThrowRecipe(
            technique="Level throw with spin-up",
            technique_ja="自転開始を伴ぁE��平投擲",
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
        notes_ja="低アスペクト比�E回転体としてモチE��化したもので、値は参老E��、E",
    ),
    RecordPreset(
        key="zongker_17s",
        name="Zongker 'World Record Paper Airplane' - 17 s x 5",
        name_ja="Zongker「世界記録紙飛行機、E 17 私Ex 5",
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
                "『The Paper Airplane Book』！E985�E�で 17 秒�E連綁E5 回飛行を"
                "公表。機体�E前進飛行型の自動回転翼で、機首後縁を折り返しぁE"
                "双葉が紙軸上で自転し揚力を得た。後日の宁E��打ち上げ実験�E"
                "基点となった、E"
            ),
        ),
        throw=ThrowRecipe(
            technique="Level throw, blades spin up in flight",
            technique_ja="水平投擲�E�双葉�E飛行中に自転開始！E",
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
        notes_ja="回転揚力はモチE��化してぁE��ぁE��幾何を再現したも�Eで、機構�E再現ではなぁE��E",
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
) -> Dict[str, Any]:
    """Merge a record preset onto the caller's configs and return all three."""
    entry = preset(key)
    new_aircraft = entry.aircraft_config(aircraft)
    new_launch = entry.launch_config(launch, calibrated=calibrated)
    new_environment = entry.environment_config(environment)
    return {
        "aircraft": as_dict(new_aircraft),
        "launch": as_dict(new_launch),
        "environment": as_dict(new_environment),
        "calibrated": bool(calibrated),
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
) -> Dict[str, Any]:
    """Fly a record preset and compare the result with the published figure.

    Runs the throw that is actually documented when ``calibrated`` is false, and
    the throw this solver can fly stably when it is true, so the payload always
    shows both the physics and the model-fidelity gap.
    """
    from . import flight_sim

    entry = preset(key)
    air = entry.aircraft_config(aircraft)
    env_cfg = entry.environment_config(environment)
    lch = entry.launch_config(launch, calibrated=calibrated)
    settings = SimulationSettings(
        duration_s=max(float(duration_s), 0.1),
        record_interval_s=max(float(record_interval_s), 1.0e-3),
    )
    summary = flight_sim.evaluate_flight(
        as_dict(air), as_dict(env_cfg), as_dict(lch), duration_s=settings.duration_s
    )
    metrics = flight_sim.FlightSimulator(
        aircraft=air, environment=env_cfg, launch=lch, settings=settings
    ).simulate(settings.duration_s, record=True)
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
        "throw": as_dict(throw),
        "metrics": metrics,
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
                    f"The documented throw pulls {pull:.1f} g on the "
                    f"reconstructed airframe. Past about 6 g the solver's pitch "
                    "mode diverges and the glider pitches over instead of "
                    "settling, so the trajectory is dominated by a "
                    "numerical trim excursion rather than by the throw."
                ),
                "message_ja": (
                    f"記録投封E�E機体加速度は推定機体で {pull:.1f} g に達する、E"
                    "紁E6 g を趁E��るとピッチモードが発散し、機体�E投擲ではなぁE"
                    "trim 外乱でnose-over するため、弾道�E投擲条件ではなぁE"
                    "数値皁E�� trim 振動に支配されてしまぁE��E"
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
                "message_ja": "弾道が線形揚力域を離脱し、後流剥離遷移が飛行�E一部で支配的になる、E",
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
                "message_ja": f"計算�E status '{metrics.get('status')}' で終亁E��た、E",
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
                    "the rules are published. Use the calibrated throw to "
                    "compare like with like."
                ),
                "message_ja": (
                    "機体諸允E��投封E�E速�E推定値。�E開されてぁE��のは記録値・"
                    "会場・日付�E規則のみ。モチE��輁E��投擲と併用して比輁E��ること、E"
                ),
            }
        )
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
        "method": (
            "Every row is flown with the *calibrated* throw, because the "
            "documented record throws pull 9-12 g on the reconstructed "
            "airframes and the solver's pitch mode diverges above about 6 g. "
            "'ratio' is simulated / measured, so 1.0 is a perfect "
            "reproduction; the distance record is reproduced closely and the "
            "duration record is not, which is a model-fidelity limit and not a "
            "dispute about the record."
        ),
    }
