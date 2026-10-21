"""Unit and integration tests for record-airframe domain and REST endpoints."""

import re

import pytest
from app import app
from physics import records_domain
from physics.config_schema import LaunchConfig


@pytest.mark.parametrize("key", ["unite_31s", "toda_29_2s", "blackburn_27_6s"])
@pytest.mark.parametrize("calibrated", [False, True])
def test_explicit_full_flight_starts_at_hand_for_duration_presets(key, calibrated):
    payload = records_domain.apply_preset(key, calibrated=calibrated, flight_mode="full_flight")
    entry = records_domain.preset(key)
    assert payload["flight_mode"] == "full_flight"
    assert payload["launch"]["flight_mode"] == "full_flight"
    assert payload["launch"]["launch_height_m"] == entry.throw.release_height_m
    assert payload["launch"]["launch_speed"] == entry.throw.release_speed_mps
    assert payload["launch"]["launch_angle_deg"] == entry.throw.launch_angle_deg
    assert payload["launch"]["launch_height_m"] <= 2.0
    assert payload["source_fallback"] is calibrated
    assert payload["performance_validated"] is False


@pytest.mark.parametrize("calibrated", [False, True])
def test_explicit_apex_mode_preserves_existing_calibration(calibrated):
    entry = records_domain.preset("unite_31s")
    payload = records_domain.apply_preset(entry.key, calibrated=calibrated, flight_mode="apex_glide")
    assert payload["flight_mode"] == "apex_glide"
    assert payload["launch"]["launch_height_m"] == 17.3
    assert payload["launch"]["launch_angle_deg"] == -2.8
    assert payload["source_fallback"] is (not calibrated)
    assert payload["preset"]["calibration"] == entry.calibration.as_payload()


@pytest.mark.parametrize("key", ["ayoob_69_1m", "papercopter_27_9s"])
def test_apex_mode_rejects_distance_and_rotor_presets(key):
    with pytest.raises(ValueError, match="apex_glide"):
        records_domain.apply_preset(key, flight_mode="apex_glide")


def test_invalid_flight_mode_is_rejected():
    with pytest.raises(ValueError, match="flight_mode"):
        records_domain.apply_preset("unite_31s", flight_mode="unknown")


def test_legacy_launch_selection_compatibility_and_mode_reset():
    base = LaunchConfig(flight_mode="apex_glide", throw_speed_source="impulse", throw_force_n=10.0)
    entry = records_domain.preset("unite_31s")
    legacy = entry.launch_config(base, calibrated=True)
    assert legacy.launch_height_m == 17.3
    assert legacy.flight_mode == "legacy"
    assert legacy.throw_speed_source == "direct"
    full = entry.launch_config(base, calibrated=True, flight_mode="full_flight")
    assert full.launch_height_m == entry.throw.release_height_m
    assert full.flight_mode == "full_flight"


def test_catalogue_payload_advertises_actual_available_start_modes():
    for payload in records_domain.list_presets():
        assert payload["default_flight_mode"] == "full_flight"
        assert payload["flight_modes"]["full_flight"] == payload["throw"]
        if "apex_glide" in payload["flight_modes"]:
            assert payload["category"] == "indoor_duration"
            assert payload["flight_modes"]["apex_glide"] == payload["calibration"]


@pytest.mark.parametrize("mode, height, speed, angle", [
    ("full_flight", 2.0, 26.0, 80.0),
    ("apex_glide", 17.3, 3.3, -2.8),
])
def test_api_records_explicit_mode_matches_resolved_initial_launch(mode, height, speed, angle):
    client = app.test_client()
    response = client.post("/api/v1/records/apply", json={
        "key": "unite_31s", "calibrated": True, "flight_mode": mode})
    assert response.status_code == 200
    payload = response.json
    assert payload["flight_mode"] == mode
    assert payload["launch"]["launch_height_m"] == height
    assert payload["launch"]["launch_speed"] == speed
    assert payload["launch"]["launch_angle_deg"] == angle
    assert payload["throw"]["release_height_m"] == height


