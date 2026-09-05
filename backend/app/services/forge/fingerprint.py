"""Layered Narrative Fingerprint.

Producer: ``build_fingerprint`` (deterministic metrics over every imported
chapter + verified observations from chapter analyses).
Consumers: ``compiler`` (compact representation in every generation context),
``style_eval`` (target ranges), ``validators`` (negative constraints), UI.

Each layer is a ``FingerprintLayer`` with measurable features, qualitative
rules, source references (chapter numbers / observation ids, never long
quotes), anti-patterns, confidence, applicability and a compact prompt line.
The whole fingerprint carries a version and a dependency hash over the chapter
text hashes it was built from, so a manuscript change makes it stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.services.forge.evidence import verified_observations
from app.services.forge.textmetrics import aggregate, infer_pov, measure, stable_id

FINGERPRINT_VERSION = "fingerprint-1"

LAYERS: Sequence[str] = (
    "global_voice", "pov_focalization", "rhythm", "dialogue", "internal_monologue", "exposition", "humor",
    "emotional_expression", "action_scene", "romance_scene", "suspense_reveal", "chapter_opening", "chapter_ending",
    "scene_transition", "character_voices", "beat_execution", "pacing_reward", "korean_register",
    "negative_constraints", "evidence_index",
)


@dataclass
class FingerprintLayer:
    layer: str
    features: Dict[str, Any] = field(default_factory=dict)
    rules: List[str] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)  # {"chapter": n, "observation_id": ...}
    anti_patterns: List[str] = field(default_factory=list)
    confidence: float = 0.5
    supporting_chapters: List[int] = field(default_factory=list)
    applicability: List[str] = field(default_factory=list)
    compact: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _band(dist: Dict[str, float], unit: str = "") -> str:
    if not dist or not dist.get("n"):
        return "n/a"
    return f"{dist.get('p10', 0):.2f}–{dist.get('p90', 0):.2f}{unit} (median {dist.get('median', 0):.2f})"


def _top(cat: Dict[str, float], k: int = 2) -> str:
    return ", ".join(f"{name} {share:.0%}" for name, share in list(cat.items())[:k]) if cat else "n/a"


def _obs_by_prefix(observations: List[Dict[str, Any]], prefixes: Iterable[str]) -> List[Dict[str, Any]]:
    pref = tuple(prefixes)
    return [o for o in observations if str(o.get("category") or "").startswith(pref)]


def _refs(obs: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, Any]]:
    return [{"chapter": o.get("chapter_number"), "observation_id": o.get("observation_id"), "observation": str(o.get("observation") or "")[:140]} for o in obs[:limit]]


def _chapters(obs: List[Dict[str, Any]]) -> List[int]:
    return sorted({int(o.get("chapter_number") or 0) for o in obs if o.get("chapter_number")})


def build_fingerprint(
    chapters: Iterable[Any],
    *,
    manuscript_id: str,
    analyses: Optional[Dict[int, Dict[str, Any]]] = None,
    character_roles: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build the 20-layer fingerprint.

    ``chapters`` are ``corpus.SourceChapter`` objects (or objects with ``text``,
    ``chapter_number``, ``language``, ``text_hash``). ``analyses`` maps chapter
    number -> verified ChapterAnalysis dict (only verified observations are used).
    """
    chapter_list = list(chapters)
    per_chapter: List[Dict[str, Any]] = []
    hashes: List[str] = []
    all_obs: List[Dict[str, Any]] = []
    techniques: Dict[str, int] = {}
    hook_types: Dict[str, int] = {}
    scene_counts: List[int] = []
    scene_functions: Dict[str, int] = {}
    emotions: List[Dict[str, Any]] = []
    reward_gaps: List[int] = []
    last_reward: Optional[int] = None
    for ch in chapter_list:
        text = getattr(ch, "text", "")
        num = int(getattr(ch, "chapter_number", 0) or 0)
        m = measure(text, getattr(ch, "language", None)).as_dict()
        m["chapter_number"] = num
        per_chapter.append(m)
        hashes.append(str(getattr(ch, "text_hash", "") or stable_id(text)))
        an = (analyses or {}).get(num) or {}
        if an:
            all_obs += verified_observations(an)
            for t in an.get("techniques") or []:
                if isinstance(t, str):
                    techniques[t] = techniques.get(t, 0) + 1
            if an.get("hook_type"):
                hook_types[str(an["hook_type"])] = hook_types.get(str(an["hook_type"]), 0) + 1
            scenes = an.get("scenes") or []
            scene_counts.append(len(scenes))
            for s in scenes:
                if isinstance(s, dict) and s.get("function"):
                    scene_functions[str(s["function"])] = scene_functions.get(str(s["function"]), 0) + 1
            emo = an.get("emotion") or {}
            if isinstance(emo, dict):
                emotions.append({"chapter": num, **{k: emo.get(k) for k in ("tension", "satisfaction", "curiosity", "humor", "intimacy")}, "rewards": emo.get("rewards") or []})
                if emo.get("rewards"):
                    if last_reward is not None:
                        reward_gaps.append(num - last_reward)
                    last_reward = num

    agg = aggregate(per_chapter)
    lang = agg.get("language", "und")
    n = len(per_chapter)
    conf_base = min(1.0, 0.35 + 0.05 * n)
    chapter_numbers = [m["chapter_number"] for m in per_chapter]
    pov_votes: Dict[str, int] = {}
    for m in per_chapter:
        p = "first_person" if m["first_person_ratio"] >= 0.55 else ("third_person" if m["third_person_ratio"] >= 0.55 else "mixed")
        pov_votes[p] = pov_votes.get(p, 0) + 1
    dominant_pov = max(pov_votes.items(), key=lambda kv: kv[1])[0] if pov_votes else "unknown"
    pov_stability = (pov_votes.get(dominant_pov, 0) / n) if n else 0.0
    layers: Dict[str, FingerprintLayer] = {}

    def add(layer: FingerprintLayer) -> None:
        layers[layer.layer] = layer

    add(FingerprintLayer(
        layer="global_voice",
        features={"language": lang, "past_tense_ratio": agg.get("past_tense_ratio"), "sensory_density_per_k": agg.get("sensory_density"), "metaphor_density_per_k": agg.get("metaphor_density"), "information_density_exposition": agg.get("exposition_ratio")},
        rules=[f"Write in {lang.upper()}", f"Sensory detail ~{agg['sensory_density']['median']:.1f} cues per 1000 units", f"Figurative language ~{agg['metaphor_density']['median']:.1f} per 1000 units"],
        examples=_refs(_obs_by_prefix(all_obs, ("chapter",))),
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["all"],
        compact=f"language={lang}; sensory/k={_band(agg['sensory_density'])}; metaphor/k={_band(agg['metaphor_density'])}; past-tense={agg['past_tense_ratio'].get('median', 0):.2f}",
    ))
    add(FingerprintLayer(
        layer="pov_focalization",
        features={"pov": dominant_pov, "pov_stability": round(pov_stability, 3), "first_person_ratio": agg.get("first_person_ratio"), "third_person_ratio": agg.get("third_person_ratio")},
        rules=[f"Dominant POV: {dominant_pov} (stable in {pov_stability:.0%} of chapters)", "Never head-hop inside a scene; knowledge stays with the POV character"],
        anti_patterns=["omniscient asides", "reporting another character's private thoughts"],
        confidence=conf_base * (0.6 + 0.4 * pov_stability), supporting_chapters=chapter_numbers, applicability=["all"],
        compact=f"POV {dominant_pov} (stability {pov_stability:.0%}); no head-hopping",
    ))
    add(FingerprintLayer(
        layer="rhythm",
        features={"sentence_len_mean": agg.get("sentence_len_mean"), "paragraph_len_mean": agg.get("paragraph_len_mean"), "short_paragraph_ratio": agg.get("short_paragraph_ratio"), "fragment_freq": agg.get("fragment_freq"), "ellipsis_per_k": agg.get("ellipsis_freq"), "dash_per_k": agg.get("dash_freq"), "chapter_units": agg.get("unit_count")},
        rules=[f"Mean sentence length {_band(agg['sentence_len_mean'], ' units')}", f"Mean paragraph length {_band(agg['paragraph_len_mean'], ' units')}", f"Short paragraphs (<=15 units): {_band(agg['short_paragraph_ratio'])}", f"Sentence fragments: {_band(agg['fragment_freq'])} of sentences"],
        anti_patterns=["uniform paragraph length", "long compound sentences in tense moments"],
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["all"],
        compact=f"sentence≈{agg['sentence_len_mean'].get('median', 0):.1f}u [{_band(agg['sentence_len_mean'])}]; paragraph≈{agg['paragraph_len_mean'].get('median', 0):.1f}u; short-para {agg['short_paragraph_ratio'].get('median', 0):.0%}; fragments {agg['fragment_freq'].get('median', 0):.0%}; chapter≈{agg['unit_count'].get('median', 0):.0f}u",
    ))
    add(FingerprintLayer(
        layer="dialogue",
        features={"dialogue_ratio": agg.get("dialogue_ratio"), "dialogue_tag_density_per_k": agg.get("dialogue_tag_density"), "attribution_omission_ratio": agg.get("attribution_omission_ratio"), "question_freq": agg.get("question_freq"), "exclamation_freq": agg.get("exclamation_freq")},
        rules=[f"Dialogue share {_band(agg['dialogue_ratio'])}", f"Omit attribution in ~{agg['attribution_omission_ratio'].get('median', 0):.0%} of lines", "Each speaker's line is its own paragraph"],
        anti_patterns=["adverb-laden dialogue tags", "expository speeches"],
        examples=_refs(_obs_by_prefix(all_obs, ("scene:confrontation", "scene:character_bonding"))),
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["dialogue scenes"],
        compact=f"dialogue {_band(agg['dialogue_ratio'])}; tags/k {agg['dialogue_tag_density'].get('median', 0):.1f}; untagged {agg['attribution_omission_ratio'].get('median', 0):.0%}; questions {agg['question_freq'].get('median', 0):.0%}",
    ))
    add(FingerprintLayer(
        layer="internal_monologue",
        features={"internal_thought_ratio": agg.get("internal_thought_ratio"), "rhetorical_question_freq": agg.get("rhetorical_question_freq")},
        rules=[f"Internal thought share {_band(agg['internal_thought_ratio'])}", f"Rhetorical questions {_band(agg['rhetorical_question_freq'])} of sentences", "Direct thought stays in the POV's idiom; no italic thought dumps longer than a paragraph"],
        examples=_refs(_obs_by_prefix(all_obs, ("scene:decision",))),
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["all"],
        compact=f"inner-thought {_band(agg['internal_thought_ratio'])}; rhetorical-q {agg['rhetorical_question_freq'].get('median', 0):.0%}",
    ))
    add(FingerprintLayer(
        layer="exposition",
        features={"exposition_ratio": agg.get("exposition_ratio"), "techniques": [t for t, _ in sorted(techniques.items(), key=lambda kv: -kv[1]) if "exposit" in t.lower() or "info" in t.lower()][:5]},
        rules=[f"Exposition share {_band(agg['exposition_ratio'])}", "Deliver world facts through conflict or consequence, not lecture"],
        anti_patterns=["multi-paragraph lore dumps", "explaining a rule right before it matters"],
        examples=_refs(_obs_by_prefix(all_obs, ("scene:world_revelation", "scene:setup"))),
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["setup", "world_revelation"],
        compact=f"exposition {_band(agg['exposition_ratio'])}; delivered via conflict/consequence",
    ))
    humor_vals = [e.get("humor") for e in emotions if isinstance(e.get("humor"), (int, float))]
    add(FingerprintLayer(
        layer="humor",
        features={"humor_score_mean": round(sum(humor_vals) / len(humor_vals), 2) if humor_vals else None, "exclamation_freq": agg.get("exclamation_freq")},
        rules=["Comedy comes from understatement and timing (short line after a long build), not from jokes announced as jokes"] if humor_vals and sum(humor_vals) / len(humor_vals) >= 3 else ["Humor is sparse; use dry understatement only"],
        anti_patterns=["slapstick", "characters laughing at their own lines"],
        confidence=conf_base * (0.9 if humor_vals else 0.4), supporting_chapters=[e["chapter"] for e in emotions if (e.get("humor") or 0) >= 5][:20], applicability=["banter", "comedic_reversal"],
        compact=f"humor≈{(sum(humor_vals) / len(humor_vals)) if humor_vals else 0:.1f}/10; dry understatement, timing-based",
    ))
    add(FingerprintLayer(
        layer="emotional_expression",
        features={"explicit_emotion_density_per_k": agg.get("explicit_emotion_density"), "restraint_index": round(1.0 - min(1.0, agg["explicit_emotion_density"].get("median", 0) / 10.0), 3)},
        rules=[f"Explicit emotion words {_band(agg['explicit_emotion_density'])} per 1000 units", "Show emotion through action, silence and withheld subject rather than naming it"],
        anti_patterns=["naming the feeling and then describing it", "tears as default"],
        examples=_refs(_obs_by_prefix(all_obs, ("scene:recovery",))),
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["all"],
        compact=f"explicit-emotion/k {_band(agg['explicit_emotion_density'])}; restraint index {1.0 - min(1.0, agg['explicit_emotion_density'].get('median', 0) / 10.0):.2f}",
    ))
    add(FingerprintLayer(
        layer="action_scene",
        features={"action_ratio": agg.get("action_ratio"), "short_paragraph_ratio": agg.get("short_paragraph_ratio")},
        rules=[f"Action share {_band(agg['action_ratio'])}", "In fights: one motion per sentence, paragraphs shrink, no simultaneous multi-actor summaries"],
        anti_patterns=["blow-by-blow with no consequence", "sudden new abilities"],
        examples=_refs(_obs_by_prefix(all_obs, ("scene:confrontation", "scene:disaster"))),
        confidence=conf_base * (0.9 if agg["action_ratio"].get("median", 0) > 0.03 else 0.5), supporting_chapters=chapter_numbers, applicability=["fight_choreography", "threat_escalation"],
        compact=f"action {_band(agg['action_ratio'])}; one motion per sentence",
    ))
    intimacy = [e.get("intimacy") for e in emotions if isinstance(e.get("intimacy"), (int, float))]
    add(FingerprintLayer(
        layer="romance_scene",
        features={"intimacy_mean": round(sum(intimacy) / len(intimacy), 2) if intimacy else None},
        rules=["Romantic progress advances through protective action and withheld speech; physical description stays minimal"],
        anti_patterns=["explicit declarations before the relationship ledger allows them"],
        examples=_refs(_obs_by_prefix(all_obs, ("scene:character_bonding",))),
        confidence=conf_base * (0.8 if intimacy else 0.4), supporting_chapters=[e["chapter"] for e in emotions if (e.get("intimacy") or 0) >= 5][:20], applicability=["romantic_tension", "confession"],
        compact=f"intimacy≈{(sum(intimacy) / len(intimacy)) if intimacy else 0:.1f}/10; progress via protective action",
    ))
    add(FingerprintLayer(
        layer="suspense_reveal",
        features={"hook_types": dict(sorted(hook_types.items(), key=lambda kv: -kv[1])), "scene_functions": dict(sorted(scene_functions.items(), key=lambda kv: -kv[1]))},
        rules=["Reveals reinterpret an earlier scene rather than introduce new information from nowhere", "False reassurance precedes the sharpest escalation"],
        anti_patterns=["reveal explained twice", "cliffhanger resolved in the first paragraph of the next chapter"],
        examples=_refs(_obs_by_prefix(all_obs, ("scene:discovery", "scene:reversal", "reveals"))),
        confidence=conf_base * (0.9 if hook_types else 0.5), supporting_chapters=chapter_numbers, applicability=["reveal", "mystery_clue", "false_reassurance"],
        compact=f"hooks: {_top({k: v / max(1, sum(hook_types.values())) for k, v in hook_types.items()}, 3)}; reveals reinterpret earlier scenes",
    ))
    add(FingerprintLayer(
        layer="chapter_opening",
        features={"opening_types": agg.get("opening_type")},
        rules=[f"Openings: {_top(agg['opening_type'], 3)}", "Enter late: first paragraph is already inside a situation"],
        anti_patterns=["weather or waking-up openings", "recap paragraphs"] if lang != "ko" else ["weather openings"],
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["chapter start"],
        compact=f"opening {_top(agg['opening_type'], 3)}",
    ))
    add(FingerprintLayer(
        layer="chapter_ending",
        features={"ending_types": agg.get("ending_type"), "hook_types": hook_types},
        rules=[f"Endings: {_top(agg['ending_type'], 3)}", "Final paragraph is short and leaves one open question"],
        anti_patterns=["summarizing the chapter at its end", "ending on a resolved note twice in a row"],
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["chapter end"],
        compact=f"ending {_top(agg['ending_type'], 3)}; last paragraph short, one open question",
    ))
    add(FingerprintLayer(
        layer="scene_transition",
        features={"transition_marker_freq": agg.get("transition_marker_freq"), "scene_count": aggregate([{"unit_count": c} for c in scene_counts]).get("unit_count") if scene_counts else None},
        rules=[f"Transition markers open {_band(agg['transition_marker_freq'])} of paragraphs", f"≈{(sum(scene_counts) / len(scene_counts)) if scene_counts else 0:.1f} scenes per chapter", "Scene breaks are hard cuts marked by a blank line; no 'meanwhile' bridges longer than one sentence"],
        confidence=conf_base * (0.9 if scene_counts else 0.5), supporting_chapters=chapter_numbers, applicability=["transition"],
        compact=f"scenes/chapter≈{(sum(scene_counts) / len(scene_counts)) if scene_counts else 0:.1f}; transition markers {agg['transition_marker_freq'].get('median', 0):.0%}",
    ))
    role_lines = [f"{role}: distinct speech level and address forms (see character cards)" for role in sorted(set((character_roles or {}).values()))][:8]
    add(FingerprintLayer(
        layer="character_voices",
        features={"roles": sorted(set((character_roles or {}).values()))},
        rules=role_lines or ["Give each recurring role a distinct sentence length and address habit"],
        anti_patterns=["all characters sharing the narrator's diction"],
        confidence=conf_base * (0.7 if character_roles else 0.4), supporting_chapters=chapter_numbers, applicability=["dialogue"],
        compact="each role keeps a distinct speech level / address habit",
    ))
    top_techniques = [t for t, _ in sorted(techniques.items(), key=lambda kv: -kv[1])[:10]]
    add(FingerprintLayer(
        layer="beat_execution",
        features={"techniques": top_techniques, "scene_functions": scene_functions},
        rules=top_techniques or ["Each scene has a goal, an obstacle, a turn and an exit hook"],
        examples=_refs(_obs_by_prefix(all_obs, ("techniques", "scene:"))),
        confidence=conf_base * (0.9 if top_techniques else 0.5), supporting_chapters=_chapters(all_obs) or chapter_numbers, applicability=["all"],
        compact="techniques: " + "; ".join(top_techniques[:5]) if top_techniques else "goal→obstacle→turn→exit hook per scene",
    ))
    gap_dist = aggregate([{"unit_count": g} for g in reward_gaps]).get("unit_count") if reward_gaps else {}
    tension = [e.get("tension") for e in emotions if isinstance(e.get("tension"), (int, float))]
    add(FingerprintLayer(
        layer="pacing_reward",
        features={"reward_gap_chapters": gap_dist, "tension_mean": round(sum(tension) / len(tension), 2) if tension else None, "chapter_units": agg.get("unit_count")},
        rules=[f"Deliver a reader reward every {gap_dist.get('median', 1) if gap_dist else '1-2'} chapter(s)", "Alternate pressure and reward; never two setback chapters without a small competence beat"],
        anti_patterns=["three consecutive chapters with no payoff", "rewards without cost"],
        confidence=conf_base * (0.9 if reward_gaps else 0.5), supporting_chapters=[e["chapter"] for e in emotions][:40], applicability=["planning", "all"],
        compact=f"reward every ≈{gap_dist.get('median', 1) if gap_dist else 1:.0f} ch; tension≈{(sum(tension) / len(tension)) if tension else 0:.1f}/10; chapter≈{agg['unit_count'].get('median', 0):.0f}u",
    ))
    sl = agg.get("speech_levels") or {}
    add(FingerprintLayer(
        layer="korean_register",
        features={"speech_levels": sl, "honorific_density_per_k": agg.get("honorific_density"), "onomatopoeia_per_k": agg.get("onomatopoeia_density"), "short_paragraph_ratio": agg.get("short_paragraph_ratio")},
        rules=([f"Narration speech level mix: " + ", ".join(f"{k} {v.get('median', 0):.0%}" for k, v in sl.items()), f"Honorific/address markers {_band(agg['honorific_density'])} per 1000 eojeol", f"Mimetic/sound words {_band(agg['onomatopoeia_density'])} per 1000 eojeol", "Keep subject omission where the referent is clear; do not over-specify 나는/그는"] if lang == "ko" else ["Not applicable: source language is not Korean"]),
        anti_patterns=["mixing 해요체 and 하다체 within one narrator voice", "translating idioms literally"] if lang == "ko" else [],
        confidence=conf_base if lang == "ko" else 0.0, supporting_chapters=chapter_numbers if lang == "ko" else [], applicability=["ko"],
        compact=("levels " + ", ".join(f"{k}:{v.get('median', 0):.0%}" for k, v in sl.items()) + f"; honorific/k {agg['honorific_density'].get('median', 0):.1f}; mimetic/k {agg['onomatopoeia_density'].get('median', 0):.1f}") if lang == "ko" else "n/a (non-Korean source)",
    ))
    negatives = []
    for layer in layers.values():
        negatives += layer.anti_patterns
    add(FingerprintLayer(
        layer="negative_constraints",
        features={"count": len(set(negatives))},
        rules=sorted(set(negatives)),
        anti_patterns=sorted(set(negatives)),
        confidence=conf_base, supporting_chapters=chapter_numbers, applicability=["all"],
        compact="avoid: " + "; ".join(sorted(set(negatives))[:8]),
    ))
    add(FingerprintLayer(
        layer="evidence_index",
        features={"verified_observations": len(all_obs), "chapters_with_analysis": len(analyses or {}), "chapters_measured": n, "observation_ids": [o.get("observation_id") for o in all_obs][:500]},
        rules=[],
        examples=_refs(all_obs, limit=20),
        confidence=1.0 if all_obs else 0.3, supporting_chapters=_chapters(all_obs), applicability=["audit"],
        compact=f"{len(all_obs)} verified observations over {len(analyses or {})} analysed / {n} measured chapters",
    ))

    dependency_hash = stable_id(FINGERPRINT_VERSION, manuscript_id, *hashes, length=32)
    return {
        "version": FINGERPRINT_VERSION,
        "manuscript_id": manuscript_id,
        "language": lang,
        "chapters_measured": n,
        "dependency_hash": dependency_hash,
        "chapter_hashes": hashes,
        "layers": {name: layers[name].as_dict() for name in LAYERS if name in layers},
        "targets": {k: agg[k] for k in agg if isinstance(agg[k], dict) and "n" in agg[k]} | {"opening_type": agg.get("opening_type"), "ending_type": agg.get("ending_type"), "speech_levels": sl},
        "per_chapter_metrics": per_chapter,
        "stale": False,
    }


