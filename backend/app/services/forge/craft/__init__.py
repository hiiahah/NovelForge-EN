"""Prose Craft: multi-pass scene drafting, subtext packets, adversarial critic, line polish and hook sharpening.

Entry point for the pipeline is ``passes.craft_chapter``; the deterministic
analyzers (``tics``, ``critic``, ``hooks``, ``scenes``, ``subtext``) are also
exposed over the API so the studio can grade any chapter without a model.
"""

from app.services.forge.craft.passes import CRAFT_VERSION, CraftInputs, CraftOptions, CraftOutcome, craft_chapter

__all__ = ["CRAFT_VERSION", "CraftInputs", "CraftOptions", "CraftOutcome", "craft_chapter"]
