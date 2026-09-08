"""Craft passes: scene-by-scene drafting, adversarial critic, line polish, hook sharpening.

All passes speak to the model through the Forge ``Drafter`` protocol
(``role``, ``system_prompt``, ``user_prompt``, ``context``) so the same fake
drafters that test the pipeline test the craft layer. New roles:

- ``scene_planner``  — structured scene decomposition (JSON in text; parsed leniently)
- ``drafting``       — one scene at a time (same role as single-shot so existing role routing works)
- ``critic``         — adversarial webnovel editor; returns JSON CriticReport
- ``polish``         — surgical line edit against cited findings
- ``hook``           — rewrite of the final paragraphs into a real serialized hook

Every pass is optional and individually gated by ``CraftOptions``; every pass
degrades to a deterministic fallback when the model output is unusable, and the
chapter is never left in a worse state than the pass received it in
(the deterministic critic must not regress or the pass's output is discarded).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Sequence

from app.schemas.craft import CraftPassRecord, CraftReport, CriticReport, HookAnalysis, SceneDecomposition, ScenePlan, SubtextPacket
from app.services.forge import claims as claims_mod
from app.services.forge.craft import critic as critic_mod
from app.services.forge.craft import hooks as hooks_mod
from app.services.forge.craft import scenes as scenes_mod
from app.services.forge.craft import subtext as subtext_mod
from app.services.forge.craft.prompts import (
    CRITIC_SYSTEM_PROMPT,
    HOOK_SYSTEM_PROMPT,
    POLISH_SYSTEM_PROMPT,
    SCENE_DRAFT_DIRECTIVES,
    SCENE_PLAN_SYSTEM_PROMPT,
    build_critic_prompt,
    build_hook_prompt,
    build_polish_prompt,
    build_scene_plan_prompt,
)
from app.services.forge.textmetrics import count_units, detect_language

CRAFT_VERSION = "craft-1"


class Drafter(Protocol):
    async def __call__(self, *, role: str, system_prompt: str, user_prompt: str, context: Any) -> str: ...


@dataclass
class CraftOptions:
    """What the craft layer may do. Defaults are conservative so the legacy single-shot pipeline is unchanged."""

    scene_by_scene: bool = False
    model_scene_plan: bool = False
    subtext_packets: bool = True
    critic: bool = False
    model_critic: bool = False
    polish: bool = False
    max_polish_passes: int = 2
    accept_score: float = critic_mod.ACCEPT_THRESHOLD
    hook_sharpen: bool = False
    min_hook_strength: int = 5
    protagonist_voice: bool = True

    @classmethod
    def off(cls) -> "CraftOptions":
        return cls(subtext_packets=False, protagonist_voice=False)

    @classmethod
    def full(cls) -> "CraftOptions":
        return cls(scene_by_scene=True, model_scene_plan=True, subtext_packets=True, critic=True, model_critic=True, polish=True, max_polish_passes=3, hook_sharpen=True, protagonist_voice=True)

    @classmethod
    def preset(cls, name: str) -> "CraftOptions":
        name = (name or "").lower()
        if name in ("full", "elite", "quality"):
            return cls.full()
        if name in ("balanced",):
            return cls(scene_by_scene=True, model_scene_plan=False, subtext_packets=True, critic=True, model_critic=True, polish=True, max_polish_passes=2, hook_sharpen=True)
        if name in ("economy", "single", "single_shot"):
            return cls(scene_by_scene=False, critic=True, model_critic=False, polish=False, hook_sharpen=False)
        if name in ("off", "none", "legacy"):
            return cls.off()
        return cls()

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class CraftInputs:
    """Everything the passes need from the compiled context and the Bible, pre-resolved by the pipeline."""

    pov: str
    participants: List[str]
    beats: List[Dict[str, Any]]
    word_target: int
    closing_hook: str = ""
    location: str = ""
    cards_by_name: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    relationships: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    knowledge_gaps: Dict[str, List[str]] = field(default_factory=dict)
    language: Optional[str] = None


@dataclass
class CraftOutcome:
    prose: str
    report: CraftReport
    model_calls: int = 0


# ----------------------------------------------------------------- utilities
_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _lenient_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    m = _JSON_BLOCK.search(text)
    if not m:
        return None
    raw = m.group(0)
    for candidate in (raw, raw.replace("```json", "").replace("```", "")):
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else None
        except Exception:
            continue
    return None


def _record(report: CraftReport, name: str, *, calls: int = 0, changed: bool = False, note: str = "") -> None:
    report.passes.append(CraftPassRecord(name=name, model_calls=calls, changed=changed, note=note))
    report.model_calls += calls


def voice_section_text(inputs: CraftInputs) -> str:
    card = inputs.cards_by_name.get(inputs.pov.lower()) or {"name": inputs.pov}
    return subtext_mod.render_voice(subtext_mod.voice_from_card(card), inputs.pov)


def build_packets(scenes: Sequence[ScenePlan], inputs: CraftInputs) -> List[SubtextPacket]:
    return [subtext_mod.build_packet(s, pov_name=inputs.pov, cards_by_name=inputs.cards_by_name, relationships=inputs.relationships, knowledge_gaps=inputs.knowledge_gaps) for s in scenes]


# --------------------------------------------------------------- scene plan
async def plan(drafter: Optional[Drafter], context: Any, inputs: CraftInputs, opts: CraftOptions, report: CraftReport) -> List[ScenePlan]:
    scenes = scenes_mod.plan_scenes(inputs.beats, participants=inputs.participants, pov=inputs.pov, word_target=inputs.word_target, location=inputs.location, closing_hook=inputs.closing_hook)
    if not scenes:
        return []
    if not (opts.model_scene_plan and drafter is not None) or len(inputs.beats) < 2:
        _record(report, "scene_plan", note=f"deterministic: {len(scenes)} scene(s)")
        return scenes
    raw = await drafter(role="scene_planner", system_prompt=SCENE_PLAN_SYSTEM_PROMPT, user_prompt=build_scene_plan_prompt(context, inputs.beats, scenes, inputs.pov, inputs.participants, inputs.word_target, inputs.closing_hook), context=context)
    data = _lenient_json(raw)
    try:
        decomposition = SceneDecomposition.model_validate(data or {})
    except Exception:
        decomposition = SceneDecomposition()
    model_scenes = [s for s in decomposition.scenes if s.beat_indexes]
    covered = sorted({b for s in model_scenes for b in s.beat_indexes})
    valid = bool(model_scenes) and scenes_mod.MIN_SCENES <= len(model_scenes) <= scenes_mod.MAX_SCENES and covered == list(range(1, len(inputs.beats) + 1))
    if not valid:
        _record(report, "scene_plan", calls=1, note="model plan rejected (coverage/shape); deterministic plan kept")
        return scenes
    allowed = {p.lower() for p in inputs.participants}
    for i, s in enumerate(model_scenes, start=1):
        s.index = i
        s.present = [p for p in s.present if p.lower() in allowed] or [p for p in inputs.participants]
    total = sum(s.word_share for s in model_scenes) or 0.0
    if total <= 0.0:
        for s in model_scenes:
            s.word_share = round(len(s.beat_indexes) / float(len(inputs.beats)), 3)
    else:
        for s in model_scenes:
            s.word_share = round(s.word_share / total, 3)
    _record(report, "scene_plan", calls=1, changed=True, note=f"model plan: {len(model_scenes)} scene(s)")
    return model_scenes


# ---------------------------------------------------------------- drafting
async def draft_scene_by_scene(drafter: Drafter, context: Any, base_system_prompt: str, base_user_prompt: str, scenes: Sequence[ScenePlan], packets: Sequence[SubtextPacket], inputs: CraftInputs, report: CraftReport) -> str:
    texts: List[str] = []
    handoff = None
    voice_text = voice_section_text(inputs)
    for scene, packet in zip(scenes, packets):
        brief = scenes_mod.render_scene_brief(scene, inputs.beats, word_target=inputs.word_target, scene_count=len(scenes), handoff=handoff, pov=inputs.pov)
        parts = [base_user_prompt, "", f"[PROTAGONIST VOICE — {inputs.pov}]", voice_text, "", f"[SUBTEXT PACKET — scene {scene.index}]", subtext_mod.render_packet(packet, inputs.pov), "", SCENE_DRAFT_DIRECTIVES, "", brief]
        raw = await drafter(role="drafting", system_prompt=base_system_prompt, user_prompt="\n".join(parts), context=context)
        if scene.index < len(scenes):
            prose_only, _ = claims_mod.split_prose_and_claims(raw)  # intermediate scenes must not carry chapter blocks
            texts.append(prose_only)
            handoff = scenes_mod.extract_handoff(prose_only)
        else:
            texts.append(raw)
    _record(report, "draft_scenes", calls=len(scenes), changed=True, note=f"{len(scenes)} scene(s) drafted and stitched")
    return scenes_mod.stitch(texts)


# ------------------------------------------------------------------- critic
async def run_critic(drafter: Optional[Drafter], context: Any, prose: str, inputs: CraftInputs, opts: CraftOptions, report: CraftReport, *, label: str) -> CriticReport:
    hook = hooks_mod.analyze_hook(prose, closing_hook_plan=inputs.closing_hook, language=inputs.language)
    det = critic_mod.deterministic_critic(prose, hook=hook, language=inputs.language, pov_name=inputs.pov, word_target=inputs.word_target)
    if label == "before":
        report.hook_before = hook
    else:
        report.hook_after = hook
    if not (opts.model_critic and drafter is not None):
        _record(report, f"critic_{label}", note=f"deterministic {det.overall}/10 · {det.verdict}")
        return det
    raw = await drafter(role="critic", system_prompt=CRITIC_SYSTEM_PROMPT, user_prompt=build_critic_prompt(context, prose, inputs.pov, det), context=context)
    data = _lenient_json(raw)
    model_report: Optional[CriticReport] = None
    if data:
        try:
            model_report = CriticReport.model_validate({**data, "source": "model"})
        except Exception:
            model_report = None
    merged = critic_mod.merge_reports(det, model_report)
    _record(report, f"critic_{label}", calls=1, note=f"{'merged' if model_report else 'model unusable; deterministic'} {merged.overall}/10 · {merged.verdict}")
    return merged


# ------------------------------------------------------------------- polish
def _prose_and_blocks(raw: str) -> Dict[str, str]:
    """Split visible prose from trailing metadata blocks so polish/hook passes never touch the blocks."""
    prose, _ = claims_mod.split_prose_and_claims(raw)
    blocks = raw[len(prose):] if raw.startswith(prose) else ""
    if not blocks:
        # Blocks may have been interleaved; rebuild from the regexes.
        tail_parts = []
        for rx in (claims_mod.CHAPTER_SUMMARY_RX, claims_mod.SCENE_HANDOFF_RX, claims_mod.CLAIMS_BLOCK_RX):
            m = rx.search(raw)
            if m:
                tail_parts.append(m.group(0))
        blocks = "\n".join(tail_parts)
    return {"prose": prose, "blocks": blocks.strip()}


def _rejoin(prose: str, blocks: str) -> str:
    return prose.rstrip() + ("\n" + blocks if blocks else "")


async def polish(drafter: Drafter, context: Any, raw: str, critic: CriticReport, inputs: CraftInputs, opts: CraftOptions, report: CraftReport) -> Dict[str, Any]:
    parts = _prose_and_blocks(raw)
    before = parts["prose"]
    findings = [f for f in critic.findings if f.severity in ("critical", "high", "medium")][:30]
    if not findings:
        return {"raw": raw, "changed": False, "critic": critic}
    out = await drafter(role="polish", system_prompt=POLISH_SYSTEM_PROMPT, user_prompt=build_polish_prompt(context, before, findings, critic.strongest_moment, inputs.pov, voice_section_text(inputs)), context=context)
    candidate, _ = claims_mod.split_prose_and_claims(out)
    candidate = candidate.strip()
    lang = inputs.language or detect_language(before)
    # Guard rails: comparable length (findings may license cutting, but never more than ~a third) and no deterministic regression.
    cited = sum(len(f.quote) for f in findings if f.quote)
    floor = max(0.6, 0.85 - cited / float(max(len(before), 1)))
    if not candidate or count_units(candidate, lang) < count_units(before, lang) * floor:
        _record(report, "polish", calls=1, note=f"rejected: output too short (floor {floor:.2f})")
        return {"raw": raw, "changed": False, "critic": critic}
    hook = hooks_mod.analyze_hook(candidate, closing_hook_plan=inputs.closing_hook, language=lang)
    after = critic_mod.deterministic_critic(candidate, hook=hook, language=lang, pov_name=inputs.pov, word_target=inputs.word_target)
    det_before = critic_mod.deterministic_critic(before, hook=hooks_mod.analyze_hook(before, closing_hook_plan=inputs.closing_hook, language=lang), language=lang, pov_name=inputs.pov, word_target=inputs.word_target)
    if after.overall + 0.05 < det_before.overall:
        _record(report, "polish", calls=1, note=f"rejected: deterministic score fell {det_before.overall} -> {after.overall}")
        return {"raw": raw, "changed": False, "critic": critic}
    _record(report, "polish", calls=1, changed=True, note=f"tics {det_before.tic_count} -> {after.tic_count}; score {det_before.overall} -> {after.overall}")
    return {"raw": _rejoin(candidate, parts["blocks"]), "changed": True, "critic": after}


# --------------------------------------------------------------------- hook
async def sharpen_hook(drafter: Drafter, context: Any, raw: str, hook: HookAnalysis, inputs: CraftInputs, report: CraftReport) -> Dict[str, Any]:
    parts = _prose_and_blocks(raw)
    split = hooks_mod.split_for_hook_rewrite(parts["prose"], paragraphs=2)
    if not split["keep"]:
        _record(report, "hook", note="skipped: chapter too short to split")
        return {"raw": raw, "changed": False, "hook": hook}
    out = await drafter(role="hook", system_prompt=HOOK_SYSTEM_PROMPT, user_prompt=build_hook_prompt(context, split["keep"][-2500:], split["tail"], hook, inputs.pov, inputs.closing_hook), context=context)
    new_tail, _ = claims_mod.split_prose_and_claims(out)
    new_tail = new_tail.strip()
    lang = inputs.language or detect_language(parts["prose"])
    if not new_tail or count_units(new_tail, lang) > max(400, count_units(split["tail"], lang) * 2.5):
        _record(report, "hook", calls=1, note="rejected: tail empty or bloated")
        return {"raw": raw, "changed": False, "hook": hook}
    candidate = split["keep"].rstrip() + "\n\n" + new_tail
    after = hooks_mod.analyze_hook(candidate, closing_hook_plan=inputs.closing_hook, language=lang)
    if after.strength <= hook.strength:
        _record(report, "hook", calls=1, note=f"rejected: strength {hook.strength} -> {after.strength}")
        return {"raw": raw, "changed": False, "hook": hook}
    _record(report, "hook", calls=1, changed=True, note=f"{hook.ending_class}/{hook.hook_type} {hook.strength} -> {after.ending_class}/{after.hook_type} {after.strength}")
    return {"raw": _rejoin(candidate, parts["blocks"]), "changed": True, "hook": after}


# ------------------------------------------------------------- orchestrator
async def craft_chapter(
    drafter: Drafter,
    context: Any,
    *,
    base_system_prompt: str,
    base_user_prompt: str,
    inputs: CraftInputs,
    opts: CraftOptions,
    single_shot: Callable[[], Awaitable[str]],
) -> CraftOutcome:
    """Draft (single-shot or scene-by-scene), then critic -> polish -> hook. Returns the raw model text (prose + blocks)."""
    report = CraftReport(version=CRAFT_VERSION)
    calls_before = 0
    scenes: List[ScenePlan] = []
    packets: List[SubtextPacket] = []
    if opts.scene_by_scene and len(inputs.beats) >= 2:
        scenes = await plan(drafter, context, inputs, opts, report)
        if scenes:
            packets = build_packets(scenes, inputs) if opts.subtext_packets else [SubtextPacket(scene_index=s.index) for s in scenes]
            report.mode = "scene_by_scene"
            report.scenes = scenes
            report.subtext = packets
            raw = await draft_scene_by_scene(drafter, context, base_system_prompt, base_user_prompt, scenes, packets, inputs, report)
        else:
            raw = await single_shot()
            report.model_calls += 1
            _record(report, "draft_single_shot", calls=0, changed=True, note="no scenes planned")
    else:
        raw = await single_shot()
        report.model_calls += 1
        _record(report, "draft_single_shot", calls=0, changed=True)
    calls_before = report.model_calls

    if opts.critic:
        critic = await run_critic(drafter, context, _prose_and_blocks(raw)["prose"], inputs, opts, report, label="before")
        report.critic_before = critic
        passes = 0
        while opts.polish and passes < opts.max_polish_passes and critic_mod.needs_polish(critic, threshold=opts.accept_score):
            passes += 1
            res = await polish(drafter, context, raw, critic, inputs, opts, report)
            if not res["changed"]:
                break
            raw = res["raw"]
            critic = res["critic"]
        if opts.hook_sharpen:
            hook = hooks_mod.analyze_hook(_prose_and_blocks(raw)["prose"], closing_hook_plan=inputs.closing_hook, language=inputs.language)
            if hook.is_soft or hook.strength < opts.min_hook_strength:
                res = await sharpen_hook(drafter, context, raw, hook, inputs, report)
                raw = res["raw"]
        if report.model_calls > calls_before or opts.hook_sharpen:
            report.critic_after = await run_critic(drafter, context, _prose_and_blocks(raw)["prose"], inputs, CraftOptions(**{**opts.as_dict(), "model_critic": False}), report, label="after")
        else:
            report.critic_after = critic
            report.hook_after = report.hook_before
        final = report.critic_after
        report.accepted = final.verdict != "rewrite"
        if not report.accepted:
            report.reasons.append(f"critic verdict 'rewrite' ({final.overall}/10)")
    return CraftOutcome(prose=raw, report=report, model_calls=report.model_calls)


__all__ = ["CRAFT_VERSION", "CraftInputs", "CraftOptions", "CraftOutcome", "build_packets", "craft_chapter", "plan", "polish", "run_critic", "sharpen_hook", "voice_section_text"]
