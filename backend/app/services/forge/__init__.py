"""Forge: evidence-grounded reverse-engineering and controlled chapter generation.

Modules (each has a producer, a consumer and tests):

- ``textmetrics``      deterministic prose measurements (EN + KO aware)
- ``corpus``           manuscript identity, hashes, idempotent import, invalidation
- ``evidence``         verification of quoted evidence against imported chapters
- ``fingerprint``      layered Narrative Fingerprint built from verified observations + metrics
- ``examples``         Reference Example Library + hybrid function-based retrieval
- ``firewall``         Originality Firewall (entity / phrase / n-gram / beat-sequence overlap)
- ``provenance``       dependency graph, stale propagation, Project Narrative Manifest
- ``canon``            temporal CanonFact store (as-of queries, revisions)
- ``compiler``         authoritative fail-closed chapter context compiler
- ``claims``           claim extraction and fact-class validation
- ``validators``       entity / outline / POV / character / temporal / style / originality validators
- ``style_eval``       Style Adherence Report (deterministic + model-based criteria)
- ``sync``             automatic post-chapter synchronization and Next Chapter State Packet
- ``pipeline``         compile -> draft -> validate -> repair -> commit -> sync orchestration
- ``audits``           card-quality audits (duplicates, orphans, stale, contamination)
- ``models``           model-role registry and AuthND model validation
"""
