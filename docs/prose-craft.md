# Prose Craft — multi-pass chapter quality engine

Prose Craft is the layer between *compile the chapter context* and *validate the
draft* in the Forge pipeline. It exists because a single drafting call, however
large the model, rushes through beats, narrates from outside the protagonist's
head and fades out at the end. Craft turns one call into a small editorial
room: plan the scenes, brief every character's agenda, draft one scene at a
time, have an adversarial editor grade the result, polish only the cited lines,
and sharpen the last two paragraphs into a real serialized hook.

Token cost is deliberately *not* an optimization target; the presets exist so
the same engine can also run cheaply when you want a quick draft.

```
compile ──► [scene plan] ──► [subtext packets] ──► draft scene 1 ─ handoff ─► scene 2 ─ … ──► stitch
                                                                                              │
                              ┌──── polish (≤N passes, only cited spans) ◄── critic (det + model) ◄─┘
                              │
                              └──► hook sharpener (final 2 paragraphs) ──► critic (after) ──► validators ──► commit
```

Every model pass has a deterministic fallback and a guard rail: a polish that
scores lower than its input is discarded, a hook rewrite that is not stronger is
discarded, a scene plan that does not cover every beat exactly once is replaced
by the deterministic plan. The chapter is never left worse than the pass found
it.

## The passes

### 1. Scene decomposition (`craft/scenes.py`)

The outline's ordered beats are grouped into 2–5 scenes. Deterministically:
beat function tags that open (`cold_open`, `time_skip`, `arrival`…) or close
(`chapter_cliffhanger`, `reveal`, `exit`…) a scene, participant changes, and
"Later / Meanwhile / The next morning" phrasing all start a new scene. With the
`full` preset the model may replace this plan; it is accepted only if it covers
beat indexes `1..n` exactly once, in order, with 2–5 scenes. Each scene carries
a dramatic question, a **turn**, the protagonist's private thread
(`interiority_focus`) and one planned **micro-payoff**.

Each scene is drafted with the same compiled chapter context plus a
`[THIS SCENE — k of n]` brief and, from scene 2 on, the **exact ending lines**
of the previous scene, the last spoken line and who said it, bodies/objects as
left, and the carried tension — extracted from the drafted prose, not
paraphrased. Intermediate scenes are told not to resolve the chapter and not to
emit metadata blocks; the final scene lands the hook and emits
`<chapter_summary>`, `<scene_handoff>` and `<claims>` for the whole chapter.
Scenes are stitched with the project's three-line-break scene separator.

### 2. Subtext packets (`craft/subtext.py`)

Before any dialogue is written, every non-POV participant gets an **agenda**
compiled from their Character Card (`dramatic_design`, `voice`, `competence`,
`consistency_rules`), the Relationship Arc with the POV, and Knowledge Facts the
character knows but the POV does not:

- what they want from the protagonist *in this scene*
- what they are suppressing (secret, secret desire, fear, withheld knowledge)
- leverage, fear in scene, tactic, tell when lying
- speech cadence / formality / humor / verbal tells, how they address the POV,
  what they never say

plus the POV's own private agenda (want vs. unadmitted need, operating belief)
and one thing the POV is likely to **misread** (public image ≠ self image) —
fuel for dramatic irony. The packet closes with the rule *nobody states their
want in the first exchange; every line is a move*.

This is deterministic on purpose: the packet is exactly as rich as the Bible,
so deepening a Character Card measurably improves dialogue.

### 3. Protagonist voice (`ProtagonistVoice` on Character Cards)

A new optional Character Card group, filled by **Character Bible Deepening**:
`archetype`, `inner_register` vs `composure_mask`, `notices_first`,
`private_humor`, `self_deception`, `calculation_style`, `signature_moves`,
`forbidden_interior`. The Forge context compiler emits it as a
`PROTAGONIST VOICE` section whenever the POV card has one; the craft layer
derives a serviceable profile from `personality` / `voice` / `dramatic_design`
when it does not. It is marked `x-ai-exclude` on the card schema so blueprint
generation never invents it.

### 4. Webnovel critic (`craft/critic.py`, `craft/tics.py`)

The deterministic grader always runs and scores 1–10 on **authenticity, voice,
interiority, dialogue, pacing, sensory, hook, payoff** (plus `ai_tics`) from:

- the **AI-tic catalogue** (`tics.RULES`): "not X, but Y" scaffolds, "a
  testament to", "the man who…", "something shifted", "a breath he didn't know
  he was holding", "in that moment", emotion cocktails, therapy vocabulary,
  adverb-propped tags, melodrama stock phrases, adjective triads, closing
  maxims… Each rule carries a severity, a rewrite hint and a per-chapter
  tolerance (a single "in that moment" is fine; the second is a tic).
