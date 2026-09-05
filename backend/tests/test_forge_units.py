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