@pytest.mark.parametrize("key, mode", [
    ("unite_31s", "unknown"),
    ("ayoob_69_1m", "apex_glide"),
    ("papercopter_27_9s", "apex_glide"),
])
def test_invalid_api_mode_does_not_mutate_session(key, mode):
    from app import SESSION
    from physics.config_schema import as_dict
    client = app.test_client()
    before = tuple(as_dict(config) for config in (SESSION.aircraft, SESSION.launch, SESSION.environment))
    response = client.post("/api/v1/records/apply", json={"key": key, "flight_mode": mode})
    assert response.status_code == 400
    after = tuple(as_dict(config) for config in (SESSION.aircraft, SESSION.launch, SESSION.environment))
    assert after == before


def test_simulate_preset_runs_once_and_optimizer_payload_matches_trajectory(monkeypatch):
    from physics import flight_sim
    calls = []
    original = flight_sim.FlightSimulator.simulate

    def counted_simulate(self, duration_s=None, record=True):
        if duration_s == 0.1 or getattr(self.settings, "duration_s", None) == 0.1:
            calls.append(self)
        return original(self, duration_s, record)

    monkeypatch.setattr(flight_sim.FlightSimulator, "simulate", counted_simulate)
    result = records_domain.simulate_preset("unite_31s", duration_s=0.1,
        calibrated=True, flight_mode="full_flight", record_interval_s=0.02)
    assert len(calls) == 1
    summary = result["optimizer_payload"]
    assert set(summary) == set(flight_sim.EVALUATE_KEYS)
    assert summary["flight_time_s"] == result["metrics"]["flight_time_s"]
    assert summary["points"] == result["total_frames"]
    assert summary["max_altitude_m"] == max(sample["altitude_m"] for sample in result["samples"])
    assert result["flight_mode"] == "full_flight"
    assert result["performance_validated"] is False


def test_list_presets_returns_all_catalogue_entries():
    presets = records_domain.list_presets()
    assert len(presets) >= 10
    keys = [p["key"] for p in presets]
    assert "unite_31s" in keys
    assert "toda_29_2s" in keys
    assert "blackburn_27_6s" in keys
    assert "ayoob_69_1m" in keys
    assert "firefly5_98m" in keys


def test_preset_payload_structure():
    payload = records_domain.preset_payload("ayoob_69_1m")
    assert payload["key"] == "ayoob_69_1m"
    assert "throw" in payload
    assert payload["throw"]["release_speed_mps"] > 20.0
    assert payload["throw"]["launch_angle_deg"] > 10.0
    assert "aircraft" in payload
    assert "environment" in payload
    assert "throw_rules" in payload


def test_apply_preset_merges_configs():
    merged = records_domain.apply_preset("toda_29_2s")
    aircraft = merged["aircraft"]
    launch = merged["launch"]
    env = merged["environment"]

    assert aircraft["mass_kg"] > 0
    assert launch["launch_angle_deg"] > 60.0  # vertical loft throw
    assert env["temperature_K"] > 250.0


def test_api_records_presets_get():
    client = app.test_client()
    resp = client.get("/api/v1/records/presets")
    assert resp.status_code == 200
    data = resp.json
    assert "presets" in data
    assert data["count"] >= 10


def test_api_records_preset_by_key():
    client = app.test_client()
    resp = client.get("/api/v1/records/presets/blackburn_27_6s")
    assert resp.status_code == 200
    data = resp.json
    assert data["key"] == "blackburn_27_6s"
    assert data["throw"]["throw_style"] == "record_loft"


def test_api_records_apply():
    client = app.test_client()
    resp = client.post("/api/v1/records/apply", json={"key": "ruble_88_3m"})
    assert resp.status_code == 200
    data = resp.json
    assert data["ok"] is True
    assert "aircraft" in data
    assert "launch" in data


