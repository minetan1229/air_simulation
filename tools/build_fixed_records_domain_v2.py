import sys

def main():
    with open('physics/records_domain.py', 'r', encoding='utf-8') as f:
        text = f.read()

    orig_count = text.count('\ufffd')
    print(f"Original U+FFFD count: {orig_count}")

    # 1. Line 37 Docstring
    doc_target = "The stopwatch read 31.22 s.  The airframe was the ``Unite`` ("
    idx = text.find(doc_target)
    assert idx != -1, "doc_target not found"
    end_line = text.find("\n", idx)
    text = text[:idx] + "The stopwatch read 31.22 s.  The airframe was the ``Unite`` (天鹰)." + text[end_line:]

    # 2. RECORD_METRICS
    metrics_start = text.find("RECORD_METRICS: Dict[str, Dict[str, Any]] = {")
    metrics_end = text.find("\n}\n\n#: Documented statements", metrics_start) + 3
    assert metrics_start != -1 and metrics_end != -1, "RECORD_METRICS not found"
    new_metrics = """RECORD_METRICS: Dict[str, Dict[str, Any]] = {
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
}"""
    text = text[:metrics_start] + new_metrics + text[metrics_end:]

    # 3. THROW_RULES
    tr_start = text.find("THROW_RULES: Tuple[Dict[str, str], ...] = (")
    tr_end = text.find("\n)\n\n#: Notes on each source", tr_start) + 2
    assert tr_start != -1 and tr_end != -1, "THROW_RULES not found"
    new_tr = """THROW_RULES: Tuple[Dict[str, str], ...] = (
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
            "滞空記録保持者ケン・ブラックバーンの法則: 最大高度と滑空への良好な移行を"
            "得るには、投擲角は鉛直から 10 度以内でなければならない。"
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
            "ケン・ブラックバーンの法則: 滑空への移行を成功させるには少なくとも 60 mph "
            "(97 km/h) の投擲速度が必要である。"
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
            "2026 年の滞空記録は、代理選手の初投で達成された。"
            "先に公式投擲を行った饒翀逸自身が 50 回以上の連続全力投擲で"
            "疲労困憊したためである。"
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
            "南方の夏は湿度が高く紙が湿気を含み気流も不安定になるため、乾燥した"
            "冬の低温期が最良の条件であったとチームは報告している。"
        ),
        "source": "gwr_cn_2026_03",
    },
)"""
    text = text[:tr_start] + new_tr + text[tr_end:]

    # 4. CITATIONS (title line)
    cit_marker = '"key": "gwr_cn_2026_03"'
    c_idx = text.find(cit_marker)
    assert c_idx != -1, "gwr_cn_2026_03 not found"
    title_start = text.find('"title":', c_idx)
    title_end = text.find('\n', title_start)
    text = text[:title_start] + '"title": "中国团队刷新纸飞机滞空吉尼斯世界纪录",' + text[title_end:]

    # 5. COMPETITION_RULES
    cr_start = text.find("COMPETITION_RULES: Tuple[CompetitionRule, ...] = (")
    cr_end = text.find("\n)\n\n\ndef competition_rules() -> List[Dict[str, Any]]:", cr_start) + 2
    assert cr_start != -1 and cr_end != -1, "COMPETITION_RULES not found"
    new_cr = """COMPETITION_RULES: Tuple[CompetitionRule, ...] = (
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
        statement_ja="規則は紙の坪量を 100 g/m^2 以下に制限する。",
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
            "100 g/m^2 の A4 判用紙は 6.237 g であり、競技規則に適合する機体は"
            "これより重くすることはできない。"
        ),
        source="wikipedia_paper_plane",
    ),
    CompetitionRule(
        key="max_runup",
        label="Maximum run-up",
        label_ja="助走の上限",
        value=MAX_RUNUP_M,
        unit="m",
        statement="Run-up of up to 10 ft (3.04 m) is permitted in distance events.",
        statement_ja="距離種目では最大 10 ft (3.04 m) の助走が認められている。",
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
            "現行の世界記録はいずれも屋内で達成された。滞空種目は個人による"
            "挑戦で、1/100 秒単位で計測される。"
        ),
        source="gwr_duration_2026",
    ),
)"""
    text = text[:cr_start] + new_cr + text[cr_end:]

    # 6. launch_config method
    lc_find = 'source.setdefault("release_pitch_deg", RECORD_TRIM_ALPHA_DEG)'
    lc_idx = text.find(lc_find)
    assert lc_idx != -1, "launch_config setdefault not found"
    new_lc_code = (
        'if source.get("release_pitch_deg") in (None, 0.0):\n'
        '            source["release_pitch_deg"] = RECORD_TRIM_ALPHA_DEG\n'
        '        source.setdefault("release_pitch_deg", RECORD_TRIM_ALPHA_DEG)'
    )
    text = text[:lc_idx] + new_lc_code + text[lc_idx + len(lc_find):]

    # 7. gwr_compliance checks
    comp_start = text.find('checks = [\n        {\n            "key": "sheet_area",')
    comp_end = text.find('return {\n        "compliant":', comp_start)
    assert comp_start != -1 and comp_end != -1, "gwr_compliance checks not found"
    new_comp = """checks = [
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
    """
    text = text[:comp_start] + new_comp + text[comp_end:]

    # 8. _stable_throw
    st_start = text.find('def _stable_throw(')
    st_tech = text.find('technique_ja="', st_start)
    st_tech_end = text.find('",', st_tech)
    text = text[:st_tech] + 'technique_ja="モデル校正投擲' + text[st_tech_end:]

    # 9. RECORD_PRESET_CATALOGUE
    cat_start = text.find("RECORD_PRESET_CATALOGUE: Tuple[RecordPreset, ...] = (")
    cat_end = text.find("\n)\n\n\n# --------------------------------------------------------------------------\n# Lookup and payloads", cat_start) + 2
    assert cat_start != -1 and cat_end != -1, "RECORD_PRESET_CATALOGUE not found"

    new_catalogue = """RECORD_PRESET_CATALOGUE: Tuple[RecordPreset, ...] = (
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
                "ストップウォッチの計測値は 31.22 秒。同日の公式一投目に饒翀逸が"
                "「Wind Voyager-UR」で 29.53 秒を出し、戸田拓夫の 15 年間の記録を更新していた。"
                "最終的な世界記録投擲は、50 回以上の連続全力投擲で疲労困憊した饒翀逸の後を継いだ"
                "代理選手の劉力文が、王承昊設計の「天鹰」を投げて樹立した。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.00537,
            wing_span_m=0.315,
            wing_root_chord_m=0.108,
            wing_tip_chord_m=0.045,
            paper_gsm=80.0,
            cd0=_cd0_for_ld(11.5, 0.315, 0.0241, 0.82),
            oswald_e=0.82,
            paper_stiffness=0.55,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 26.0,
            "launch_angle_deg": 76.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
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
        calibration=_stable_throw(3.3, -2.8, 17.3, "record_overhand", 18.0, 0.22),
        calibration_note="Calibrated apex glide condition (hand loft removed to reproduce historical simulator glide)",
        notes=(
            "Airframe geometry is reconstructed: the record body publishes the "
            "result, the venue and the team, but no dimensions. Mass is derived "
            "from the 100 g/m^2 A4 rule."
        ),
        notes_ja=(
            "機体諸元は推定値。記録団体が公開しているのは記録値・会場・チーム名のみで、"
            "寸法は非公開。質量は 100 g/m^2 の A4 規則から算出した。"
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
                "記録当日の最初の公式投擲。この 1 本で戸田拓夫の 29.2 秒を更新したが、"
                "同日の午後に「天鹰」がさらに記録を更新したため、本機型は前記録として保存された。"
                "チームは開発初期のバージョンでは 18 秒しか飛べなかったと語っている。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.00550,
            wing_span_m=0.320,
            wing_root_chord_m=0.110,
            wing_tip_chord_m=0.048,
            paper_gsm=80.0,
            cd0=_cd0_for_ld(11.2, 0.320, 0.0253, 0.82),
            oswald_e=0.82,
            paper_stiffness=0.55,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 26.0,
            "launch_angle_deg": 76.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
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
            date="2025-12-25",
            venue="Jiaxing, Zhejiang (indoors)",
            aircraft_name="Firefly5 (research prototype)",
            aircraft_name_ja="研究原型機 Firefly5",
            team=(
                "Liu Liwen",
                "Rao Chongyi",
                "Wang Chenghao",
                "Tang Shuai",
            ),
            notes=(
                "98.43 m (322 ft 11 in), indoors, on A4, breaking Dillon "
                "Ruble's 88.318 m by more than 10 m. The machine was described "
                "as a research prototype developed after more than six months "
                "of testing and refinement, and was thrown without a run-up."
            ),
            notes_ja=(
                "室内 98.43 m（322 ft 11 in）、A4 判使用。6 ヶ月以上の試験と改良を"
                "重ねた研究原型機とされ、助走なしで投擲された。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.00537,
            wing_span_m=0.295,
            wing_root_chord_m=0.105,
            wing_tip_chord_m=0.038,
            paper_gsm=80.0,
            cd0=_cd0_for_ld(11.0, 0.295, 0.0211, 0.80),
            oswald_e=0.80,
            paper_stiffness=0.55,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 29.8,
            "launch_angle_deg": 12.0,
            "launch_height_m": 1.85,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={
            "wind_speed": 0.0,
            "gust_intensity": 0.0,
            "thermal_strength": 0.0,
            "turbulence_scale": 0.0,
            "temperature_K": 278.0,
        },
        notes=(
            "Same competition airframe as the duration machine, which is what "
            "the team actually did: four of the seven record holders also broke "
            "the distance record two months earlier."
        ),
        notes_ja=(
            "滞空機と同じ競技用機体。チームの実際の構成でもある（滞空記録の 7 名の"
            "うち 4 名が 2 ヶ月前に距離記録も更新した）。"
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
            venue="Fukuyama, Hiroshima (indoors)",
            aircraft_name="Takuo Toda paper glider",
            aircraft_name_ja="戸田拓夫 紙飛行機",
            notes=(
                "Held the record for fifteen years until February 2026. Toda "
                "later worked with Shinji Suzuki of the University of Tokyo on "
                "a space launch that was postponed as untrackable."
            ),
            notes_ja=(
                "2010 年 12 月から 15 年間保持。戸田は後に東京大学の鈴木真二教授らと"
                "宇宙打ち上げプロジェクトを企図したが、追跡困難として延期された。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.00510,
            wing_span_m=0.330,
            wing_root_chord_m=0.115,
            wing_tip_chord_m=0.050,
            paper_gsm=75.0,
            cd0=_cd0_for_ld(11.8, 0.330, 0.0272, 0.82),
            oswald_e=0.82,
            paper_stiffness=0.50,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 22.0,
            "launch_angle_deg": 76.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={
            "wind_speed": 0.0,
            "gust_intensity": 0.0,
            "thermal_strength": 0.0,
            "turbulence_scale": 0.0,
            "temperature_K": 293.15,
        },
        calibration=_stable_throw(3.2, -2.5, 15.0, "record_loft", 18.0, 0.22),
        calibration_note="Calibrated apex glide condition (hand loft removed to reproduce historical simulator glide)",
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
                "ギネス認定官が確認し、CNN が報道した。ブラックバーンの投擲則"
                "（鉛直から 10 度以内、60 mph 以上）は本モジュールの記録機構成に"
                "そのまま使われている。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.00624,
            wing_span_m=0.285,
            wing_root_chord_m=0.100,
            wing_tip_chord_m=0.040,
            paper_gsm=100.0,
            cd0=_cd0_for_ld(10.5, 0.285, 0.0200, 0.78),
            oswald_e=0.78,
            paper_stiffness=0.65,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 26.9,
            "launch_angle_deg": 78.0,
            "launch_height_m": 1.8,
            "launch_roll_deg": 0.0,
            "launch_yaw_deg": 0.0,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={
            "wind_speed": 0.0,
            "gust_intensity": 0.0,
            "thermal_strength": 0.0,
            "turbulence_scale": 0.0,
            "temperature_K": 293.15,
        },
        calibration=_stable_throw(3.3, -2.6, 17.5, "record_loft", 18.0, 0.22),
        calibration_note="Calibrated apex glide condition (hand loft removed to reproduce historical simulator glide)",
        notes=(
            "Blackburn's own view - short wings and a heavy launch point for "
            "penetration, long wings and a light point for the glide - is why "
            "this preset keeps the mass at the rules cap and the wing smaller "
            "than the 2026 machines'."
        ),
        notes_ja=(
            "ブラックバーンの持論（打ち上げは短翼・重ノーズで貫通性を確保し、"
            "滑空は長翼・軽点で飛行時間を確保する）に基づき、本プリセットは質量を"
            "規則上限に保ち、翼は 2026 年機より小さく設定している。"
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
            venue="Crown Point, Indiana (indoors)",
            aircraft_name="",
            notes=(
                "88.318 m (289 ft 9 in) indoors, the record until Liu Liwen's "
                "98.43 m in December 2025."
            ),
            notes_ja="室内 88.318 m（289 ft 9 in）、2025 年 12 月の劉力文による 98.43 m まで記録を保持した。",
        ),
        throw=ThrowRecipe(
            technique="Hard level throw",
            technique_ja="力強く水平に投擲",
            throw_style="level_push",
            release_speed_mps=29.0,
            launch_angle_deg=12.0,
            release_height_m=1.85,
            provenance=Provenance.RECONSTRUCTED,
            rules=(),
        ),
        aircraft=_airframe(
            mass_kg=0.00624,
            wing_span_m=0.280,
            wing_root_chord_m=0.095,
            wing_tip_chord_m=0.025,
            paper_gsm=100.0,
            cd0=_cd0_for_ld(11.0, 0.280, 0.0168, 0.78),
            oswald_e=0.78,
            paper_stiffness=0.70,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 29.5,
            "launch_angle_deg": 12.0,
            "launch_height_m": 1.8,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
            "throw_speed_source": "direct",
            "throw_runup_m": 0.0,
        },
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="A lower-aspect, faster machine than the 2025 record.",
        notes_ja="アスペクト比が低く高速重視の機体（推定）。",
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
                "226 ft 10 in、2012 年 2 月 29 日。設計者の John Collins は"
                "『The Gliding Flight』の著者であり、翼型付きグライダーの"
                "「Paperang」系列で 12:1 を超える滑空比を主張していた。"
                "この記録は 10 年以上保持された。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Hard level throw",
            technique_ja="力強く水平に投擲",
            throw_style="level_push",
            release_speed_mps=26.5,
            launch_angle_deg=13.0,
            release_height_m=1.80,
            provenance=Provenance.RECONSTRUCTED,
            rules=(),
        ),
        aircraft=_airframe(
            mass_kg=0.00624,
            wing_span_m=0.335,
            wing_root_chord_m=0.120,
            wing_tip_chord_m=0.030,
            paper_gsm=100.0,
            cd0=_cd0_for_ld(14.0, 0.335, 0.0251, 0.85),
            oswald_e=0.85,
            paper_stiffness=0.70,
        ),
        launch={"launch_position_m": (0.0, 0.0, 0.0), "launch_speed": 26.5, "launch_angle_deg": 13.0, "launch_height_m": 1.8, "release_pitch_deg": RECORD_TRIM_ALPHA_DEG, "throw_speed_source": "direct", "throw_runup_m": 0.0},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes=(
            "A laminated, airfoiled machine rather than a folded dart - the "
            "design class that makes a 14:1 glide ratio possible."
        ),
        notes_ja="折り紙ではなく積層・翼型機。14:1 の滑空比を可能にする設計クラス。",
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
                "252 ft 7 in、2022 年 5 月に韓国屋内で達成、同年 12 月に"
                "Dillon Ruble の 88.318 m に更新された。"
            ),
        ),
        throw=ThrowRecipe(
            technique="Hard level throw",
            technique_ja="力強く水平に投擲",
            throw_style="level_push",
            release_speed_mps=27.5,
            launch_angle_deg=13.0,
            release_height_m=1.85,
            provenance=Provenance.RECONSTRUCTED,
            rules=(),
        ),
        aircraft=_airframe(
            mass_kg=0.00624,
            wing_span_m=0.310,
            wing_root_chord_m=0.110,
            wing_tip_chord_m=0.035,
            paper_gsm=100.0,
            cd0=_cd0_for_ld(12.5, 0.310, 0.0225, 0.82),
            oswald_e=0.82,
            paper_stiffness=0.65,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 27.5,
            "launch_angle_deg": 13.0,
            "launch_height_m": 1.85,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
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
        design_class="world_record_competition",
        achievement=RecordAchievement(
            metric="indoor_distance",
            value=63.20,
            unit="m",
            holder="Stephen Kreiger",
            holder_ja="Stephen Kreiger",
            country="United States",
            date="2003-06-12",
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
            launch_angle_deg=14.0,
            release_height_m=1.85,
            provenance=Provenance.RECONSTRUCTED,
            rules=(),
        ),
        aircraft=_airframe(
            mass_kg=0.00624,
            wing_span_m=0.290,
            wing_root_chord_m=0.100,
            wing_tip_chord_m=0.030,
            paper_gsm=100.0,
            cd0=_cd0_for_ld(11.5, 0.290, 0.0189, 0.80),
            oswald_e=0.80,
            paper_stiffness=0.65,
        ),
        launch={
            "launch_position_m": (0.0, 0.0, 0.0),
            "launch_speed": 23.4,
            "launch_angle_deg": 14.0,
            "launch_height_m": 1.85,
            "release_pitch_deg": RECORD_TRIM_ALPHA_DEG,
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
            aircraft_name_ja="N-424（ホワイトウイングスシリーズ）",
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
                "ギネス記録ではなく、研究成果として公表。1960 年代後半から"
                "1980 年代にかけて二宮と Mathews は設計戦略を体系化し、"
                "飛翔距離を 10 m 超から 85 m 超へと引き上げた。"
                "後述の記述では、これが紙グライダー性能研究の最終形態と"
                "位置づけられている。"
                "N-424 は 1966 年の『Jet Age Jamboree』に、Mathews の"
                "『Paper Pilot』シリーズ（1984-1993）は Göttingen 801 曲板翼と"
                "小型最適化風洞を用いた。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.0125,
            wing_span_m=0.420,
            wing_root_chord_m=0.090,
            wing_tip_chord_m=0.045,
            paper_gsm=180.0,
            cd0=_cd0_for_ld(15.5, 0.420, 0.0284, 0.88),
            oswald_e=0.88,
            paper_stiffness=0.85,
        ),
        launch={"throw_speed_source": "direct", "throw_runup_m": 0.0, "release_pitch_deg": RECORD_TRIM_ALPHA_DEG},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes=(
            "Kent paper, glued and taped multi-laminate construction - a very "
            "different machine from a folded A4 dart, which is why it can carry "
            "a Göttingen section and reach its section's critical Reynolds "
            "number in flight."
        ),
        notes_ja=(
            "ケント紙を接着剤とテープで積層した機体。折り紙の A4 ダートとは"
            "全く異なる構造であり、Göttingen 翼型を載せ飛行中に臨界レイノルズ数に"
            "到達できる理由である。"
        ),
    ),
    RecordPreset(
        key="papercopter_27_9s",
        name="Mathews Papercopter - 27.9 s",
        name_ja="Mathews Papercopter - 27.9 秒",
        category="rotorcraft",
        design_class="rotor_performance",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=27.9,
            unit="s",
            holder="E. H. Mathews",
            holder_ja="E. H. Mathews",
            country="South Africa",
            date="1984-1993",
            venue="Research field trials",
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
                "本カタログで唯一固定翼ではないため、本シミュレータの平板翼"
                "空力モデルでは近似に留まる。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.00450,
            wing_span_m=0.180,
            wing_root_chord_m=0.060,
            wing_tip_chord_m=0.060,
            paper_gsm=80.0,
            cd0=0.035,
            oswald_e=0.55,
            paper_stiffness=0.50,
        ),
        launch={"throw_speed_source": "impulse", "throw_runup_m": 0.0, "throw_wrist_spin_radps": 20.0, "release_pitch_deg": RECORD_TRIM_ALPHA_DEG},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Modelled as a low-aspect rotor-like plate; the numbers are indicative only.",
        notes_ja="低アスペクト比の回転体としてモデル化したもので、値は参考値。",
    ),
    RecordPreset(
        key="zongker_17s",
        name="Zongker 'World Record Paper Airplane' - 17 s x 5",
        name_ja="Zongker「世界記録紙飛行機」 - 17 秒 x 5",
        category="historic",
        design_class="historic_performance",
        achievement=RecordAchievement(
            metric="indoor_duration",
            value=17.0,
            unit="s",
            holder="James Zongker",
            holder_ja="James Zongker",
            country="United States",
            date="1985",
            venue="United States",
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
                "『The Paper Airplane Book』（1985）で 17 秒の連続 5 回飛行を"
                "公表。機体は前進飛行型の自動回転翼で、機首後縁を折り返した"
                "双葉が紙軸上で自転し揚力を得た。後日の宇宙打ち上げ実験の"
                "基点となった。"
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
        ),
        aircraft=_airframe(
            mass_kg=0.00500,
            wing_span_m=0.210,
            wing_root_chord_m=0.080,
            wing_tip_chord_m=0.040,
            paper_gsm=80.0,
            cd0=0.038,
            oswald_e=0.60,
            paper_stiffness=0.50,
        ),
        launch={"throw_speed_source": "impulse", "throw_runup_m": 0.0, "release_pitch_deg": RECORD_TRIM_ALPHA_DEG},
        environment={"wind_speed": 0.0, "gust_intensity": 0.0, "thermal_strength": 0.0},
        notes="Rotor lift is not modelled; the preset reproduces the geometry, not the mechanism.",
        notes_ja="回転揚力はモデル化しておらず、幾何を再現したもので、機構の再現ではない。",
    ),
)"""
    text = text[:cat_start] + new_catalogue + text[cat_end:]

    # 10. _limitations
    lim_start = text.find('if float(metrics.get("stalled", False)):')
    lim_end = text.find('out.append({\n        "key": "performance_unvalidated",', lim_start)
    assert lim_start != -1 and lim_end != -1, "_limitations not found"

    new_lim = """if float(metrics.get("stalled", False)):
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
                    "機体諸元および投擲初速は推定値。公開されているのは記録値・"
                    "会場・日付および規則のみ。モデル校正投擲と併用して比較すること。"
                ),
            }
        )
    """
    text = text[:lim_start] + new_lim + text[lim_end:]

    rem_fffd = text.count('\ufffd')
    print(f"Remaining U+FFFD count: {rem_fffd}")
    assert rem_fffd == 0, f"U+FFFD still exists: {rem_fffd}"

    with open('physics/records_domain.py', 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print("SUCCESS: physics/records_domain.py updated and verified 0 U+FFFD!")

if __name__ == '__main__':
    main()
