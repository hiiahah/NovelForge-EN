"""Character agenda & subtext packets, compiled from the Bible before dialogue is written.

Everything here is deterministic: the packet is only as good as the Character
Cards, Relationship Arcs and Knowledge Facts feeding it, which is exactly the
point — Bible quality becomes prose quality. A model may refine the packet
later (``passes.refine_subtext``), but the deterministic version is always
available and is what tests exercise.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.schemas.craft import CharacterAgenda, ProtagonistVoice, ScenePlan, SubtextPacket


def _s(v: Any, limit: int = 200) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = "; ".join(str(x) for x in v if x)
    text = str(v).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first(*vals: Any) -> str:
    for v in vals:
        s = _s(v)
        if s:
            return s
    return ""


def _tactic_for(dd: Dict[str, Any], voice: Dict[str, Any], role: str) -> str:
    coping = _s(dd.get("coping_mechanism"))
    deception = _s(voice.get("deception_style"))
    anger = _s(voice.get("anger_style"))
    if deception and role.lower() in ("antagonist", "rival", "villain"):
        return f"deflects and misdirects ({deception})"
    if coping:
        return coping
    if anger:
        return f"pressure; when crossed: {anger}"
    return "keeps the real request under a smaller one"


def _relationship_line(rel: Dict[str, Any]) -> str:
    if not rel:
        return ""
    parts = []
    if rel.get("private_relationship"):
        parts.append(_s(rel["private_relationship"], 100))
    nums = []
    for k in ("trust", "affection", "fear", "resentment", "dependency"):
        if rel.get(k) is not None:
            nums.append(f"{k} {rel[k]}")
    if nums:
        parts.append(", ".join(nums))
    if rel.get("unresolved_tension"):
        parts.append(f"tension: {_s(rel['unresolved_tension'], 100)}")
    return "; ".join(parts)


def build_agenda(card: Dict[str, Any], *, pov_name: str, relationship: Optional[Dict[str, Any]] = None, scene: Optional[ScenePlan] = None, knowledge_gap: Sequence[str] = ()) -> CharacterAgenda:
    """Compile one non-POV participant's agenda for a scene from their Character Card."""
    name = str(card.get("name") or "")
    dd = card.get("dramatic_design") or {}
    voice = card.get("voice") or {}
    rules = card.get("consistency_rules") or {}
    role = str(card.get("role_type") or "")
    want = _first(dd.get("external_goal"), card.get("core_drive"))
    if scene and scene.dramatic_question:
        wants = f"{want} — specifically, in this scene: whatever moves '{_s(scene.dramatic_question, 90)}' their way"
    else:
        wants = want
    secrets = dd.get("secrets") or []
    suppressing = _first(secrets[0] if secrets else "", dd.get("secret_desire"), dd.get("greatest_fear"))
    if knowledge_gap:
        suppressing = (suppressing + "; " if suppressing else "") + f"knows and is not saying: {_s(list(knowledge_gap)[:2], 160)}"
    leverage = _first(dd.get("agency_source"), (card.get("competence") or {}).get("social_power"), dd.get("public_image"))
    fear = _first(dd.get("greatest_fear"), dd.get("breaking_point"))
    address = ""
    for form in voice.get("forms_of_address") or []:
        f = str(form)
        if pov_name.split()[0].lower() in f.lower() or "protagonist" in f.lower():
            address = f
            break
    if not address and voice.get("forms_of_address"):
        address = _s(voice["forms_of_address"][0], 80)
    speech_bits = [b for b in (_s(voice.get("sentence_tendency"), 60), _s(voice.get("formality"), 60), _s(voice.get("humor_style"), 60), ("tells: " + _s(voice.get("verbal_tells"), 100)) if voice.get("verbal_tells") else "") if b]
    return CharacterAgenda(
        name=name,
        wants_from_pov=_s(wants, 220),
        suppressing=_s(suppressing, 220),
        leverage=_s(leverage, 140),
        fear_in_scene=_s(fear, 140),
        tactic=_s(_tactic_for(dd, voice, role), 140),
        tell_when_lying=_s(voice.get("deception_style"), 120),
        speech="; ".join(speech_bits)[:260],
        address_pov_as=address,
        never_says=[str(x) for x in (voice.get("forbidden_speech") or [])][:6] + [str(x) for x in (rules.get("voice_restrictions") or [])][:3],
        relationship_now=_relationship_line(relationship or {}),
    )


def pov_private_agenda(pov_card: Dict[str, Any], scene: Optional[ScenePlan]) -> str:
    dd = pov_card.get("dramatic_design") or {}
    want = _first(dd.get("external_goal"), pov_card.get("core_drive"))
    need = _s(dd.get("internal_need"))
    parts = []
    if scene and scene.interiority_focus:
        parts.append(_s(scene.interiority_focus, 160))
    if want:
        parts.append(f"wants: {want}")
    if need:
        parts.append(f"actually needs (won't admit): {need}")
    if dd.get("false_belief"):
        parts.append(f"operating belief: {_s(dd['false_belief'], 120)}")
    return "; ".join(parts)[:420]


def pov_misread(pov_card: Dict[str, Any], others: Sequence[Dict[str, Any]]) -> str:
    """One thing the POV is likely to get wrong about another character — fuel for dramatic irony."""
    blind = (pov_card.get("competence") or {}).get("blind_spots") or []
    for other in others:
        dd = other.get("dramatic_design") or {}
        public, private = _s(dd.get("public_image"), 90), _s(dd.get("self_image"), 90)
        if public and private and public.lower() != private.lower():
            return f"reads {other.get('name')} as '{public}' while they privately are '{private}'"
        if dd.get("secret_desire"):
            return f"does not suspect {other.get('name')}'s private want: {_s(dd['secret_desire'], 100)}"
    if blind:
        return f"blind spot in play: {_s(blind[0], 120)}"
    return ""