def test_api_records_apply_throw_only():
    client = app.test_client()
    # First set a distinct aircraft mass
    resp1 = client.post("/api/v1/records/apply", json={"key": "ayoob_69_1m"})
    orig_mass = resp1.json["aircraft"]["mass_kg"]

    # Apply toda throw only
    resp2 = client.post("/api/v1/records/apply", json={"key": "toda_29_2s", "throw_only": True})
    assert resp2.status_code == 200
    # Mass should remain orig_mass
    assert resp2.json["aircraft"]["mass_kg"] == orig_mass
    # Launch angle should be Toda's loft pitch
    assert resp2.json["launch"]["launch_angle_deg"] > 70.0


def test_api_throw_techniques():
    client = app.test_client()
    resp = client.get("/api/v1/records/throw_techniques")
    assert resp.status_code == 200
    data = resp.json
    assert "throw_rules" in data
    assert len(data["throw_rules"]) > 0


def test_api_records_apply_calibrated_toggle():
    client = app.test_client()
    # Default is calibrated=True
    resp_cal = client.post("/api/v1/records/apply", json={"key": "ayoob_69_1m"})
    assert resp_cal.status_code == 200
    assert resp_cal.json["calibrated"] is True
    assert resp_cal.json["throw"]["release_speed_mps"] == 26.5

    # Explicit calibrated=False applies raw documented throw
    resp_raw = client.post("/api/v1/records/apply", json={"key": "firefly5_98m", "calibrated": False})
    assert resp_raw.status_code == 200
    assert resp_raw.json["calibrated"] is False
    assert resp_raw.json["throw"]["release_speed_mps"] == 27.0


# ---------------------------------------------------------------------------
# Japanese text hygiene (regression guard for the CP932 mojibake incident)
# ---------------------------------------------------------------------------

#: Codepoints that only ever appear as the mangled "trail byte" side of a
#: CP932 double byte pair, plus stray Hangul syllables.  A single occurrence
#: in Japanese text means the string was round-tripped through the wrong
#: codec.  Keep in sync with ``tools/check_mojibake.py``.
MOJIBAKE_ARTIFACTS = frozenset(
    "\u5181\u6a41\u7401\u8f01\u8a01\u5bc1\u7a41\u8ac1\u7681\u6701"
    "\u7d81\u4e81\u5fc1\u91c1\u8d81\u8d34\u5e01\u6581\u6579\u6721"
    "\u5e04\u5b81\u6241\u4e3a\u4e01\u5141\u3041"
)
HANGUL = re.compile(r"[\uac00-\ud7af]")
DIGITS = re.compile(r"\d+(?:[.,]\d+)*")


def _japanese_strings():
    """Yield ``(label, text)`` for every Japanese string in the catalogue."""
    for metric in records_domain.RECORD_METRICS.values():
        yield "RECORD_METRICS.label_ja", metric["label_ja"]
    for rule in records_domain.THROW_RULES:
        yield "THROW_RULES.label_ja", rule["label_ja"]
        yield "THROW_RULES.statement_ja", rule["statement_ja"]
    for rule in records_domain.COMPETITION_RULES:
        yield "COMPETITION_RULES.label_ja", rule.label_ja
        yield "COMPETITION_RULES.statement_ja", rule.statement_ja
    for entry in records_domain.RECORD_PRESET_CATALOGUE:
        key = entry.key
        yield f"{key}.name_ja", entry.name_ja
        yield f"{key}.notes_ja", entry.notes_ja
        yield f"{key}.holder_ja", entry.achievement.holder_ja
        yield f"{key}.aircraft_name_ja", entry.achievement.aircraft_name_ja
        yield f"{key}.achievement.notes_ja", entry.achievement.notes_ja
        yield f"{key}.technique_ja", entry.throw.technique_ja


def test_japanese_strings_are_mojibake_free_and_balanced():
    for label, text in _japanese_strings():
        assert "\ufffd" not in text, f"{label}: replacement character in {text!r}"
        assert not (set(text) & MOJIBAKE_ARTIFACTS), f"{label}: mojibake artifact in {text!r}"
        assert not HANGUL.search(text), f"{label}: hangul syllable in {text!r}"
        assert text.count("（") == text.count("）"), f"{label}: unbalanced parentheses in {text!r}"
        assert text.count("「") == text.count("」"), f"{label}: unbalanced quotes in {text!r}"
        assert text.count("『") == text.count("』"), f"{label}: unbalanced book quotes in {text!r}"


