# Story Memory: how a chapter-300 continuation remembers chapter 12

The Novel Bible answers *what is true about the world*. Story Memory answers
*what has actually happened on the page so far*, and feeds both into every
chapter generation automatically.

## The problem it solves

Before Story Memory, chapter continuation received the previous chapter's full
text plus the Bible ledgers. Nothing captured the raw narrative state of
chapters 1..N-2: who is where, who holds what, which hooks the reader is still
waiting on, what the last paragraph of the last chapter actually was. Long
serials drift. Story Memory removes the drift with four cooperating pieces.

## 1. Chapter Digest (`Chapter Digest` card)

One structured LLM extraction per written chapter (`Chapter Digest Extraction`
prompt, `ChapterDigest` schema in `app/schemas/story_memory.py`):

| Field group | What it captures |
| --- | --- |
| `one_line`, `summary`, `opening_state`, `ending_state`, `last_paragraph_gist` | The chapter, and *exactly* where it stops |
| `events[]` with `significance` and `consequence` | Ordered beats later chapters must honour |
| `state_changes[]` (`location`, `possession`, `injury`, `power`, `alive_dead`, ...) | Persistent entity state that carries forward |
| `knowledge_deltas[]` | Who learned what (drives the prohibited-information list) |
| `hooks_opened[]` / `hooks_closed[]` | What the reader is waiting for / what was resolved |
| `promises_made[]`, `objects_introduced[]`, `named_extras[]` | Things a careless later chapter would silently re-invent |
| `continuity_risks[]` | "It is still night", "Mira is unarmed" |
| `tension_start/end`, `hook_strength`, `dominant_function`, `rewards_delivered` | Rhythm data for the planner |
| `quotable_lines[]`, `style_notes[]` | Callback material and voice consistency |

System fields (`source_hash`, `stale`, `word_count`, `digested_at`, ...) are
marked `x-ai-exclude` and never requested from the model.

Digests are keyed by a hash of the chapter text: re-digesting unchanged text is
a no-op; editing the text marks the digest `stale` (synchronously, in the
`card.saved` handler). Digest cards live under a `Story Memory` folder so they
appear in the card tree, the context DSL and exports like any other card.

### Auto-digest

`Story Memory Settings` (a singleton card per project; no migration) controls:

- `auto_digest_on_save` (default on) and `auto_digest_min_words` (400): when a
  Chapter Text card is saved with changed content, a digest is extracted in a
  background thread. AI drafts still awaiting confirmation are skipped.
- `digest_llm_config_id`: model for digests; falls back to the chapter card's or
  type's model. Without any configured model nothing runs (stale marking still
  happens).
- `recent_window` (3), `mid_window` (12), `recap_budget_chars` (7000),
  `hook_overdue_chapters` (8), `inject_into_continuation`,
  `inject_brief_into_continuation`.

Batch: `POST /api/story-memory/digests/batch` digests every missing/stale
chapter sequentially (`force` re-digests everything).

## 2. Story So Far (deterministic, no LLM)

`StorySoFarCompiler.compile(project_id, next_chapter, budget_chars)` folds
every digest before `next_chapter` into:

- **Tiers**: `recent` (full detail), `mid` (one line + permanent state + strong
  hooks), `distant` (grouped by volume / blocks of 10 with pivotal events).
- **Carry-forward state**: latest value per `(entity, kind)` in chapter order,
  location, alive/dead, last seen chapter.
- **Dangling hooks**: opened and never closed (fuzzy token match against
  `hooks_closed`), with `overdue` computed from age × strength.
- **Story clock**, **last ending state** and **last paragraph gist**.

The prompt-ready `text` is trimmed to the budget by dropping distant → mid →
carry-forward detail → recent detail, never the previous chapter's ending.

## 3. Injection into generation

`assemble_context` (used by `/api/context/assemble` and by chapter
continuation) now returns `story_memory` and `chapter_brief` next to
`bible_context`. `enrich_continuation_context_info` appends the
`[Story So Far …]` and `[Next Chapter Brief …]` blocks to the continuation
context when the project settings (or the explicit request flags
`include_story_memory` / `include_chapter_brief`) allow it. Story Memory does
not need participants, so it is assembled even when the chapter has no entity
list yet. The continuation dialog exposes both toggles and shows how much of
the book is currently remembered.

## 4. Continuity Guard

`POST /api/story-memory/continuity/check` runs deterministic checks on a draft
against the Bible slice and the digests:

| code | what it catches |
| --- | --- |
| `prohibited_reveal` | compiled prohibited knowledge surfaces in prose (critical) |
| `dead_entity` | an entity recorded dead acts/speaks (critical) |
| `possession_conflict` | an item recorded as lost is used again |
| `location_teleport` | POV opens somewhere else than the previous ending with no transition |
| `unknown_entity` | recurring proper name not in Bible, digests or named extras |
| `head_hopping`, `time_inversion` | reused Forge validators |
| `dropped_strong_hook` | strong hook expected "next chapter" not addressed |
| `forbidden_outcome` | outline's `forbidden_outcomes` appear |

`use_llm: true` adds a conservative model pass (`Continuity Guard` prompt,
`LlmContinuityFindings` schema) whose cited issues are merged; deterministic
findings are never removed. The report carries a score, a verdict
(`clean | review | block`) and spans so the editor can jump to the offending
text.

## 5. Next Chapter Brief and Bible Health

`POST /api/story-memory/brief` assembles *must address / should consider /
avoid / rhythm advice* from dangling hooks, due and overdue promises, neglected
threads, pending relationship shifts, prohibited knowledge, the chapter outline
(beats, allowed/forbidden outcomes) and reward drought.

`GET /api/story-memory/health` scores foundation, character depth,
relationship coverage, ledgers, truth hygiene, story-memory coverage and audit
warnings into one weighted number with per-dimension fix hints.

## UI

- **Novel Bible → Story Memory**: health score, coverage, tension strip,
  digest timeline, digest detail, the exact Story So Far text sent to the
  model, world state table, dangling hooks, Next Chapter Brief, settings.
- **Chapter editor → Continuity tab**: instant / deep continuity check with
  click-to-jump issues, "Digest this chapter", compact brief, recap preview.
- **Continue dialog**: toggles for Story So Far / Brief and a memory status line.

## Tests

`backend/tests/test_story_memory.py` covers settings, digest storage and
idempotency, stale marking, background auto-digest, batch, tiering/budget,
carry-forward, dangling hooks, context and continuation injection, all guard
codes, the LLM merge, the planner and the health score without any network
call. `frontend/src/renderer/src/composables/__tests__/useStoryMemory.test.ts`
covers the pure helpers.
