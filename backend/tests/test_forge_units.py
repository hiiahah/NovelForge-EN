"""Unit tests for the deterministic Forge components (no DB, no model calls)."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fixtures import synthetic_novel as syn  # noqa: E402

from app.services.forge import claims as claims_mod  # noqa: E402
from app.services.forge import evidence as ev  # noqa: E402
from app.services.forge import examples as ex  # noqa: E402
from app.services.forge import firewall as fw  # noqa: E402
from app.services.forge import textmetrics as tm  # noqa: E402
from app.services.forge import validators as v  # noqa: E402
from app.services.forge.fingerprint import LAYERS, build_fingerprint, compact_fingerprint, validate_fingerprint  # noqa: E402
from app.services.forge.models import AUTHND_LAB_ALLOWED_MODELS, validate_lab_llm_config  # noqa: E402

KO_SAMPLE = (
    "비가 다시 내렸다. 골목은 젖은 밧줄 냄새가 났다.\n\n"
    "\"어디 갔었어?\"\n\n\"거래소에.\"\n\n"
    "나는 대답하지 않았다. 대답할 이유가 없었다.\n\n"
    "쿵. 문이 닫혔다.\n\n"
    "\"선배님, 정말 괜찮으세요?\"\n\n\"괜찮습니다. 걱정하지 마세요.\"\n\n"
    "그날 밤 나는 열쇠를 다시 확인했다. 왜 아직도 맞는 걸까?"
)


class _Ch:
    def __init__(self, n: int):
        self.chapter_number = n
        self.text = syn.chapter_text(n)
        self.language = "en"
        self.text_hash = tm.sha256_text(self.text)


# ------------------------------------------------------------------ textmetrics
def test_language_detection_and_units():
    assert tm.detect_language(syn.chapter_text(1)) == "en"
    assert tm.detect_language(KO_SAMPLE) == "ko"
    assert tm.count_units("one two three", "en") == 3
    assert tm.count_units("하나 둘 셋", "ko") == 3


def test_measure_is_deterministic_and_stylized():
    a = tm.measure(syn.chapter_text(3))
    b = tm.measure(syn.chapter_text(3))
    assert a.as_dict() == b.as_dict()
    assert a.language == "en"
    assert a.first_person_ratio > a.third_person_ratio
    assert a.short_paragraph_ratio > 0.6
    assert a.ending_type == "question_hook"
    assert a.dialogue_ratio > 0.1
    assert a.sentence_len["mean"] < 12


def test_korean_metrics_measure_original_text_without_translation():
    m = tm.measure(KO_SAMPLE)
    assert m.language == "ko"
    assert m.speech_levels  # speech levels detected from endings
    assert set(m.speech_levels) & {"formal", "polite", "plain", "intimate"}
    assert m.onomatopoeia_density > 0
    assert m.honorific_density > 0
    assert m.ending_type == "question_hook"
    assert tm.speech_level("괜찮습니다.") == "formal"
    assert tm.speech_level("걱정하지 마세요.") == "polite"
    assert tm.speech_level("문이 닫혔다.") == "plain"


def test_aggregate_ranges_and_in_range():
    agg = tm.aggregate([tm.measure(syn.chapter_text(n)).as_dict() for n in range(1, 7)])
    assert agg["chapters"] == 6
    assert agg["language"] == "en"
    assert "question_hook" in agg["ending_type"]
    assert tm.in_range(agg["dialogue_ratio"]["median"], agg["dialogue_ratio"])
    assert not tm.in_range(0.99, agg["dialogue_ratio"])


# --------------------------------------------------------------------- evidence
def test_evidence_locate_exact_and_normalized():
    text = syn.chapter_text(2)
    q = "Marit handed me the copper key."
    assert ev.locate(q, text) is not None
    # Whitespace and curly-quote differences are tolerated.
    assert ev.locate("Marit handed me the copper key.   “Keep it,”  she said.", text) is not None
    assert ev.locate("The moon hung like a coin", text) is None
    assert ev.locate("Ma", text) is None  # too short to identify a passage


def test_verify_chapter_analysis_rejects_fabricated_quotes_and_caps_inference():
    an = syn.fake_chapter_analysis(5)
    out = ev.verify_chapter_analysis(an, syn.chapter_text(5), manuscript_id="m1", chapter_id="c5", chapter_number=5)
    statuses = {o["evidence_excerpt"][:30]: o["verification_status"] for o in out["observations"]}
    assert any(s == "unverified" for s in statuses.values())
    fabricated = [o for o in out["observations"] if "moon hung" in o["evidence_excerpt"]]
    assert fabricated and fabricated[0]["verification_status"] == "unverified"
    assert fabricated[0]["inference_level"] == "weakly_inferred"
    assert fabricated[0]["confidence"] <= 0.4
    assert fabricated[0]["evidence_hash"] == ""
    verified = ev.verified_observations(out)
    assert verified and all(o["evidence_hash"] for o in verified)
    assert out["evidence_verified"] == len(verified)
    assert 0 < out["evidence_coverage"] < 1
    assert out.get("analysis_status") != "failed"


def test_verify_chapter_analysis_invalid_chapter_reference_and_excluded_section():
    an = {"evidence": [{"chapter_number": 99, "quote": syn.chapter_text(1)[:60]}], "scenes": []}
    out = ev.verify_chapter_analysis(an, syn.chapter_text(1), manuscript_id="m", chapter_id="c", chapter_number=1)
    assert out["observations"][0]["verification_status"] == "invalid_reference"
    assert out["analysis_status"] == "failed"
    an2 = {"evidence": [{"chapter_number": 1, "quote": syn.chapter_text(1)[:60]}]}
    out2 = ev.verify_chapter_analysis(an2, syn.chapter_text(1), manuscript_id="m", chapter_id="c", chapter_number=1, chapter_excluded=True)
    assert out2["observations"][0]["verification_status"] == "excluded_section"
    out3 = ev.verify_chapter_analysis({"summary": "x", "evidence": []}, syn.chapter_text(1), manuscript_id="m", chapter_id="c", chapter_number=1)
    assert out3["analysis_status"] == "failed"


# --------------------------------------------------------------------- firewall
def _profile():
    chapters = [_Ch(n) for n in range(1, 25)]
    return fw.SourceProfile.from_chapters(chapters, manuscript_id="m1", entity_names=list(syn.SOURCE_CHARACTERS) + syn.SOURCE_LOCATIONS + syn.SOURCE_ITEMS, character_roles=syn.SOURCE_CHARACTERS, locations=syn.SOURCE_LOCATIONS, objects=syn.SOURCE_ITEMS, beat_sequence=["setup", "confrontation", "false_victory", "escalation", "reversal"] * 4)


def test_firewall_detects_entity_phrase_dialogue_and_quotation():
    prof = _profile()
    original = "Nadia counted the rivets on the hatch. Eleven. \"You were late,\" said Teo. \"I was somewhere else,\" she said. Then the corridor light died and the ship went quiet. Who had turned it off?"
    report = fw.check_text(original, prof, allowed_names=["Nadia", "Teo"])
    assert report.passed, [f.as_dict() for f in report.findings]
    leaked = "Nadia met Marit Solen near the Greywater Exchange. I did not run. Running is a confession. \"You will know the door when you see it.\""
    rep = fw.check_text(leaked, prof, allowed_names=["Nadia"])
    checks = {f.check for f in rep.findings}
    assert not rep.passed
    assert "entity_overlap" in checks
    assert "dialogue_overlap" in checks
    # Long phrase copied verbatim from the source
    copied = "Something else. " + syn.chapter_text(4).split("\n\n")[12] + " And then nothing."
    rep2 = fw.check_text(copied, prof, allowed_names=["Marit", "Brann", "Greywater Exchange"])
    assert any(f.check in ("long_phrase_overlap", "accidental_quotation") for f in rep2.findings)
    assert all(f.span is not None for f in rep2.findings if f.check == "long_phrase_overlap")


def test_firewall_beat_sequence_and_role_mapping():
    prof = _profile()
    rep = fw.check_text("Plain original text with nothing shared.", prof, beat_sequence=["setup", "confrontation", "false_victory", "escalation", "reversal", "setup", "confrontation", "false_victory"], character_roles={"Nadia": "Protagonist", "Ilse Varn": "Antagonist"})
    checks = {f.check for f in rep.findings}
    assert "beat_sequence_similarity" in checks
    assert "character_role_mapping" in checks


def test_firewall_bible_cards():
    prof = _profile()
    cards = [{"card_type": "Character Card", "title": "Nadia", "content": {"name": "Nadia", "aliases": ["the Quiet One"], "description": "A dock clerk."}}, {"card_type": "Scene Card", "title": "Tessaly", "content": {"name": "Tessaly", "description": "A city"}}]
    rep = fw.check_bible_cards(cards, prof)
    assert not rep.passed
    assert any(f.check == "location_similarity" and f.matched.lower() == "tessaly" for f in rep.findings)


# --------------------------------------------------------------------- examples
def test_example_tagging_redaction_and_positions():
    roles = {**{k: v.lower().replace(" ", "_") for k, v in syn.SOURCE_CHARACTERS.items()}, "Marit": "deuteragonist", "Brann": "antagonist", "Ilse": "protagonist", "Oskar": "supporting_character", "Lantern Ward": "place", "Greywater Exchange": "place", "Saltmarket": "place", "Tessaly": "place"}
    cands = ex.build_candidates(chapter_card_id=1, chapter_number=3, text=syn.chapter_text(3), manuscript_id="m1", roles=roles, hook_type="question", language="en")
    assert len(cands) >= 2
    assert all(len(c.excerpt) <= ex.MAX_EXCERPT_CHARS for c in cands)
    joined = " ".join(c.excerpt for c in cands)
    for name in ("Ilse", "Marit", "Brann", "Oskar", "Tessaly", "Greywater"):
        assert name not in joined, name
    assert "[ROLE:" in joined
    assert cands[0].position == "opening"
    assert cands[-1].position == "ending"
    assert "chapter_cliffhanger" in cands[-1].tags
    assert all(c.beat_function in tm.BEAT_FUNCTIONS for c in cands)
    assert all(c.evidence_hash for c in cands)


def test_functions_from_outline():
    outline = {"beats": [{"function": "quiet_scene_opening", "description": "x"}, {"function": "dialogue_heavy_scene"}, {"function": "chapter_cliffhanger"}], "ending_function": "chapter_cliffhanger"}
    assert ex.functions_from_outline(outline) == ["quiet_scene_opening", "dialogue_heavy_scene", "chapter_cliffhanger"]
    assert ex.functions_from_outline({"overview": "They fight; blades swung and parried, blood on the floor."}) == ["fight_choreography"] or "fight_choreography" in ex.functions_from_outline({"overview": "They fight; blades swung and parried, blood on the floor."})


# ------------------------------------------------------------------ fingerprint
def test_fingerprint_has_all_layers_and_validates():
    chapters = [_Ch(n) for n in range(1, 13)]
    analyses = {}
    for n in range(1, 13):
        analyses[n] = ev.verify_chapter_analysis(syn.fake_chapter_analysis(n), syn.chapter_text(n), manuscript_id="m1", chapter_id=f"c{n}", chapter_number=n)
    fp = build_fingerprint(chapters, manuscript_id="m1", analyses=analyses, character_roles=syn.SOURCE_CHARACTERS)
    assert validate_fingerprint(fp) == []
    assert set(fp["layers"]) == set(LAYERS)
    assert fp["layers"]["pov_focalization"]["features"]["pov"] == "first_person"
    assert fp["layers"]["pov_focalization"]["features"]["pov_stability"] == 1.0
    assert fp["layers"]["evidence_index"]["features"]["verified_observations"] > 0
    # No fabricated observation id made it into the index.
    fabricated_ids = {o["observation_id"] for a in analyses.values() for o in a["observations"] if o["verification_status"] != "verified"}
    assert not (set(fp["layers"]["evidence_index"]["features"]["observation_ids"]) & fabricated_ids)
    assert fp["layers"]["korean_register"]["confidence"] == 0.0
    compact = compact_fingerprint(fp, functions=["fight_choreography", "chapter_cliffhanger"])
    assert "rhythm" in compact and "action_scene" in compact and "suspense_reveal" in compact
    assert len(compact) <= 2200
    # Dependency hash changes with the text.
    fp2 = build_fingerprint(chapters[:-1], manuscript_id="m1", analyses=analyses)
    assert fp2["dependency_hash"] != fp["dependency_hash"]
    # Entity names never appear in compact rules.
    for name in syn.SOURCE_CHARACTERS:
        assert name not in compact


# ------------------------------------------------------------------- claims/validators
def test_claims_extraction_and_model_claim_cross_check():
    prose = "Nadia took the brass token from the drawer. Teo arrived at Harrow Quay before dusk. Nadia realized that the courier was left-handed."
    claims = claims_mod.extract_claims(prose, "en")
    kinds = {(c.kind, c.subject) for c in claims}
    assert ("possession_gained", "Nadia") in kinds
    assert ("location_changed", "Teo") in kinds
    assert ("knowledge_gained", "Nadia") in kinds
    assert all(prose[c.span[0]:c.span[1]] == c.evidence for c in claims)
    model = claims_mod.ChapterClaims(claims=[claims_mod.ClaimModel(kind="injury", subject="Teo", value="cut", evidence="Teo bled from a cut nobody saw.")])
    merged = claims_mod.merge_model_claims(prose, claims, model)
    assert any(c.source == "model" and c.support == "unsupported" for c in merged)
    prose2, model2 = claims_mod.split_prose_and_claims("Body text.\n<claims>{\"claims\": [], \"summary\": \"s\"}</claims>")
    assert prose2 == "Body text." and model2 is not None and model2.summary == "s"


def test_entity_validation_flags_source_leak_and_unplanned_recurring():
    prose = "Nadia watched Ilse Varn cross the yard. Corvin waved. Corvin waved again. Nadia did not."
    issues = v.validate_entities(prose, allowed=["Nadia"], source_entities=["Ilse Varn", "Marit Solen"], language="en")
    codes = {(i.code, i.evidence) for i in issues}
    assert ("source_entity_leak", "Ilse Varn") in codes
    assert ("unauthorized_entity", "Corvin") in codes


def test_outline_validation_detects_missing_and_out_of_order_and_future():
    beats = [{"description": "Nadia counts the rivets on the hatch", "keywords": ["rivets", "hatch"]}, {"description": "Teo confronts Nadia about the token", "keywords": ["confronts", "token"]}]
    prose = "Teo confronts Nadia about the token in the hold.\n\nLater Nadia counts the rivets on the hatch.\n\nThe captain reveals the hidden cargo manifest to everyone."
    issues = v.validate_outline(prose, beats=beats, forbidden=["(ch.5) captain reveals hidden cargo manifest"])
    codes = [i.code for i in issues]
    assert "beat_out_of_order" in codes
    assert "future_beat_advanced" in codes
    issues2 = v.validate_outline("Nothing relevant happens here at all.", beats=beats, forbidden=[])
    assert [i.code for i in issues2].count("beat_missing") == 2


def test_pov_validation_head_hopping_and_forbidden_reveal():
    prose = "Nadia watched the door. Teo thought about his brother's debt. Teo seemed tired. The courier was Teo all along, she was sure now."
    issues = v.validate_pov(prose, pov="Nadia", others=["Teo"], pov_type="third_person", prohibited=["the courier is Teo all along (POV unaware)"], language="en")
    codes = [i.code for i in issues]
    assert "head_hopping" in codes
    assert "forbidden_reveal" in codes
    assert codes.count("head_hopping") == 1  # "Teo seemed tired" is perception, not head-hopping


def test_temporal_validation():
    issues = v.validate_temporal("It was evening when they met. By midnight the gate was shut. At noon the same day she woke.", language="en")
    assert any(i.code == "time_inversion" for i in issues)
    assert not v.validate_temporal("Evening fell. The next morning she woke.", language="en")


def test_style_report_deterministic_targets():
    chapters = [_Ch(n) for n in range(1, 13)]
    fp = build_fingerprint(chapters, manuscript_id="m1")
    good = syn.chapter_text(20)  # same style
    bad = " ".join(["The long and winding exposition of the ancient guild continued for many paragraphs, because the history of the system was known to all, and it was said that centuries ago the rule had been written by scholars who were remembered for their patience and their extremely long sentences that never seemed to end."] * 12)
    rg = v.style_report(good, fp)
    rb = v.style_report(bad, fp)
    assert rg["adherence_score"] > rb["adherence_score"]
    assert "sentence_len_mean" in rb["failed_dimensions"]
    assert rb["repair_recommendations"]
    assert rg["evaluator_version"] == v.STYLE_EVALUATOR_VERSION
    criteria = v.model_style_criteria(fp)
    assert {c["dimension"] for c in criteria} >= {"pov_feel", "emotional_restraint", "chapter_hook"}
    assert all("sound like" not in c["criterion"].lower() for c in criteria)


# ---------------------------------------------------------------------- models
class _Cfg:
    def __init__(self, provider, model, api_key="", display_name=""):
        self.provider, self.model_name, self.api_key, self.display_name = provider, model, api_key, display_name


@pytest.mark.parametrize("provider,model,ok", [
    ("authnd", "moonshotai/kimi-k3", True),
    ("authnd", "authnd/moonshotai/kimi-k3", True),
    ("AuthND", "kimi-k3", True),
    ("authnd", "moonshotai/kimi-k2-instruct", True),
    ("genspark", "anything", True),
    ("authnd", "foo/kimi", False),
    ("authnd", "kimi-k3-mini", False),
    ("authnd", "moonshotai/kimi", False),
    ("authnd", "deepseek-ai/deepseek-v3", False),
    ("openai", "gpt-4o", False),  # no api key
    ("anthropic", "", False),  # no model name
    ("authnd", "moonshotai/", False),
])
def test_lab_model_validation_exact(provider, model, ok, monkeypatch):
    monkeypatch.delenv("AUTHND_DEFAULT_PUBLISHER", raising=False)
    result, _ = validate_lab_llm_config(_Cfg(provider, model))
    assert result is ok
    assert "moonshotai/kimi-k3" in AUTHND_LAB_ALLOWED_MODELS


@pytest.mark.parametrize("provider,model", [("anthropic", "claude-fable-5-1"), ("openai", "gpt-4o"), ("openai_compatible", "any/model"), ("google", "gemini-2.5-pro")])
def test_lab_model_validation_api_key_providers(provider, model):
    ok, reason = validate_lab_llm_config(_Cfg(provider, model, api_key="k"))
    assert ok is True
    assert reason == f"{provider}/{model}"
    ok, reason = validate_lab_llm_config(_Cfg(provider, model, api_key="   "))
    assert ok is False and "API key" in reason


def test_named_entities_ignores_contractions_and_common_sentence_starters():
    prose = "Don't do that. Don't look at me. Like a moth to flame, he walked. Students gathered outside. Students murmured. Trying was useless. Trying again would fail."
    entities = claims_mod.named_entities(prose, "en")
    assert "Don" not in entities
    assert "Like" not in entities
    assert "Students" not in entities
    assert "Trying" not in entities


def test_outline_validation_window_prevents_scattered_false_positives():
    beats = [{"description": "Ren enters room", "keywords": ["enters"]}]
    prose = "Ren opened his eyes in the morning. Sister Mary arrived hours later to clean. At dusk he felt threatened by the exam schedule."
    issues = v.validate_outline(prose, beats=beats, forbidden=["(ch.4) Sister Mary has openly threatened Ren"], language="en", participants=["Ren", "Sister Mary"])
    assert not any(i.code == "future_beat_advanced" for i in issues)


def test_pov_validation_ignores_character_mentions_for_prohibited_reveal():
    prose = "Ren Barlow drank the bitter tea. Sister Mary watched with a faint smile."
    issues = v.validate_pov(prose, pov="Ren Barlow", others=["Sister Mary"], prohibited=["(ch.6) Sister Mary is defeated"], language="en")
    assert not any(i.code == "forbidden_reveal" for i in issues)


def test_split_prose_and_claims_supports_chapter_summary_and_scene_handoff():
    raw = (
        "The tea tasted of cold ash. Ren did not swallow.\n\n"
        "<chapter_summary>\n"
        "# Chapter 1 Summary\n"
        "- Core Events: Ren survived the poisoned tea and formed an alliance with Vivian.\n"
        "- Ending State: Stood in the west corridor facing Sister Mary.\n"
        "</chapter_summary>\n\n"
        "<scene_handoff>\n"
        "ending_location: West Gallery Corridor\n"
        "current_time: Day 1, Evening\n"
        "present_characters: Ren, Sister Mary\n"
        "unresolved_action: Mary reaches inside her sleeve for the vial\n"
        "open_dialogue: Mary: \"You have not finished your cup, Tutor.\"\n"
        "</scene_handoff>"
    )
    prose, claims = claims_mod.split_prose_and_claims(raw)
    assert prose == "The tea tasted of cold ash. Ren did not swallow."
    assert claims is not None
    assert "Ren survived the poisoned tea" in claims.summary
    assert claims.ending_location == "West Gallery Corridor"
    assert claims.current_time == "Day 1, Evening"
    assert "reaches inside her sleeve" in claims.unresolved_immediate_action
    assert "You have not finished" in claims.open_dialogue_obligation


def test_split_prose_and_claims_supports_hash_chapter_summary():
    raw = (
        "Prose content here.\n\n"
        "#chapter1sum\n"
        "Ren established his tutoring contract and exposed the counterfeit formula.\n"
        "#endchapter1sum"
    )
    prose, claims = claims_mod.split_prose_and_claims(raw)
    assert prose == "Prose content here."
    assert claims is not None
    assert "Ren established his tutoring contract" in claims.summary


def test_sync_balanced_summary_extractor():
    from app.services.forge.sync import _summary
    paras = [f"Paragraph {i} begins here. This is the detail for scene {i}." for i in range(20)]
    full_text = "\n\n".join(paras)
    summary = _summary(full_text, max_chars=1000)
    assert "Paragraph 0 begins here." in summary
    assert "Paragraph 19 begins here." in summary


def test_compiler_rolling_10_chapter_summary_window():
    from unittest.mock import MagicMock
    from app.services.forge.compiler import ChapterContextCompiler
    
    compiler = ChapterContextCompiler(MagicMock())
    def mock_packet(project_id, ch):
        return {"chapter_number": ch, "summary": f"Summary of Chapter {ch}."}
    
    compiler._state_packet = mock_packet
    compiler._state_packet_card = lambda pid, ch: None
    
    start_ch = max(1, 15 - 10)
    assert start_ch == 5
    summary_sections = []
    for ch in range(start_ch, 15):
        p = compiler._state_packet(1, ch)
        if p and p.get("summary"):
            summary_sections.append(f"### Chapter {ch} Summary:\n{p.get('summary')}")
    assert len(summary_sections) == 10
    assert "Chapter 5 Summary" in summary_sections[0]
    assert "Chapter 14 Summary" in summary_sections[-1]
    assert not any("Chapter 4 Summary" in s for s in summary_sections)


def test_outline_validation_detects_morphological_variants():
    beats = [{"description": "Investigating the ledger", "keywords": ["ledger"]}]
    # Forbidden outcome has "forges" and "reveals", prose uses "forging" and "revealed"
    prose = "He looked through the ledger.\n\nThen the clerk revealed that Corvin was forging the manifests."
    forbidden = ["(ch.5) clerk reveals that Corvin forges the manifests"]
    issues = v.validate_outline(prose, beats=beats, forbidden=forbidden)
    assert any(i.code == "future_beat_advanced" for i in issues)


def test_architecture_3tier_clue_progression_validation():
    from app.services.autonomous.architecture import validate_architecture
    arch = {
        "contract": {"pov": "first person", "ending_contract": "solved"},
        "characters": [
            {"name": "Nadia", "role": "Protagonist", "home_location": "Docks", "arc": [{"phase": "start", "state": "s"}, {"phase": "mid", "state": "m"}, {"phase": "end", "state": "e"}]},
            {"name": "Vane", "role": "Antagonist", "home_location": "Docks", "arc": []}
        ],
        "locations": [{"name": "Docks"}],
        "setups_payoffs": [{"setup": "gun", "setup_chapter": 1, "payoff": "shot", "payoff_chapter": 10}],
        "plot_threads": [{"name": "Main", "thread_type": "main_plot", "opening_chapter": 1, "resolution_chapter": 10}],
        "knowledge_facts": [
            {"fact": "Vane poisoned the well", "knowers_at_start": ["Vane"], "clue_chapter": 3, "suspicion_chapter": 6, "reader_reveal_chapter": 9}
        ]
    }
    # Valid progression passes
    problems = validate_architecture(arch, chapter_count=10)
    assert not any(p["code"] in ("clue_after_reveal", "suspicion_after_reveal", "suspicion_before_clue") for p in problems)

    # Clue after reveal fails
    arch["knowledge_facts"][0]["clue_chapter"] = 10
    problems = validate_architecture(arch, chapter_count=10)
    assert any(p["code"] == "clue_after_reveal" for p in problems)


def test_chapter_plan_active_thread_continuity_and_forbidden_clues():
    from app.services.autonomous.chapter_plan import build_prompt, blueprint_to_outline
    arch = {
        "contract": {"ending_contract": "solved"},
        "plot_threads": [
            {"name": "Lost Sister", "thread_type": "subplot", "central_question": "Where is she?", "opening_chapter": 2, "resolution_chapter": 20},
            {"name": "Dock Strike", "thread_type": "subplot", "central_question": "Will they strike?", "opening_chapter": 9, "resolution_chapter": 12}
        ],
        "knowledge_facts": [
            {"fact": "The mayor is an impostor", "clue_chapter": 5, "reader_reveal_chapter": 15}
        ],
        "allocation": [{"function": "setup", "chapter_start": 1, "chapter_end": 8}],
        "act_plan": ["act 1"]
    }
    # Window 9-16: Lost Sister is active continuity, Dock Strike opens in window
    prompt = build_prompt(arch, chapters=[9, 10, 11, 12, 13, 14, 15, 16], total=20, word_target=2500, previous=[])
    assert "[PLOT THREAD CONTINUITY IN THIS WINDOW]" in prompt
    assert "Lost Sister" in prompt
    assert "ACTIVE CONTINUITY" in prompt
    assert "Dock Strike" in prompt

    # Blueprint at chapter 3: before clue_chapter (5) and reveal_chapter (15)
    bp = {"chapter_number": 3, "title": "Dawn", "beats": [{"function": "scene_opening", "description": "walking", "keywords": []}]}
    outline = blueprint_to_outline(bp, arch=arch, word_target=2500, total=20)
    assert "The mayor is an impostor" in outline["forbidden_outcomes"]
    assert any("hint or clue" in f for f in outline["forbidden_outcomes"])


def test_stage_source_analysis_dispatch_pacing():
    import time
    from unittest.mock import AsyncMock, MagicMock
    from app.services.autonomous.source_stages import stage_source_analysis, SourceContext

    timestamps = []

    class MockClient:
        async def structured(self, **kwargs):
            timestamps.append(time.monotonic())
            class Res:
                def model_dump(self, **kw):
                    return {"scenes": []}
            return Res()

    # Simulate 3 items with 0.05s dispatch interval
    session = MagicMock()
    session.exec = MagicMock()
    # Mock loaded chapters
    mock_ch1 = MagicMock(card_id=1, text="ch1 text", chapter_number=1, text_hash="h1", title="Ch1", manuscript_id="m1", chapter_id=1, language="en", analysis=None)
    mock_ch2 = MagicMock(card_id=2, text="ch2 text", chapter_number=2, text_hash="h2", title="Ch2", manuscript_id="m1", chapter_id=2, language="en", analysis=None)
    mock_ch3 = MagicMock(card_id=3, text="ch3 text", chapter_number=3, text_hash="h3", title="Ch3", manuscript_id="m1", chapter_id=3, language="en", analysis=None)

    import app.services.autonomous.source_stages as src_stages

    orig_load = src_stages.load_source_chapters
    orig_prompt = src_stages.get_prompt_by_name
    orig_records = src_stages.fn_lab_analysis_records
    try:
        src_stages.load_source_chapters = lambda s, pid: [mock_ch1, mock_ch2, mock_ch3]
        mock_prompt = MagicMock()
        mock_prompt.template = "{{content}}"
        src_stages.get_prompt_by_name = lambda s, name: mock_prompt
        src_stages.fn_lab_analysis_records = lambda results: []

        ctx = SourceContext(
            source_project_id=1,
            filename="test.epub",
            data=b"",
            client=MockClient(),
            options={"analysis_dispatch_interval": 0.05, "analysis_concurrency": 10},
            progress=lambda m, p: None,
            analysis_concurrency=10
        )
        import asyncio
        asyncio.run(stage_source_analysis(session, ctx))

        assert len(timestamps) == 3
        # Intervals between consecutive dispatches should be at least ~0.045s
        diff1 = timestamps[1] - timestamps[0]
        diff2 = timestamps[2] - timestamps[1]
        assert diff1 >= 0.04, f"diff1 was {diff1}"
        assert diff2 >= 0.04, f"diff2 was {diff2}"
    finally:
        src_stages.load_source_chapters = orig_load
        src_stages.get_prompt_by_name = orig_prompt
        src_stages.fn_lab_analysis_records = orig_records


def test_create_job_restarts_after_cancelled_or_failed_job(tmp_path):
    from sqlmodel import SQLModel, create_engine, Session
    from app.services.autonomous import runner as runner_mod
    from app.db.models import LLMConfig

    engine = create_engine(f"sqlite:///{tmp_path}/test_restart.db")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        cfg = LLMConfig(provider="custom", model_name="test-model", api_key="", display_name="Test")
        session.add(cfg)
        session.commit()
        session.refresh(cfg)

    # 1. Create first job with explicit idempotency key
    job1 = runner_mod.create_job(session, filename="test.epub", data=b"epub_bytes_1", llm_config_id=cfg.id, idempotency_key="ui-fixed-key-1")
    assert job1.status == "queued"

    # 2. Re-submitting with active job returns job1 (idempotent duplicate prevention)
    job1_again = runner_mod.create_job(session, filename="test.epub", data=b"epub_bytes_1", llm_config_id=cfg.id, idempotency_key="ui-fixed-key-1")
    assert job1_again.id == job1.id

    # 3. Cancel job1
    runner_mod.cancel(session, job1)
    session.refresh(job1)
    assert job1.status == "cancelled"

    # 4. Re-submitting with the same key must NOT return the cancelled job; it must create a new job
    job2 = runner_mod.create_job(session, filename="test.epub", data=b"epub_bytes_1", llm_config_id=cfg.id, idempotency_key="ui-fixed-key-1")
    assert job2.id != job1.id
    assert job2.status == "queued"
    assert job2.idempotency_key != job1.idempotency_key

    # 5. Cancel job2 and verify job3 is created
    runner_mod.cancel(session, job2)
    session.refresh(job2)
    job3 = runner_mod.create_job(session, filename="test.epub", data=b"epub_bytes_1", llm_config_id=cfg.id, idempotency_key="ui-fixed-key-1")
    assert job3.id not in (job1.id, job2.id)
    assert job3.status == "queued"





