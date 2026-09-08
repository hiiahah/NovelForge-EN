"""Evidence verification for reverse-engineered observations.

Producer: ``verify_chapter_analysis`` (run after each chapter analysis and by the
"Lab" workflow helper ``lab_analysis_records``).
Consumers: fingerprint (only verified observations), audits, UI evidence coverage.

Rules enforced here (Phase 3):
- A quote is *verified* only when it occurs in the cited chapter text (exact,
  or whitespace/quote-normalized match). Fabricated quotes are downgraded to
  ``unverified`` and never counted as source evidence.
- A cited chapter that does not exist, or that is an excluded section, makes
  the observation ``invalid_reference``.
- Inference level is never promoted: an observation whose evidence failed can
  at best be ``weakly_inferred``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.forge.textmetrics import normalize_for_index, sha256_text, stable_id

EVIDENCE_SCHEMA_VERSION = "observation-1"
INFERENCE_LEVELS = ("directly_observed", "strongly_inferred", "weakly_inferred", "unknown")
VERIFICATION_STATUSES = ("verified", "unverified", "invalid_reference", "excluded_section", "missing_quote")

_WS = re.compile(r"\s+")
_QUOTE_CHARS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "「": '"', "」": '"', "『": '"', "』": '"'})


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "").translate(_QUOTE_CHARS)).strip().lower()


@dataclass
class Observation:
    observation_id: str
    source_manuscript_id: str
    source_chapter_id: str
    chapter_number: int
    category: str
    observation: str
    evidence_excerpt: str
    evidence_hash: str
    span_start: int
    span_end: int
    confidence: float
    inference_level: str
    verification_status: str
    extraction_model: str = ""
    prompt_version: str = ""
    schema_version: str = EVIDENCE_SCHEMA_VERSION
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def locate(quote: str, text: str) -> Optional[Tuple[int, int]]:
    """Return (start, end) of ``quote`` inside ``text`` or None.

    Exact match first; then a normalization-tolerant match (whitespace and
    curly quotes). Quotes shorter than 4 characters are never accepted because
    they cannot identify a passage.
    """
    q = (quote or "").strip()
    if len(q) < 4 or not text:
        return None
    idx = text.find(q)
    if idx >= 0:
        return idx, idx + len(q)
    nq = _norm(q)
    if not nq:
        return None
    # Build a normalized copy while tracking offsets.
    offsets: List[int] = []
    chars: List[str] = []
    prev_space = False
    for i, ch in enumerate(text.translate(_QUOTE_CHARS)):
        if ch.isspace():
            if prev_space:
                continue
            prev_space = True
            chars.append(" ")
        else:
            prev_space = False
            chars.append(ch.lower())
        offsets.append(i)
    nt = "".join(chars)
    j = nt.find(nq)
    if j < 0:
        return None
    start = offsets[j]
    end = offsets[min(j + len(nq) - 1, len(offsets) - 1)] + 1
    return start, end


def verify_evidence_item(quote: str, chapter_text: str, *, chapter_exists: bool = True, chapter_excluded: bool = False) -> Tuple[str, Optional[Tuple[int, int]]]:
    if not chapter_exists:
        return "invalid_reference", None
    if chapter_excluded:
        return "excluded_section", None
    if not (quote or "").strip():
        return "missing_quote", None
    span = locate(quote, chapter_text)
    return ("verified", span) if span else ("unverified", None)


def _cap_level(level: str, status: str) -> str:
    """Unverified evidence can never support more than a weak inference."""
    level = level if level in INFERENCE_LEVELS else "weakly_inferred"
    if status != "verified" and level in ("directly_observed", "strongly_inferred"):
        return "weakly_inferred"
    return level


def build_observations(
    *,
    manuscript_id: str,
    chapter_id: str,
    chapter_number: int,
    chapter_text: str,
    items: Iterable[Dict[str, Any]],
    default_category: str,
    extraction_model: str = "",
    prompt_version: str = "",
    chapter_exists: bool = True,
    chapter_excluded: bool = False,
) -> List[Observation]:
    """Turn raw evidence dicts (``quote``/``note``/``scene``/``chapter_number``) into verified Observations."""
    out: List[Observation] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        quote = str(raw.get("quote") or raw.get("excerpt") or "")
        cited = raw.get("chapter_number")
        exists = chapter_exists and (cited is None or int(cited) == int(chapter_number))
        status, span = verify_evidence_item(quote, chapter_text, chapter_exists=exists, chapter_excluded=chapter_excluded)
        excerpt = chapter_text[span[0]:span[1]] if span else quote[:200]
        level = _cap_level(str(raw.get("inference_level") or ("directly_observed" if status == "verified" else "weakly_inferred")), status)
        conf = float(raw.get("confidence") or (0.9 if status == "verified" else 0.3))
        if status != "verified":
            conf = min(conf, 0.4)
        obs_text = str(raw.get("observation") or raw.get("note") or "").strip()
        out.append(Observation(
            observation_id=stable_id(manuscript_id, chapter_id, default_category, obs_text, excerpt),
            source_manuscript_id=manuscript_id,
            source_chapter_id=chapter_id,
            chapter_number=int(chapter_number),
            category=str(raw.get("category") or default_category),
            observation=obs_text,
            evidence_excerpt=excerpt[:240],
            evidence_hash=sha256_text(excerpt) if span else "",
            span_start=span[0] if span else -1,
            span_end=span[1] if span else -1,
            confidence=round(max(0.0, min(1.0, conf)), 3),
            inference_level=level,
            verification_status=status,
            extraction_model=extraction_model,
            prompt_version=prompt_version,
            note=str(raw.get("scene") or ""),
        ))
    return out


def verify_chapter_analysis(analysis: Dict[str, Any], chapter_text: str, *, manuscript_id: str, chapter_id: str, chapter_number: int, extraction_model: str = "", prompt_version: str = "", chapter_excluded: bool = False) -> Dict[str, Any]:
    """Verify every evidence item of a ChapterAnalysis-like dict.

    Returns the analysis with ``observations``, ``evidence_verified``,
    ``evidence_total``, ``evidence_coverage`` and per-scene verification. The
    analysis is never marked complete if the evidence list is empty.
    """
    observations: List[Observation] = []
    observations += build_observations(manuscript_id=manuscript_id, chapter_id=chapter_id, chapter_number=chapter_number, chapter_text=chapter_text, items=analysis.get("evidence") or [], default_category="chapter", extraction_model=extraction_model, prompt_version=prompt_version, chapter_excluded=chapter_excluded)
    scenes = analysis.get("scenes") or []
    for idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        obs = build_observations(manuscript_id=manuscript_id, chapter_id=chapter_id, chapter_number=chapter_number, chapter_text=chapter_text, items=scene.get("evidence") or [], default_category=f"scene:{scene.get('function') or 'setup'}", extraction_model=extraction_model, prompt_version=prompt_version, chapter_excluded=chapter_excluded)
        scene["evidence_verified"] = sum(1 for o in obs if o.verification_status == "verified")
        scene["evidence_total"] = len(obs)
        observations += obs
    for key in ("techniques", "reveals", "setups", "payoffs"):
        for item in analysis.get(key) or []:
            if isinstance(item, dict) and item.get("quote"):
                observations += build_observations(manuscript_id=manuscript_id, chapter_id=chapter_id, chapter_number=chapter_number, chapter_text=chapter_text, items=[item], default_category=key, extraction_model=extraction_model, prompt_version=prompt_version, chapter_excluded=chapter_excluded)
    verified = [o for o in observations if o.verification_status == "verified"]
    result = dict(analysis)
    result["observations"] = [o.as_dict() for o in observations]
    result["evidence_total"] = len(observations)
    result["evidence_verified"] = len(verified)
    result["evidence_coverage"] = round(len(verified) / len(observations), 3) if observations else 0.0
    result["evidence_schema_version"] = EVIDENCE_SCHEMA_VERSION
    if not observations:
        result["analysis_status"] = "failed"
        result["analysis_error"] = "no evidence extracted"
    elif not verified:
        result["analysis_status"] = "failed"
        result["analysis_error"] = "no evidence could be verified against the chapter text"
    return result


def verified_observations(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [o for o in (analysis.get("observations") or []) if isinstance(o, dict) and o.get("verification_status") == "verified"]


__all__ = [
    "EVIDENCE_SCHEMA_VERSION", "INFERENCE_LEVELS", "VERIFICATION_STATUSES", "Observation", "build_observations",
    "locate", "verify_chapter_analysis", "verify_evidence_item", "verified_observations",
]
