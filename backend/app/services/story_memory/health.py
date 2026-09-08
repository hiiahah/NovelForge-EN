"""Bible Health: one deterministic coverage score with actionable dimensions."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlmodel import Session

from app.schemas.story_memory import BibleHealth, HealthDimension
from app.services.bible.bible_service import BibleService
from app.services.story_memory.digest_service import DigestService


def _c(card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _filled(content: Dict[str, Any], min_fields: int = 3) -> bool:
    return sum(1 for v in content.values() if v not in (None, "", [], {})) >= min_fields


def bible_health(session: Session, project_id: int) -> BibleHealth:
    bible = BibleService(session)
    digests = DigestService(session)
    dims: List[HealthDimension] = []

    # Foundation singletons.
    foundation = ["Story Foundation", "Reader Contract", "Theme Map", "Style Profile", "Narrative Architecture", "Power System"]
    present = [t for t in foundation if (c := bible.singleton(project_id, t)) and _filled(_c(c))]
    dims.append(HealthDimension(key="foundation", label="Foundation layer", weight=2.0, score=int(100 * len(present) / len(foundation)), detail=f"{len(present)}/{len(foundation)} foundation cards filled", fix_hint="Run Create Bible or fill the missing foundation cards" if len(present) < len(foundation) else ""))

    # Characters deepened.
    chars = bible.cards_of_type(project_id, "Character Card")
    deep = [c for c in chars if (_c(c).get("dramatic_design") or {}).get("external_goal") and (_c(c).get("voice") or {}).get("verbal_tells")]
    main = [c for c in chars if str(_c(c).get("role_type") or "").lower().startswith(("protag", "main", "antag", "deuter"))] or chars[:5]
    main_deep = [c for c in main if c in deep]
    score = int(100 * len(main_deep) / len(main)) if main else 0
    dims.append(HealthDimension(key="characters", label="Character depth", weight=2.0, score=score, detail=f"{len(deep)}/{len(chars)} characters deepened ({len(main_deep)}/{len(main)} principal)", fix_hint="Use 'Deepen' on principal characters" if score < 100 else ""))

    # Relationship coverage among principal characters.
    arcs = bible.cards_of_type(project_id, "Relationship Arc")
    names = [str(_c(c).get("name") or c.title).lower() for c in main]
    pairs = {(a, b) for i, a in enumerate(names) for b in names[i + 1:]}
    covered = set()
    for arc in arcs:
        a, b = str(_c(arc).get("character_a", "")).lower(), str(_c(arc).get("character_b", "")).lower()
        covered.add(tuple(sorted((a, b))))
    hit = sum(1 for p in pairs if tuple(sorted(p)) in covered)
    score = int(100 * hit / len(pairs)) if pairs else (100 if arcs else 0)
    dims.append(HealthDimension(key="relationships", label="Relationship arcs", weight=1.2, score=score, detail=f"{hit}/{len(pairs)} principal pairs have an arc", fix_hint="Add Relationship Arc cards for principal pairs" if score < 70 else ""))

    # Ledgers populated.
    ledgers = {"Plot Thread": 2, "Promise Payoff": 3, "Knowledge Fact": 2, "World Rule": 3, "Timeline Event": 3}
    have = {t: len(bible.cards_of_type(project_id, t)) for t in ledgers}
    ledger_score = int(100 * sum(min(1.0, have[t] / n) for t, n in ledgers.items()) / len(ledgers))
    dims.append(HealthDimension(key="ledgers", label="Ledgers", weight=1.5, score=ledger_score, detail=", ".join(f"{t.split()[0].lower()}s {have[t]}" for t in ledgers), fix_hint="Populate threads, promises, secrets and world rules before drafting" if ledger_score < 80 else ""))

    # Evidence & truth hygiene.
    ledger_cards = [c for t in ledgers for c in bible.cards_of_type(project_id, t)]
    if ledger_cards:
        disputed = sum(1 for c in ledger_cards if _c(c).get("truth_status") == "disputed")
        evidenced = sum(1 for c in ledger_cards if _c(c).get("truth_status") == "canon" and _c(c).get("evidence"))
        canon = sum(1 for c in ledger_cards if _c(c).get("truth_status") == "canon")
        hygiene = 100 - min(60, disputed * 15) - (0 if canon == 0 else int(30 * (1 - evidenced / canon)))
        dims.append(HealthDimension(key="hygiene", label="Truth hygiene", weight=1.0, score=max(0, hygiene), detail=f"{disputed} disputed; {evidenced}/{canon} canon entries carry evidence", fix_hint="Resolve disputed facts; accept Living Bible proposals so canon carries evidence" if hygiene < 80 else ""))
    else:
        dims.append(HealthDimension(key="hygiene", label="Truth hygiene", weight=1.0, score=50, detail="No ledger entries yet", fix_hint="Ledger entries carry truth status and evidence"))

    # Story memory coverage.
    cov = digests.coverage(project_id)
    written = len(cov["written"])
    if written:
        fresh = written - len(cov["missing"]) - len(cov["stale"])
        score = int(100 * fresh / written)
        dims.append(HealthDimension(key="memory", label="Story memory", weight=2.5, score=score, detail=f"{fresh}/{written} written chapters have fresh digests ({len(cov['stale'])} stale)", fix_hint="Digest missing/stale chapters so generation remembers them" if score < 100 else ""))
    else:
        dims.append(HealthDimension(key="memory", label="Story memory", weight=0.5, score=100, detail="No chapters written yet", fix_hint=""))

    # Audit warnings.
    audit = bible.audit(project_id)
    warnings = audit.get("warnings") or []
    high = sum(1 for w in warnings if w.get("severity") == "high")
    audit_score = max(0, 100 - high * 15 - (len(warnings) - high) * 5)
    dims.append(HealthDimension(key="audits", label="Continuity audits", weight=1.5, score=audit_score, detail=f"{len(warnings)} warnings ({high} high)", fix_hint="Open the Audits panel and resolve high-severity warnings" if audit_score < 85 else ""))

    total_w = sum(d.weight for d in dims) or 1.0
    score = int(round(sum(d.score * d.weight for d in dims) / total_w))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    weakest = sorted(dims, key=lambda d: d.score * d.weight)[:2]
    summary = "Bible is production-ready." if score >= 90 else "Weakest: " + "; ".join(f"{d.label} ({d.score})" for d in weakest)
    return BibleHealth(project_id=project_id, score=score, grade=grade, dimensions=dims, summary=summary)
