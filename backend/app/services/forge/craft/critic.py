"""Webnovel critic: deterministic grader plus merge with an optional model verdict.

The deterministic grader always runs. It measures what a regex can honestly
measure — tic density, interiority share, dialogue share, sensory anchoring,
paragraph shape, hook and payoff presence — and turns those into 1-10 scores
with cited findings. A model critic (see ``passes.model_critic``) can then add
judgement calls (stiff dialogue, cardboard characters, unearned transitions)
and the two reports are merged: the lower score per dimension wins, findings
are concatenated and de-duplicated by quote.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from app.schemas.craft import CriticFinding, CriticReport, HookAnalysis
from app.services.forge.craft import tics as tics_mod
from app.services.forge.textmetrics import count_units, detect_language, split_paragraphs, split_sentences

ACCEPT_THRESHOLD = 8.5
REWRITE_THRESHOLD = 5.5

_DIALOGUE_LINE = re.compile(r"[\"“„「『][^\"”」』]{2,}[\"”」』]")
_INTERIOR_EN = re.compile(r"\b(?:I|he|she|they)\s+(?:thought|wondered|figured|reckoned|counted|calculated|decided|noted|guessed|suspected|knew|remembered|weighed|considered|told (?:myself|himself|herself|themselves))\b|\b(?:probably|presumably|odds were|chances were|if (?:I|he|she|they) (?:was|were) (?:honest|right|wrong)|which (?:was|is) to say|in other words|so:|so\.|fine\.|right\.|well\.|no\.)\b|\([^)]{3,80}\)", re.I)
_SENSORY_EN = re.compile(r"\b(?:smell(?:ed|s)?|stink|reek|scent|taste[ds]?|salt|smoke|iron|damp|cold|warm|heat|wet|dry|rough|slick|grit|creak(?:ed)?|hum(?:med)?|clatter|clink|ring(?:ing)?|buzz|thud|hiss|rattle|glare|flicker|shadow|lamplight|dawn|dusk|rain|wind|sweat|ache|sting|throb|numb|itch|weight|pressure)\b", re.I)
_SPEECH_CADENCE = re.compile(r"[\"“][^\"”]{140,}[\"”]")  # very long single speech = lecture

FAMILY_DIMENSION = {"lecture": "authenticity", "portent": "authenticity", "contrast_scaffold": "authenticity", "register": "voice", "melodrama": "authenticity", "stacking": "sensory", "dialogue": "dialogue"}


def _clamp(v: float) -> int:
    return int(max(1, min(10, round(v))))


def _paragraph_shape(paras: Sequence[str], language: Optional[str]) -> Dict[str, float]:
    if not paras:
        return {"avg_units": 0.0, "wall_share": 0.0, "count": 0}
    lengths = [count_units(p, language) for p in paras]
    walls = sum(1 for n in lengths if n > 120)
    return {"avg_units": sum(lengths) / float(len(lengths)), "wall_share": walls / float(len(paras)), "count": len(paras)}


def deterministic_critic(prose: str, *, hook: Optional[HookAnalysis] = None, language: Optional[str] = None, pov_name: str = "", word_target: Optional[int] = None) -> CriticReport:
    lang = language or detect_language(prose)
    words = count_units(prose, lang)
    paras = split_paragraphs(prose)
    sentences = split_sentences(prose, lang)
    findings: List[CriticFinding] = []

    # 1. AI tics -> authenticity/voice/dialogue/sensory
    hits = tics_mod.find_tics(prose)
    summary = tics_mod.tic_summary(hits)
    density = tics_mod.tic_density(hits, words)
    ai_tics_score = tics_mod.tics_score(density, int(summary["high"]))
    for h in hits[:24]:
        findings.append(CriticFinding(dimension=FAMILY_DIMENSION.get(h.family, "authenticity"), severity=h.severity, quote=h.quote[:120], problem=h.message, fix=h.hint))

    # 2. Interiority share: sentences that carry the narrator's private processing.
    interior = sum(1 for s in sentences if _INTERIOR_EN.search(s))
    interior_share = interior / float(len(sentences) or 1)
    interiority_score = _clamp(3 + interior_share * 28)  # ~25% interior sentences -> 10
    if interior_share < 0.08:
        findings.append(CriticFinding(dimension="interiority", severity="high", quote="", problem=f"Only {interior} of {len(sentences)} sentences carry the protagonist's private reasoning; the chapter reports events instead of living inside {pov_name or 'the POV'}", fix="Before each plot action add 2-4 sentences of the POV's calculation, noticing or private commentary in their own idiom"))
    elif interior_share < 0.14:
        findings.append(CriticFinding(dimension="interiority", severity="medium", quote="", problem="Interior monologue is thin; readers of serialized fiction stay for the narrator's head", fix="Add a running private reaction to each other character's lines"))

    # 3. Dialogue share and shape
    dialogue_paras = sum(1 for p in paras if _DIALOGUE_LINE.search(p))
    dialogue_share = dialogue_paras / float(len(paras) or 1)
    long_speeches = len(_SPEECH_CADENCE.findall(prose))
    dialogue_score = 8.0
    if dialogue_share < 0.15:
        dialogue_score -= 2.5
        findings.append(CriticFinding(dimension="dialogue", severity="medium", quote="", problem=f"Dialogue appears in only {round(dialogue_share * 100)}% of paragraphs", fix="Turn at least one summarized exchange into live back-and-forth with subtext"))
    elif dialogue_share > 0.7:
        dialogue_score -= 1.5
        findings.append(CriticFinding(dimension="dialogue", severity="low", quote="", problem="Dialogue dominates; the reader loses the POV's read of the room", fix="Interleave interior reaction and physical business between lines"))
    if long_speeches:
        dialogue_score -= min(3.0, long_speeches * 1.0)
        m = _SPEECH_CADENCE.search(prose)
        findings.append(CriticFinding(dimension="dialogue", severity="medium", quote=(m.group(0)[:100] + "…") if m else "", problem=f"{long_speeches} speech(es) run over ~140 characters unbroken (lecture cadence)", fix="Break long speeches with interruption, gesture, or the POV's silent reaction"))
    dialogue_score -= 0.5 * summary["by_family"].get("dialogue", 0)

    # 4. Sensory anchoring (density-based; damped below ~400 words where one paragraph swings the ratio)
    sensory = len(_SENSORY_EN.findall(prose))
    sensory_per_k = sensory * 1000.0 / float(words or 1)
    damp = min(1.0, words / 400.0)
    sensory_score = _clamp(2 + sensory_per_k * 0.6 * damp + (1 - damp) * 5)  # ~13 per 1000 words -> 10 at full length
    if sensory_per_k < 5 and words >= 400:
        findings.append(CriticFinding(dimension="sensory", severity="medium", quote="", problem="Scenes float: few concrete sensory anchors per 1000 words", fix="Ground each scene entry with one smell/temperature/sound and one object the POV handles"))

    # 5. Pacing: paragraph walls and stalled shape
    shape = _paragraph_shape(paras, lang)
    pacing_score = 8.0
    if shape["wall_share"] > 0.25:
        pacing_score -= 2.5
        findings.append(CriticFinding(dimension="pacing", severity="medium", quote="", problem=f"{round(shape['wall_share'] * 100)}% of paragraphs are walls (>120 units)", fix="Split into 1-3 sentence paragraphs around beats and lines of dialogue"))
    if shape["avg_units"] > 70:
        pacing_score -= 1.0
    if word_target and words < word_target * 0.7:
        pacing_score -= 1.5
        findings.append(CriticFinding(dimension="pacing", severity="medium", quote="", problem=f"Chapter is {words} units against a target of {word_target}: beats are being rushed", fix="Give each beat breathing room: sensory entry, exchange, interior reaction, turn"))

    # 6. Hook and payoff (from hooks.analyze_hook)
    hook_score = 6
    payoff_score = 6
    if hook is not None:
        hook_score = _clamp(hook.strength)
        if hook.is_soft:
            findings.append(CriticFinding(dimension="hook", severity="high", quote=hook.tail_excerpt[-140:], problem=f"Ending is a soft {hook.ending_class.replace('_', ' ')}; nothing pulls the reader into the next chapter", fix=f"Rewrite the last two paragraphs as a {hook.suggested_hook.replace('_', ' ')} hook"))
        payoff_score = 9 if len(hook.micro_payoffs) >= 2 else (7 if hook.micro_payoffs else 3)
        if hook.payoff_missing:
            findings.append(CriticFinding(dimension="payoff", severity="high", quote="", problem="No micro-payoff detected: no deduction, tactical win, verbal victory, comic beat or status gain", fix="Give the protagonist one earned small win before the final beat"))

    # 7. Voice: tics in register family + interior presence
    voice_score = _clamp(9 - summary["by_family"].get("register", 0) * 1.5 - (2 if interior_share < 0.08 else 0))
    authenticity_score = _clamp(min(ai_tics_score + 1, 10) - (1 if shape["wall_share"] > 0.4 else 0))

    scores = {"authenticity": authenticity_score, "voice": voice_score, "interiority": interiority_score, "dialogue": _clamp(dialogue_score), "pacing": _clamp(pacing_score), "sensory": sensory_score, "hook": hook_score, "payoff": payoff_score, "ai_tics": ai_tics_score}
    weights = {"authenticity": 1.6, "voice": 1.3, "interiority": 1.4, "dialogue": 1.0, "pacing": 1.0, "sensory": 0.7, "hook": 1.3, "payoff": 1.0, "ai_tics": 0.7}
    overall = sum(scores[k] * weights[k] for k in scores) / sum(weights.values())
    critical = any(f.severity == "critical" for f in findings)
    verdict = "rewrite" if overall < REWRITE_THRESHOLD or critical else ("accept" if overall >= ACCEPT_THRESHOLD and int(summary["high"]) == 0 else "polish")
    return CriticReport(scores=scores, overall=round(overall, 2), verdict=verdict, findings=findings, source="deterministic", tic_count=len(hits))


def merge_reports(det: CriticReport, model: Optional[CriticReport]) -> CriticReport:
    if model is None:
        return det
    scores: Dict[str, int] = dict(det.scores)
    for k, v in (model.scores or {}).items():
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        scores[k] = min(scores.get(k, iv), max(1, min(10, iv)))
    seen = {f.quote.strip().lower() for f in det.findings if f.quote}
    findings = list(det.findings)
    for f in model.findings:
        key = f.quote.strip().lower()
        if key and key in seen:
            continue
        findings.append(f)
        if key:
            seen.add(key)
    weights = {"authenticity": 1.6, "voice": 1.3, "interiority": 1.4, "dialogue": 1.0, "pacing": 1.0, "sensory": 0.7, "hook": 1.3, "payoff": 1.0, "ai_tics": 0.7}
    overall = sum(scores.get(k, 6) * w for k, w in weights.items()) / sum(weights.values())
    overall = min(overall, float(model.overall) if model.overall else overall)
    verdicts = {"rewrite": 0, "polish": 1, "accept": 2}
    verdict = min((det.verdict, model.verdict), key=lambda v: verdicts.get(v, 1))
    if overall < REWRITE_THRESHOLD:
        verdict = "rewrite"
    return CriticReport(scores=scores, overall=round(overall, 2), verdict=verdict, strongest_moment=model.strongest_moment or det.strongest_moment, findings=findings, source="merged", tic_count=det.tic_count)


def needs_polish(report: CriticReport, *, threshold: float = ACCEPT_THRESHOLD) -> bool:
    return report.verdict != "accept" or report.overall < threshold or any(f.severity in ("critical", "high") for f in report.findings)


__all__ = ["ACCEPT_THRESHOLD", "REWRITE_THRESHOLD", "deterministic_critic", "merge_reports", "needs_polish"]