def compact_fingerprint(fp: Dict[str, Any], *, functions: Iterable[str] = (), max_chars: int = 2200) -> str:
    """Prompt-ready compact representation: always-on layers + layers applicable to the requested functions."""
    fns = set(functions)
    always = ("global_voice", "pov_focalization", "rhythm", "dialogue", "internal_monologue", "emotional_expression", "chapter_opening", "chapter_ending", "negative_constraints")
    conditional = {
        "action_scene": {"fight_choreography", "threat_escalation", "action_opening"},
        "romance_scene": {"romantic_tension", "confession", "relationship_shift"},
        "suspense_reveal": {"reveal", "mystery_clue", "false_reassurance", "chapter_cliffhanger", "power_reveal"},
        "exposition": {"exposition_delivery", "status_screen_presentation"},
        "humor": {"banter", "comedic_reversal"},
        "scene_transition": {"transition"},
        "beat_execution": set(fns),
        "pacing_reward": set(fns),
    }
    lines: List[str] = []
    layers = fp.get("layers") or {}
    if fp.get("language") == "ko" and "korean_register" in layers:
        lines.append(f"- korean_register: {layers['korean_register'].get('compact')}")
    for name in always:
        if name in layers and layers[name].get("compact"):
            lines.append(f"- {name}: {layers[name]['compact']}")
    for name, trig in conditional.items():
        if name in layers and (fns & trig) and layers[name].get("compact"):
            lines.append(f"- {name}: {layers[name]['compact']}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def validate_fingerprint(fp: Dict[str, Any]) -> List[str]:
    """Schema validation: every layer present with the required fields."""
    errors: List[str] = []
    if fp.get("version") != FINGERPRINT_VERSION:
        errors.append(f"version mismatch: {fp.get('version')}")
    layers = fp.get("layers") or {}
    for name in LAYERS:
        layer = layers.get(name)
        if not isinstance(layer, dict):
            errors.append(f"missing layer {name}")
            continue
        for key in ("features", "rules", "anti_patterns", "confidence", "supporting_chapters", "applicability", "compact"):
            if key not in layer:
                errors.append(f"{name}.{key} missing")
        conf = layer.get("confidence")
        if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
            errors.append(f"{name}.confidence out of range")
    if not fp.get("dependency_hash"):
        errors.append("dependency_hash missing")
    return errors


__all__ = ["FINGERPRINT_VERSION", "LAYERS", "FingerprintLayer", "build_fingerprint", "compact_fingerprint", "validate_fingerprint"]
