"""Prose Craft: tics, critic, hooks, scene planning, subtext, and the multi-pass pipeline — no live model calls.

Unit tests cover every deterministic analyzer. The pipeline tests reuse the
synthetic Forge project fixtures and a scene-aware fake drafter whose first
draft is deliberately full of AI tics with a soft ending, so every craft pass
(scene drafting, critic, polish, hook) has real work to do and the assertions
can check that the passes improved the measurable signals without adding facts.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests import test_forge_pipeline as pl  # noqa: E402

pytestmark = pytest.mark.timeout(600)


# ================================================================ unit: tics
TIC_HEAVY = (
    "It wasn't just fear, but something older. Nadia stood in the doorway, a silent testament to the harbor's patience.\n\n"
    "In that moment, something inside her shifted. She let out a breath she didn't know she was holding.\n\n"
    "\"You came,\" Teo said softly. He was the man who had always known she would.\n\n"
    "A mix of relief and dread washed over her. She found herself nodding. She needed to process her emotions and set boundaries.\n\n"
    "The lamplight was a tapestry of gold and shadow, a reminder that nothing here was hers."
)

CLEAN = (
    "Eleven rivets. Same as yesterday. I counted them anyway, because counting is cheaper than thinking.\n\n"
    "Teo was already at the rail. He did not turn. \"You were at the Archive.\"\n\n"
    "\"I am at a lot of places.\"\n\n"
    "He let that sit. Salt and old rope on the wind; the tide bell rang once and stopped, which it never does.\n\n"
    "So: he knew where I had been, and he wanted me to know he knew. Two facts for the price of one. I filed them.\n\n"
    "\"Go below, Nadia.\"\n\n"
    "\"Not yet.\"\n\n"
    "Behind him, under the last lamp, somebody had raised the harbor chain. Nobody had rung for it."
)


def test_tics_catalogue_flags_the_known_patterns():
    from app.services.forge.craft import tics

    hits = tics.find_tics(TIC_HEAVY)
    codes = {h.code for h in hits}
    # Zero-tolerance rules fire on the first hit.
    for expected in ("silent_testament", "something_shifted", "breath_didnt_know", "mix_of_emotions", "therapy_speak", "tapestry_of", "reminder_that"):
        assert expected in codes, (expected, sorted(codes))
    # Tolerated rules (one free occurrence) fire from the second hit onward.
    doubled = TIC_HEAVY + "\n\nIt wasn't just habit, but ritual. In that moment he was the man who never left. She found herself smiling, she found herself waiting. \"Fine,\" he said quietly. \"Go,\" she said softly. \"Now,\" he said gently."
    codes2 = {h.code for h in tics.find_tics(doubled)}
    for expected in ("not_x_but_y", "in_that_moment", "the_man_who", "found_himself", "adverb_said"):
        assert expected in codes2, (expected, sorted(codes2))
    assert all(h.span[0] < h.span[1] and TIC_HEAVY[h.span[0]:h.span[1]].strip() for h in hits)
    summary = tics.tic_summary(hits)
    assert summary["total"] == len(hits) and summary["high"] >= 3
    # Sorted by position, spans point at real text.
    assert [h.span[0] for h in hits] == sorted(h.span[0] for h in hits)


def test_tics_clean_prose_is_clean_and_tolerances_hold():
    from app.services.forge.craft import tics

    hits = tics.find_tics(CLEAN)
    assert not hits, [h.as_dict() for h in hits]
    # One 'in that moment' is tolerated (max_per_chapter=1); the second is flagged.
    two = "In that moment she knew. Later, in that moment, she knew again."
    codes = [h.code for h in tics.find_tics(two)]
    assert codes.count("in_that_moment") == 1
    assert tics.tics_score(0.0, 0) == 10 and tics.tics_score(6.0, 4) == 1


# ============================================================== unit: hooks
def test_hook_analysis_soft_vs_sharp():
    from app.services.forge.craft import hooks

    soft = CLEAN.rsplit("\n\n", 3)[0] + "\n\nThe lamps went out one by one and the harbor settled into its long, quiet dark. She slept."
    sharp = CLEAN
    a, b = hooks.analyze_hook(soft), hooks.analyze_hook(sharp, closing_hook_plan="someone arrives at the door")
    assert a.is_soft and a.strength < b.strength
    assert b.hook_type in ("threat_arrival", "revelation", "reversal", "crisis") and not b.is_soft
    assert b.suggested_hook == "threat_arrival"
    assert "deduction" in b.micro_payoffs and not b.payoff_missing
    assert a.payoff_missing is False  # the deduction survives in the soft variant's body
    split = hooks.split_for_hook_rewrite(sharp, paragraphs=2)
    assert split["keep"] and split["tail"].startswith("\"Not yet.\"")


# ============================================================= unit: critic
def test_deterministic_critic_scores_and_verdicts():
    from app.services.forge.craft import critic, hooks

    bad = critic.deterministic_critic(TIC_HEAVY, hook=hooks.analyze_hook(TIC_HEAVY), pov_name="Nadia")
    good = critic.deterministic_critic(CLEAN, hook=hooks.analyze_hook(CLEAN), pov_name="Nadia")
    assert bad.overall < good.overall
    assert bad.verdict in ("polish", "rewrite") and bad.tic_count >= 8
    assert bad.scores["ai_tics"] <= 3 and good.scores["ai_tics"] == 10
    assert good.scores["interiority"] >= bad.scores["interiority"]
    dims = {f.dimension for f in bad.findings}
    assert "authenticity" in dims and "voice" in dims
    assert critic.needs_polish(bad)
    # Merge takes the lower score per dimension and keeps the model's strongest moment.
    from app.schemas.craft import CriticFinding, CriticReport

    model = CriticReport(scores={"dialogue": 3, "hook": 9}, overall=6.0, verdict="polish", strongest_moment="Eleven rivets.", findings=[CriticFinding(dimension="dialogue", severity="high", quote="You were at the Archive.", problem="stiff", fix="add a beat")], source="model")
    merged = critic.merge_reports(good, model)
    assert merged.scores["dialogue"] == 3 and merged.scores["hook"] == good.scores["hook"]
    assert merged.strongest_moment == "Eleven rivets." and merged.source == "merged"
    assert any(f.quote == "You were at the Archive." for f in merged.findings)


# ======================================================= unit: scene planning
BEATS = pl._outline(1)["beats"]


def test_plan_scenes_groups_beats_and_covers_all():
    from app.services.forge.craft import scenes

    plan = scenes.plan_scenes(BEATS, participants=["Nadia Quill", "Teo Marsh", "Corvin Ashe"], pov="Nadia Quill", word_target=2600, closing_hook="Nadia refuses to go below")
    assert scenes.MIN_SCENES <= len(plan) <= scenes.MAX_SCENES
    covered = [b for s in plan for b in s.beat_indexes]
    assert covered == list(range(1, len(BEATS) + 1))
    assert abs(sum(s.word_share for s in plan) - 1.0) < 0.01
    assert plan[-1].exit_state.startswith("the chapter hook")
    # <2 beats -> a single scene; empty -> none.
    assert len(scenes.plan_scenes(BEATS[:1], participants=["Nadia Quill"], pov="Nadia Quill", word_target=1000)) == 1
    assert scenes.plan_scenes([], participants=[], pov="x", word_target=1000) == []


def test_handoff_extraction_and_brief_rendering():
    from app.services.forge.craft import scenes

    h = scenes.extract_handoff(CLEAN)
    assert "Not yet" in h.open_dialogue or "Go below" in h.open_dialogue
    assert h.ending_lines.endswith("Nobody had rung for it.")
    assert h.carried_tension
    plan = scenes.plan_scenes(BEATS, participants=["Nadia Quill", "Teo Marsh"], pov="Nadia Quill", word_target=2000)
    brief1 = scenes.render_scene_brief(plan[0], BEATS, word_target=2000, scene_count=len(plan), handoff=None, pov="Nadia Quill")
    brief2 = scenes.render_scene_brief(plan[1], BEATS, word_target=2000, scene_count=len(plan), handoff=h, pov="Nadia Quill")
    assert "THIS SCENE — 1 of" in brief1 and "chapter opening" in brief1 and "No <chapter_summary>" in brief1
    assert "PREVIOUS SCENE — EXACT ENDING" in brief2 and "Nobody had rung for it." in brief2
    last = scenes.render_scene_brief(plan[-1], BEATS, word_target=2000, scene_count=len(plan), handoff=h, pov="Nadia Quill")
    assert "final scene" in last and "<chapter_summary>" in last
    assert scenes.stitch(["a", "", "b"]) == "a\n\n\nb"


# ============================================================ unit: subtext
def test_subtext_packet_from_bible_cards():
    from app.schemas.craft import ScenePlan
    from app.services.forge.craft import subtext

    teo = {"name": "Teo Marsh", "role_type": "Deuteragonist", "core_drive": "reclaim his post", "dramatic_design": {"external_goal": "get Nadia to trust him", "secrets": ["he was sent to watch her"], "greatest_fear": "being useless", "public_image": "loyal guard", "self_image": "a spy who hates it", "coping_mechanism": "jokes"}, "voice": {"sentence_tendency": "short, sideways", "forms_of_address": ["'Quill' to Nadia"], "forbidden_speech": ["I beg you"], "deception_style": "looks at the water"}}
    nadia = {"name": "Nadia Quill", "personality": "dry", "core_drive": "protect the archive", "dramatic_design": {"external_goal": "keep the manifest", "internal_need": "to trust someone", "false_belief": "knowledge is safest unshared", "public_image": "composed clerk"}, "voice": {"sentence_tendency": "clipped", "humor_style": "deadpan", "forbidden_speech": ["I beg you"]}, "competence": {"blind_spots": ["loyalty read as weakness"]}}
    scene = ScenePlan(index=2, title="Confrontation", beat_indexes=[2, 3], present=["Nadia Quill", "Teo Marsh"], dramatic_question="Will Teo admit why he is here?")
    packet = subtext.build_packet(scene, pov_name="Nadia Quill", cards_by_name={"teo marsh": teo, "nadia quill": nadia}, relationships={"teo marsh": {"private_relationship": "wary allies", "trust": 4, "unresolved_tension": "he lied about the chalk"}}, knowledge_gaps={"teo marsh": ["Corvin is the courier"]})
    assert packet.scene_index == 2 and len(packet.agendas) == 1
    a = packet.agendas[0]
    assert "trust him" in a.wants_from_pov and "sent to watch her" in a.suppressing and "Corvin is the courier" in a.suppressing
    assert a.address_pov_as == "'Quill' to Nadia" and "I beg you" in a.never_says and "wary allies" in a.relationship_now
    assert "to trust someone" in packet.pov_private_agenda
    assert "loyal guard" in packet.pov_reads_wrong
    text = subtext.render_packet(packet, "Nadia Quill")
    assert "Teo Marsh:" in text and "never says" in text and "nobody states their want directly" in text.lower()
    # Voice: derived when the card has no explicit profile, verbatim when it does.
    derived = subtext.voice_from_card(nadia)
    assert derived.archetype == "dry" and "therapy vocabulary" in derived.forbidden_interior
    explicit = subtext.voice_from_card({**nadia, "protagonist_voice": {"archetype": "cynical pragmatist", "inner_register": "ledger-keeper", "notices_first": ["exits"]}})
    assert explicit.archetype == "cynical pragmatist"
    rendered = subtext.render_voice(explicit, "Nadia Quill")
    assert "cynical pragmatist" in rendered and "exits" in rendered


# ======================================================= pipeline: fake drafter
def _scene_prose(n: int, scene_idx: int, scene_count: int, *, tics: bool) -> str:
    """One scene of the synthetic chapter. Scene-aware so the stitched chapter still satisfies every Forge validator."""
    other = "Teo" if n % 3 else "Corvin"
    parts: Dict[int, List[str]] = {
        1: [["The tide bell rang once.", "Rope and rust on Kestrel Row.", "Teo was already at the rail when I came up.", "Nobody on Harrow Quay locks a hatch they mean to open."][n % 4], "I counted the rivets on the hatch. Eleven. Same as yesterday.", f"{other} did not look round. \"You were at the Archive.\"", "\"I am at a lot of places.\"", "Quiet, for a breath."],
        2: [f"Page {n} of the manifest stayed blank. I wrote the date.", "Teo said the quay was clear. I let him say it.", "I checked the locker. I checked the hatch. I checked the locker again."],
        3: ["Petra came by with bread and no news. \"Clear night,\" she said. I nodded.", "I went out regardless.", ["Then the Archive lamp died in its window. No wind that night.", "Then a chalk mark appeared on our hatch. Fresh. Still damp.", "Then Petra did not come back from the Row.", "Then somebody raised the harbor chain, and nobody had rung for it."][n % 4], "I kept walking. Running admits something.", f"Harrow Quay had gone dark before I turned the corner. {'Corvin' if other == 'Teo' else 'Teo'} stood under the last lamp.", "\"Go below, Nadia.\"", "\"Not yet.\""],
    }
    # Map any scene count onto the three content blocks.
    if scene_count == 1:
        paras = parts[1] + parts[2] + parts[3]
    elif scene_idx == 1:
        paras = parts[1]
    elif scene_idx == scene_count:
        paras = parts[3]
    else:
        paras = parts[2]
    if tics and scene_idx == 1:
        paras.insert(2, "It wasn't just habit, but something older. In that moment, something inside me shifted; I let out a breath I didn't know I was holding.")
    if tics and scene_idx == scene_count:
        # Soft fade-out instead of the question hook; also 'a testament'.
        paras = paras[:-2] + ["The lamplight was a silent testament to the harbor's patience, and I let the long quiet settle over the quay.", "I went below after all and slept."]
    return "\n\n".join(paras)


class CraftDrafter:
    """Scene-aware fake: drafts scenes with tics, then behaves like a good critic/polisher/hook editor."""

    def __init__(self, *, model_plan: bool = True, critic_json: bool = True):
        self.calls: List[Dict[str, Any]] = []
        self.model_plan = model_plan
        self.critic_json = critic_json

    def _scene_meta(self, user_prompt: str) -> Optional[Dict[str, int]]:
        import re

        m = re.search(r"\[THIS SCENE — (\d+) of (\d+):", user_prompt)
        return {"idx": int(m.group(1)), "count": int(m.group(2))} if m else None

    async def __call__(self, *, role: str, system_prompt: str, user_prompt: str, context) -> str:
        n = context.chapter_number
        self.calls.append({"role": role, "chapter": n, "user_prompt": user_prompt})
        claims = {"claims": [], "summary": "Nadia is confronted, reassured, then the quay escalates; ends on a question.", "ending_location": "Harrow Quay", "current_time": "night", "unresolved_immediate_action": "Nadia refuses to go below", "open_dialogue_obligation": ""}
        blocks = "\n<claims>" + json.dumps(claims) + "</claims>"
        if role == "scene_planner":
            if not self.model_plan:
                return "no json here"
            beats = len(pl._outline(n)["beats"])
            return json.dumps({"planning_thinking": "three moves", "scenes": [
                {"index": 1, "title": "Rivets", "beat_indexes": [1, 2], "present": ["Nadia Quill", "Teo Marsh"], "dramatic_question": "What does Teo know?", "turn": "Teo names the Archive", "interiority_focus": "counting as control", "micro_payoff": "two facts for one", "word_share": 0.35},
                {"index": 2, "title": "Blank page", "beat_indexes": [3], "present": ["Nadia Quill", "Teo Marsh"], "dramatic_question": "Is the quay clear?", "turn": "Nadia lets the lie stand", "word_share": 0.2},
                {"index": 3, "title": "The chain", "beat_indexes": list(range(4, beats + 1)), "present": ["Nadia Quill", "Corvin Ashe"], "dramatic_question": "Who raised the chain?", "turn": "Nadia refuses to go below", "word_share": 0.45},
            ]})
        if role == "drafting":
            meta = self._scene_meta(user_prompt)
            if meta is None:
                return _scene_prose(n, 1, 1, tics=True) + blocks
            text = _scene_prose(n, meta["idx"], meta["count"], tics=True)
            return text + (blocks if meta["idx"] == meta["count"] else "")
        if role == "critic":
            if not self.critic_json:
                return "I think it's fine."
            return json.dumps({"scores": {"authenticity": 4, "voice": 6, "interiority": 5, "dialogue": 7, "pacing": 6, "sensory": 6, "hook": 2, "payoff": 5}, "overall": 5.2, "verdict": "polish", "strongest_moment": "I counted the rivets on the hatch. Eleven. Same as yesterday.", "findings": [{"dimension": "dialogue", "severity": "medium", "quote": "You were at the Archive.", "problem": "no reaction beat", "fix": "add interior read"}]})
        if role == "polish":
            # A surgical polish: drop the tic sentence, keep everything else verbatim.
            body = user_prompt.split("[CHAPTER]\n", 1)[1]
            return "\n\n".join(p for p in body.split("\n\n") if "wasn't just habit" not in p and "silent testament" not in p)
        if role == "hook":
            return "\"Go below, Nadia.\"\n\n\"Not yet.\"\n\nBehind him somebody had raised the harbor chain, and nobody had rung for it."
        if role == "repair":
            return _scene_prose(n, 1, 1, tics=False) + blocks
        raise AssertionError(f"unexpected role {role}")


@pytest.fixture(scope="module")
def client(app_client):
    return app_client


@pytest.fixture(scope="module")
def state() -> Dict[str, Any]:
    return {}


def _setup(client, state) -> None:
    from tests import test_forge_continuity as cont

    cont._setup_projects(client, state)
    # Give the protagonist an explicit voice profile so the compiler emits the voice section.
    cards = pl._cards(client, state["original_pid"], "Character Card")
    nadia = next(c for c in cards if c["content"]["name"] == "Nadia Quill")
    content = {**nadia["content"], "protagonist_voice": {"archetype": "cynical pragmatist", "inner_register": "ledger-keeper; counts things to stay calm", "notices_first": ["exits", "who is lying"], "private_humor": "other people's certainty", "self_deception": "that she prefers being alone", "calculation_style": "worst case first", "composure_mask": "the quiet clerk", "signature_moves": ["grades people out of eleven"], "forbidden_interior": ["self-pity"]}}
    assert client.put(f"/api/cards/{nadia['id']}", json={"content": content}).status_code == 200


def test_pipeline_full_craft_scene_by_scene(client, state):
    from app.db.session import engine
    from app.services.forge.craft import CraftOptions
    from app.services.forge.pipeline import PipelineOptions, run_chapter

    _setup(client, state)
    opid = state["original_pid"]
    drafter = CraftDrafter()
    with Session(engine) as s:
        res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=1, drafter=drafter, options=PipelineOptions(max_repairs=2, craft=CraftOptions.full())))
    assert res.status == "committed", json.dumps(res.error, default=str)[:4000]
    craft = res.craft
    assert craft["mode"] == "scene_by_scene" and len(craft["scenes"]) == 3
    roles = [c["role"] for c in drafter.calls]
    assert roles[0] == "scene_planner" and roles.count("drafting") == 3
    assert "critic" in roles and "polish" in roles and "hook" in roles
    # Scene 2 and 3 prompts carry the previous scene's exact ending and the subtext packet.
    draft_prompts = [c["user_prompt"] for c in drafter.calls if c["role"] == "drafting"]
    assert "PREVIOUS SCENE — EXACT ENDING" in draft_prompts[1] and "SUBTEXT PACKET — scene 2" in draft_prompts[1]
    assert "Same as yesterday." in draft_prompts[1]  # verbatim handoff from scene 1
    assert "PROTAGONIST VOICE" in draft_prompts[0] and "cynical pragmatist" in draft_prompts[0]
    # Critic before/after: tics removed, hook sharpened, score up.
    before, after = craft["critic_before"], craft["critic_after"]
    assert before["tic_count"] >= 3 and after["tic_count"] < before["tic_count"]
    assert after["overall"] > before["overall"]
    assert craft["hook_before"]["is_soft"] and not craft["hook_after"]["is_soft"]
    assert craft["hook_after"]["strength"] > craft["hook_before"]["strength"]
    names = [p["name"] for p in craft["passes"]]
    assert names[:2] == ["scene_plan", "draft_scenes"] and "polish" in names and "hook" in names and "critic_after" in names
    assert any(p["name"] == "polish" and p["changed"] for p in craft["passes"])
    assert any(p["name"] == "hook" and p["changed"] for p in craft["passes"])
    assert craft["accepted"] is True
    # The committed prose is the polished/hooked text and metadata blocks were preserved.
    assert "silent testament" not in res.prose and "Nobody had rung for it" in res.prose or "nobody had rung for it" in res.prose
    assert res.model_calls == craft["model_calls"] == len(drafter.calls)
    assert res.validation["craft"]["mode"] == "scene_by_scene"


def test_pipeline_craft_degrades_gracefully_when_model_outputs_are_unusable(client, state):
    from app.db.session import engine
    from app.services.forge.craft import CraftOptions
    from app.services.forge.pipeline import PipelineOptions, run_chapter

    opid = state["original_pid"]
    drafter = CraftDrafter(model_plan=False, critic_json=False)
    with Session(engine) as s:
        res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=2, drafter=drafter, options=PipelineOptions(max_repairs=2, craft=CraftOptions.full())))
    assert res.status == "committed", json.dumps(res.error, default=str)[:4000]
    craft = res.craft
    plan = next(p for p in craft["passes"] if p["name"] == "scene_plan")
    assert "rejected" in plan["note"] and craft["mode"] == "scene_by_scene"  # deterministic plan kept
    crit = next(p for p in craft["passes"] if p["name"] == "critic_before")
    assert "model unusable" in crit["note"]
    assert craft["critic_after"]["overall"] >= craft["critic_before"]["overall"]


def test_pipeline_legacy_single_shot_unchanged_and_economy_preset(client, state):
    from app.db.session import engine
    from app.services.forge.craft import CraftOptions
    from app.services.forge.pipeline import PipelineOptions, run_chapter

    opid = state["original_pid"]
    legacy = pl.FakeDrafter()
    with Session(engine) as s:
        res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=3, drafter=legacy, options=PipelineOptions(max_repairs=2)))
    assert res.status == "committed" and res.craft == {} and [c["role"] for c in legacy.calls] == ["drafting"]
    econ = pl.FakeDrafter()
    with Session(engine) as s:
        res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=4, drafter=econ, options=PipelineOptions(max_repairs=2, craft=CraftOptions.preset("economy"))))
    assert res.status == "committed"
    assert [c["role"] for c in econ.calls] == ["drafting"]  # critic graded, nothing rewritten
    assert "PROTAGONIST VOICE" in econ.calls[0]["user_prompt"]
    assert res.craft["mode"] == "single_shot" and res.craft["critic_before"] and res.craft["critic_after"]
    assert not any(p["name"] in ("polish", "hook") for p in res.craft["passes"])


def test_craft_presets_and_autonomous_mapping():
    from app.services.autonomous.chapter_loop import craft_options_for
    from app.services.forge.craft import CraftOptions

    assert CraftOptions.preset("full").scene_by_scene and CraftOptions.preset("full").model_critic
    assert not CraftOptions.preset("economy").scene_by_scene and CraftOptions.preset("economy").critic
    assert craft_options_for({}) is not None and craft_options_for({})
    assert craft_options_for({"quality_preset": "quality"}).model_scene_plan
    assert craft_options_for({"quality_preset": "economy"}).polish is False
    assert craft_options_for({"craft_preset": "off"}) is None
    assert craft_options_for({"craft_preset": "full", "quality_preset": "economy"}).model_scene_plan  # explicit wins


# ================================================================== API
def test_api_grade_and_presets(client):
    r = client.post("/api/craft/grade", json={"text": TIC_HEAVY, "pov": "Nadia", "closing_hook": "Teo arrives"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["critic"]["tic_count"] >= 8 and body["needs_polish"] is True and body["hook"]["suggested_hook"] == "threat_arrival"
    assert len(body["tics"]) == body["tic_summary"]["total"]
    r = client.post("/api/craft/grade", json={"text": CLEAN, "pov": "Nadia"})
    assert r.json()["critic"]["tic_count"] == 0
    assert {p["name"] for p in client.get("/api/craft/presets").json()} == {"off", "economy", "balanced", "full"}
    cat = client.get("/api/craft/tics/catalogue").json()
    assert any(c["code"] == "breath_didnt_know" for c in cat)


def test_api_scene_plan_for_outline(client, state):
    opid = state["original_pid"]
    r = client.post("/api/craft/scene-plan", json={"project_id": opid, "chapter_number": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pov"] == "Nadia Quill" and len(body["scenes"]) >= 2 and len(body["subtext"]) == len(body["scenes"])
    assert "cynical pragmatist" in body["voice"]
    assert any(a["name"] == "Teo Marsh" for p in body["subtext"] for a in p["agendas"])
    r = client.post("/api/craft/scene-plan", json={"project_id": opid, "chapter_number": 999})
    assert r.status_code == 404
    # Grade a committed chapter card.
    cards = pl._cards(client, opid, "Chapter Text")
    card = next(c for c in cards if int(c["content"].get("chapter_number") or 0) == 1)
    r = client.post("/api/craft/grade/card", json={"project_id": opid, "card_id": card["id"]})
    assert r.status_code == 200 and r.json()["critic"]["overall"] > 0


def test_character_deepening_schema_carries_protagonist_voice():
    from app.schemas.bible import CharacterBibleDeepening
    from app.schemas.entity import CharacterCard
    from app.utils.schema_utils import filter_schema_for_ai

    assert "protagonist_voice" in filter_schema_for_ai(CharacterBibleDeepening.model_json_schema())["properties"]
    assert "protagonist_voice" not in filter_schema_for_ai(CharacterCard.model_json_schema())["properties"]
    card = CharacterCard.model_validate({"name": "X", "entity_type": "character", "life_span": "Long Term", "born_scene": "", "description": "", "personality": "", "core_drive": "", "character_arc": "", "protagonist_voice": {"archetype": "deadpan observer"}})
    assert card.protagonist_voice and card.protagonist_voice.archetype == "deadpan observer"
