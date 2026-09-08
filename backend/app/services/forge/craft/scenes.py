"""Scene decomposition and scene-to-scene handoff.

``plan_scenes`` groups the outline's ordered beats into 2-5 scenes
deterministically (function tags + participant changes + explicit scene-break
tags). A model may replace it with a richer plan (``passes.model_scene_plan``)
but the deterministic plan is always valid and is what tests exercise.

``extract_handoff`` pulls the exact carry-forward state from drafted scene
prose so the next scene starts where the previous one physically stopped.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from app.schemas.craft import SceneHandoff, ScenePlan
from app.services.forge.textmetrics import split_paragraphs

MIN_SCENES = 2
MAX_SCENES = 5

# Beat functions that usually open a new scene.
_OPENERS = {"cold_open", "scene_change", "new_location", "time_skip", "arrival", "aftermath", "cut_to", "later", "meanwhile", "next_morning", "transition"}
# Beat functions that usually close a scene (the next beat starts a new one).
_CLOSERS = {"chapter_cliffhanger", "cliffhanger", "scene_end", "exit", "departure", "blackout", "reveal", "escalation_peak", "hook"}

_LAST_SPEAKER = re.compile(r"[\"“]([^\"”]{2,240})[\"”]\s*,?\s*(?:(?:said|asked|replied|answered|whispered|muttered|snapped|called|added)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)|([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(?:said|asked|replied|answered|whispered|muttered|snapped|called|added))?", re.S)
_POSITION_CUES = re.compile(r"\b(?:stood|sat|knelt|leaned|lay|crouched|stepped|turned|faced|held|gripped|pressed|paced|waited|stopped)\b[^.]{0,80}\.", re.I)
_TENSION_CUES = re.compile(r"[^.?!\n]*(?:\?|\bbut\b|\bunless\b|\buntil\b|\bif\b|\bbefore\b|\bwould\b|\bhad not\b|\bhadn'?t\b|\bdid not\b|\bdidn'?t\b|\bnobody\b|\bno one\b|\bnever\b|\bnot yet\b|\bstill\b)[^.?!\n]*[.?!]", re.I)


def _functions(beats: Sequence[Dict[str, Any]]) -> List[str]:
    return [str(b.get("function") or "").strip().lower() for b in beats]


def _participants(beat: Dict[str, Any]) -> set:
    names = beat.get("participants") or beat.get("present") or []
    return {str(n).strip().lower() for n in names if n}


def plan_scenes(beats: Sequence[Dict[str, Any]], *, participants: Sequence[str], pov: str, word_target: int, location: str = "", closing_hook: str = "") -> List[ScenePlan]:
    """Group beats into scenes. Never returns fewer than one scene; returns a single scene for <2 beats."""
    beats = [b for b in beats if isinstance(b, dict)]
    if not beats:
        return []
    funcs = _functions(beats)
    groups: List[List[int]] = [[1]]
    for i in range(1, len(beats)):
        idx = i + 1
        prev_f, cur_f = funcs[i - 1], funcs[i]
        breaks = cur_f in _OPENERS or prev_f in _CLOSERS
        pa, pb = _participants(beats[i - 1]), _participants(beats[i])
        if pa and pb and pa != pb:
            breaks = True
        desc = str(beats[i].get("description") or "")
        if re.match(r"^\s*(?:later|meanwhile|that (?:night|evening|morning)|the next (?:day|morning)|elsewhere|back at|hours later|by (?:dawn|dusk|nightfall))\b", desc, re.I):
            breaks = True
        if breaks and len(groups) < MAX_SCENES:
            groups.append([idx])
        else:
            groups[-1].append(idx)
    # Too few scenes for a multi-beat chapter: split the largest group(s) until MIN_SCENES.
    while len(groups) < MIN_SCENES and len(beats) >= 2:
        big = max(range(len(groups)), key=lambda g: len(groups[g]))
        if len(groups[big]) < 2:
            break
        half = len(groups[big]) // 2
        left, right = groups[big][:half], groups[big][half:]
        groups[big:big + 1] = [left, right]
    # Too many: merge the smallest adjacent pair.
    while len(groups) > MAX_SCENES:
        sizes = [len(g) + len(groups[i + 1]) for i, g in enumerate(groups[:-1])]
        j = sizes.index(min(sizes))
        groups[j:j + 2] = [groups[j] + groups[j + 1]]
    total_beats = float(len(beats))
    scenes: List[ScenePlan] = []
    for si, g in enumerate(groups, start=1):
        first, last = beats[g[0] - 1], beats[g[-1] - 1]
        present = set()
        for bi in g:
            present |= _participants(beats[bi - 1])
        present_names = [p for p in participants if p.lower() in present] if present else list(participants)
        title = str(first.get("description") or "")[:60].rstrip(" ,.;") or f"Scene {si}"
        turn = str(last.get("description") or "")[:160]
        is_last = si == len(groups)
        scenes.append(ScenePlan(
            index=si,
            title=title,
            beat_indexes=list(g),
            location=str(first.get("location") or location or ""),
            story_time=str(first.get("time") or first.get("story_time") or ""),
            present=present_names,
            dramatic_question=f"Will {pov} get through: {title}?" if not first.get("question") else str(first["question"]),
            entry_state="",
            exit_state=("the chapter hook: " + closing_hook) if (is_last and closing_hook) else "",
            turn=turn,
            interiority_focus="",
            micro_payoff="",
            word_share=round(len(g) / total_beats, 3),
        ))
    # Make word shares sum to 1.0 with the remainder on the longest scene.
    drift = round(1.0 - sum(s.word_share for s in scenes), 3)
    if scenes and abs(drift) > 0.0005:
        longest = max(scenes, key=lambda s: s.word_share)
        longest.word_share = round(min(1.0, max(0.0, longest.word_share + drift)), 3)
    return scenes


def extract_handoff(scene_prose: str) -> SceneHandoff:
    paras = split_paragraphs(scene_prose)
    ending = "\n\n".join(paras[-3:]) if paras else scene_prose
    last_speaker, open_dialogue = "", ""
    quotes = list(_LAST_SPEAKER.finditer(ending))
    if quotes:
        m = quotes[-1]
        open_dialogue = m.group(1).strip()[:200]
        last_speaker = (m.group(2) or m.group(3) or "").strip()
    positions = " ".join(x.group(0).strip() for x in _POSITION_CUES.finditer(ending))[-300:]
    tension = ""
    for m in _TENSION_CUES.finditer(ending):
        tension = m.group(0).strip()
    return SceneHandoff(ending_lines=ending[-1200:], last_speaker=last_speaker, open_dialogue=open_dialogue, physical_positions=positions, carried_tension=tension[:240])


def render_scene_brief(scene: ScenePlan, beats: Sequence[Dict[str, Any]], *, word_target: int, scene_count: int, handoff: Optional[SceneHandoff], pov: str) -> str:
    """The per-scene instruction block appended to the compiled chapter context."""
    words = max(250, int(round(word_target * (scene.word_share or (1.0 / max(scene_count, 1))))))
    lines = [f"[THIS SCENE — {scene.index} of {scene_count}: {scene.title}]", f"Write ONLY this scene (≈{words} words). Cover exactly these beats, in order, and stop when the last one lands:"]
    for bi in scene.beat_indexes:
        if 1 <= bi <= len(beats):
            b = beats[bi - 1]
            lines.append(f"  {bi}. [{b.get('function') or 'beat'}] {str(b.get('description') or '')[:400]}")
    if scene.location:
        lines.append(f"location: {scene.location}")
    if scene.story_time:
        lines.append(f"time: {scene.story_time}")
    if scene.present:
        lines.append(f"present: {', '.join(scene.present)}")
    if scene.dramatic_question:
        lines.append(f"dramatic question: {scene.dramatic_question}")
    if scene.turn:
        lines.append(f"the scene must TURN on: {scene.turn}")
    if scene.interiority_focus:
        lines.append(f"{pov}'s private thread: {scene.interiority_focus}")
    if scene.micro_payoff:
        lines.append(f"micro-payoff to land: {scene.micro_payoff}")
    if scene.exit_state:
        lines.append(f"exit state: {scene.exit_state}")
    if handoff is not None and (handoff.ending_lines or handoff.open_dialogue):
        lines += ["", "[PREVIOUS SCENE — EXACT ENDING (continue from here; do not repeat or summarize it)]", handoff.ending_lines]
        if handoff.open_dialogue:
            lines.append(f"last spoken line{f' ({handoff.last_speaker})' if handoff.last_speaker else ''}: “{handoff.open_dialogue}” — the next line answers or pointedly ignores it")
        if handoff.physical_positions:
            lines.append(f"bodies and objects as left: {handoff.physical_positions}")
        if handoff.carried_tension:
            lines.append(f"carried tension: {handoff.carried_tension}")
    elif scene.index == 1:
        lines += ["", "This is the chapter opening: begin in motion or mid-thought, never with weather or waking up unless the outline says so."]
    if scene.index < scene_count:
        lines.append("Do not resolve the chapter; end the scene on a small turn that the next scene picks up. No <chapter_summary>/<scene_handoff>/<claims> blocks for intermediate scenes.")
    else:
        lines.append("This is the final scene: land the chapter hook, then output the <chapter_summary>, <scene_handoff> and optional <claims> blocks for the WHOLE chapter.")
    return "\n".join(lines)


def stitch(scene_texts: Sequence[str]) -> str:
    """Join scene prose with the project's scene-break convention (three line breaks)."""
    cleaned = [t.strip() for t in scene_texts if t and t.strip()]
    return "\n\n\n".join(cleaned)


__all__ = ["MAX_SCENES", "MIN_SCENES", "extract_handoff", "plan_scenes", "render_scene_brief", "stitch"]
