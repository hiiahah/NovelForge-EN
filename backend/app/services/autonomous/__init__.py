"""Autonomous novel production: upload an EPUB -> choose a storyline -> finished novel.

Modules:

- ``model_client``  role-based structured / text model calls with format repair,
                    retry accounting and ``ModelInvocation`` persistence
- ``failures``      failure categories and the deterministic recovery ladder
- ``source_stages`` INGEST -> NORMALIZE/STRUCTURE -> SOURCE_ANALYSIS -> VERIFICATION
                    -> FINGERPRINT -> EXAMPLE_LIBRARY (wraps Lab + Forge services)
- ``storylines``    STORYLINE_GENERATION: original options + originality/diversity gates
- ``architecture``  NOVEL_ARCHITECTURE + BIBLE_BUILD: Story Contract, Bibles, ledgers, canon seed
- ``chapter_plan``  CHAPTER_PLAN_BUILD + NOVEL_PREFLIGHT: chapter blueprints -> Chapter Outlines
- ``chapter_loop``  CHAPTER_GENERATION_LOOP: compile/draft/validate/repair/commit/sync/reforecast
- ``audit``         WHOLE_NOVEL_AUDIT + GLOBAL_REPAIR
- ``export``        EXPORT: EPUB / DOCX / Markdown / text / reports
- ``runner``        the durable, resumable job state machine
"""