- interiority share (sentences carrying private reasoning), dialogue share and
  lecture-length speeches, sensory anchors per 1000 words, paragraph walls,
  word-target shortfall, and the hook/payoff analysis below.

With `model_critic` on, an adversarial acquisitions-editor prompt grades the
same draft (it receives the deterministic pre-scan so it looks *past* it) and
the two reports are merged: the lower score per dimension wins, findings are
de-duplicated by quote, the model's `strongest_moment` is protected during
polish. Verdicts: `accept` (≥ 8.5 and no high-severity findings), `polish`,
`rewrite` (< 5.5).

### 5. Line polish (`passes.polish`)

Runs while the verdict is not `accept`, up to `max_polish_passes`. The polisher
receives only the constraint sections (POV, knowledge boundary, fact classes,
prohibited), the voice profile, the cited findings and the protected passage,
and must leave every uncited sentence byte-identical and add no facts. Output is
rejected if it is too short (an adaptive floor that allows cutting the cited
spans) or if the deterministic score fell. Metadata blocks are stripped before
and re-attached after so the polisher can never corrupt them.

### 6. Hook & payoff engine (`craft/hooks.py`, `passes.sharpen_hook`)

`analyze_hook` classifies the ending (`textmetrics.classify_ending`) and detects
the hook type in the final paragraphs — **threat arrival, revelation, crisis,
decision, reversal, question** — with a fade-out detector ("and slept", "let the
quiet settle", "for now") that overrides earlier cues: a threat two paragraphs
up followed by bedtime is still a fade-out. Strength 0–10; `is_soft` when the
ending fades or scores under 4. Micro-payoff detectors look for a deduction,
tactical win, verbal win, comic beat, status gain or reward anywhere in the
chapter. When the ending is soft (or under `min_hook_strength`) the sharpener
rewrites only the last two paragraphs into the suggested hook type (from the
outline's `closing_hook` when it names one) and keeps the result only if it is
measurably stronger.

## Presets

| preset | scenes | model plan | subtext | critic | model critic | polish | hook | est. calls |
|---|---|---|---|---|---|---|---|---|
| `off` | – | – | – | – | – | – | – | 1 |
| `economy` | – | – | ✓ (voice only) | det | – | – | – | 1 |
| `balanced` | ✓ | – | ✓ | det+model | ✓ | ≤2 | ✓ | scenes + 4–6 |
| `full` | ✓ | ✓ | ✓ | det+model | ✓ | ≤3 | ✓ | scenes + 6–9 |

`GET /api/craft/presets` returns the live table. The **Forge Pipeline** panel
has a *Prose craft* selector (default `full`); **Create Novel** jobs follow the
quality preset (`economy → economy`, `balanced → balanced`, `quality → full`)
unless `craft_preset` is set explicitly; `off` restores the legacy single-shot
pipeline.

## Where it lives

- `backend/app/services/forge/craft/` — `scenes`, `subtext`, `tics`, `critic`,
  `hooks`, `prompts`, `passes` (orchestrator `craft_chapter`).
- `backend/app/schemas/craft.py` — `ScenePlan`, `SubtextPacket`,
  `ProtagonistVoice`, `CriticReport`, `HookAnalysis`, `CraftReport`.
- `PipelineOptions.craft: CraftOptions | None` — `None` keeps the exact legacy
  behaviour; the craft report is stored on the run's `validation_report.craft`
  and returned as `PipelineResult.craft`.
- New autonomous roles: `scene_planner`, `webnovel_critic`, `line_polisher`,
  `hook_editor` (own temperature / token / retry policies; drafting reuses
  `drafter`). All are budgeted like any other role.
- API: `POST /api/craft/grade` (any text), `POST /api/craft/grade/card`,
  `POST /api/craft/scene-plan` (deterministic plan + packets + voice for an
  outline), `GET /api/craft/presets`, `GET /api/craft/tics/catalogue`.
- UI: **Craft grade** card in the editor's *Continuity* tab (instant, no model;
  click a finding to jump), *Prose craft* selector and a *Craft* score column in
  the Forge panel, *Prose craft* selector in Create Novel.

## Tests

`backend/tests/test_prose_craft.py` covers the tic catalogue and tolerances,
hook/payoff analysis, the deterministic critic and report merging, scene
planning coverage/shape, handoff extraction and brief rendering, subtext
packets from Bible cards, the full scene-by-scene pipeline with a tic-heavy
fake drafter (tics removed, hook sharpened, score up, metadata preserved,
call counts exact), graceful degradation when model outputs are unusable, the
legacy single-shot path being byte-for-byte unchanged, preset mapping, and the
API. No live model calls.