def test_japanese_record_names_match_the_english_source():
    entry = records_domain.preset("unite_31s")
    # Seven team members in the English holder, seven in the Japanese one.
    assert len(entry.achievement.holder.split(", ")) == 7
    assert entry.achievement.holder_ja == "饒翀逸・王承昊・唐帥・劉力文・楊仕安・賈思翼・潘秭辰"
    assert entry.achievement.aircraft_name_ja == "天鹰 (Unite)"
    # Other personal names the catalogue renders in Japanese.
    assert records_domain.preset("toda_29_2s").achievement.holder_ja == "戸田 拓夫"
    assert records_domain.preset("ninomiya_n424").achievement.holder_ja == "二宮康明・E. H. Mathews"
    assert "ジョン・コリンズ" in records_domain.preset("ayoob_69_1m").name_ja
    assert "シン・ムジュン" in records_domain.preset("kim_77m").name_ja
    assert "ケン・ブラックバーン" in records_domain.preset("blackburn_27_6s").name_ja


def test_japanese_notes_keep_every_digit_of_their_english_source():
    """A number published in English must not disappear from the Japanese text."""
    pairs = []
    for entry in records_domain.RECORD_PRESET_CATALOGUE:
        pairs.append((f"{entry.key}.notes", entry.notes, entry.notes_ja))
        pairs.append((f"{entry.key}.achievement.notes",
                      entry.achievement.notes, entry.achievement.notes_ja))
    for rule in records_domain.COMPETITION_RULES:
        pairs.append(("COMPETITION_RULES.statement", rule.statement, rule.statement_ja))
    for rule in records_domain.THROW_RULES:
        pairs.append(("THROW_RULES.statement", rule["statement"], rule["statement_ja"]))
    for label, english, japanese in pairs:
        english_numbers = {n for m in DIGITS.finditer(english) for n in re.split(r"[/:,]", m.group(0)) if n}
        japanese_numbers = {n for m in DIGITS.finditer(japanese) for n in re.split(r"[/:,]", m.group(0)) if n}
        assert english_numbers <= japanese_numbers, (
            f"{label}: {sorted(english_numbers - japanese_numbers)} missing from {japanese!r}"
        )


def test_preset_payloads_expose_the_catalogue_japanese_strings():
    """The API must serve the very same Japanese text the catalogue holds."""
    listed = {payload["key"]: payload for payload in records_domain.list_presets()}
    assert set(listed) == {entry.key for entry in records_domain.RECORD_PRESET_CATALOGUE}
    for entry in records_domain.RECORD_PRESET_CATALOGUE:
        for payload in (listed[entry.key], records_domain.preset_payload(entry.key)):
            assert payload["name_ja"] == entry.name_ja
            assert payload["notes_ja"] == entry.notes_ja
            assert payload["achievement"]["holder_ja"] == entry.achievement.holder_ja
            assert payload["achievement"]["aircraft_name_ja"] == entry.achievement.aircraft_name_ja
            assert payload["achievement"]["notes_ja"] == entry.achievement.notes_ja
            assert payload["throw"]["technique_ja"] == entry.throw.technique_ja
    rules = {rule["key"]: rule for rule in records_domain.THROW_RULES}
    for served in records_domain.preset_payload("unite_31s")["throw_rules"]:
        assert served["label_ja"] == rules[served["key"]]["label_ja"]
        assert served["statement_ja"] == rules[served["key"]]["statement_ja"]
    competitions = {rule.key: rule for rule in records_domain.COMPETITION_RULES}
    for served in records_domain.competition_rules():
        assert served["label_ja"] == competitions[served["key"]].label_ja
        assert served["statement_ja"] == competitions[served["key"]].statement_ja