def build_packet(
    scene: ScenePlan,
    *,
    pov_name: str,
    cards_by_name: Dict[str, Dict[str, Any]],
    relationships: Dict[str, Dict[str, Any]],
    knowledge_gaps: Optional[Dict[str, List[str]]] = None,
) -> SubtextPacket:
    """Compile the packet for one scene. ``relationships`` is keyed by the lower-cased other character's name."""
    pov_card = cards_by_name.get(pov_name.lower()) or {"name": pov_name}
    present = [p for p in (scene.present or []) if p and p.lower() != pov_name.lower()]
    agendas: List[CharacterAgenda] = []
    others: List[Dict[str, Any]] = []
    for name in present:
        card = cards_by_name.get(name.lower())
        if not card:
            agendas.append(CharacterAgenda(name=name, wants_from_pov="(no Character Card: keep this character functional, give them one concrete want and no backstory)"))
            continue
        others.append(card)
        agendas.append(build_agenda(card, pov_name=pov_name, relationship=relationships.get(name.lower()), scene=scene, knowledge_gap=(knowledge_gaps or {}).get(name.lower(), [])))
    return SubtextPacket(scene_index=scene.index, pov_private_agenda=pov_private_agenda(pov_card, scene), pov_reads_wrong=pov_misread(pov_card, others), agendas=agendas)


def voice_from_card(card: Dict[str, Any]) -> ProtagonistVoice:
    """Read the stored Protagonist Voice, or derive a serviceable one from the Bible 2.0 groups."""
    raw = card.get("protagonist_voice")
    if isinstance(raw, dict) and any(raw.values()):
        try:
            return ProtagonistVoice.model_validate(raw)
        except Exception:
            pass
    dd = card.get("dramatic_design") or {}
    voice = card.get("voice") or {}
    comp = card.get("competence") or {}
    personality = _s(card.get("personality"), 80)
    archetype = personality or "observant pragmatist"
    return ProtagonistVoice(
        archetype=archetype,
        inner_register=_first(voice.get("sentence_tendency"), "dry, clipped, notices the practical detail before the emotional one"),
        notices_first=[str(x) for x in (comp.get("knowledge") or [])][:3] or ["exits", "who is lying", "what things cost"],
        private_humor=_first(voice.get("humor_style"), "deadpan; finds other people's certainty funny"),
        self_deception=_first(dd.get("false_belief"), dd.get("self_image")),
        calculation_style="weighs odds and costs; lists options; assumes the worst case first",
        composure_mask=_first(dd.get("public_image"), "outwardly composed"),
        signature_moves=[str(x) for x in (voice.get("rhetorical_habits") or [])][:3],
        forbidden_interior=[str(x) for x in (voice.get("forbidden_speech") or [])][:4] + ["earnest self-pity", "therapy vocabulary", "explaining the theme"],
    )


def render_voice(voice: ProtagonistVoice, pov_name: str) -> str:
    lines = [f"{pov_name} narrates from the inside. Archetype: {voice.archetype}."]
    if voice.inner_register:
        lines.append(f"- inner register (private narration): {voice.inner_register}")
    if voice.composure_mask:
        lines.append(f"- outer manner (what others see): {voice.composure_mask} — keep the gap between the two visible on every page")
    if voice.notices_first:
        lines.append(f"- notices first: {', '.join(voice.notices_first)}")
    if voice.calculation_style:
        lines.append(f"- thinks on the page by: {voice.calculation_style}")
    if voice.private_humor:
        lines.append(f"- private humor: {voice.private_humor}")
    if voice.self_deception:
        lines.append(f"- tells themself: {voice.self_deception} (the reader should see through it; the narrator should not)")
    if voice.signature_moves:
        lines.append(f"- signature interior moves: {'; '.join(voice.signature_moves)}")
    if voice.forbidden_interior:
        lines.append(f"- never in the interior voice: {'; '.join(voice.forbidden_interior)}")
    return "\n".join(lines)


def render_packet(packet: SubtextPacket, pov_name: str) -> str:
    lines: List[str] = []
    if packet.pov_private_agenda:
        lines.append(f"{pov_name} (POV) privately: {packet.pov_private_agenda}")
    if packet.pov_reads_wrong:
        lines.append(f"{pov_name} misreads: {packet.pov_reads_wrong}")
    for a in packet.agendas:
        bits = [f"{a.name}:"]
        if a.wants_from_pov:
            bits.append(f"wants from {pov_name}: {a.wants_from_pov}")
        if a.suppressing:
            bits.append(f"suppressing: {a.suppressing}")
        if a.tactic:
            bits.append(f"tactic: {a.tactic}")
        if a.leverage:
            bits.append(f"leverage: {a.leverage}")
        if a.fear_in_scene:
            bits.append(f"afraid of: {a.fear_in_scene}")
        if a.relationship_now:
            bits.append(f"relationship now: {a.relationship_now}")
        if a.speech:
            bits.append(f"speech: {a.speech}")
        if a.address_pov_as:
            bits.append(f"addresses {pov_name} as: {a.address_pov_as}")
        if a.tell_when_lying:
            bits.append(f"tell when lying: {a.tell_when_lying}")
        if a.never_says:
            bits.append(f"never says: {'; '.join(a.never_says)}")
        lines.append("\n  ".join(bits))
    lines.append("Rule: nobody states their want directly in the first exchange. Every line is a move toward the want or a defence of the secret. Let the POV read (and sometimes misread) the moves.")
    return "\n".join(lines)


__all__ = ["build_agenda", "build_packet", "pov_misread", "pov_private_agenda", "render_packet", "render_voice", "voice_from_card"]
