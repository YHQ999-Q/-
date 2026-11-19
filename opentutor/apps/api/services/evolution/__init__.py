"""METAMIND Evolution Engine — shared service package.

Modules are added one at a time and kept independent:

* ``learner_state``   (module 1) — read-only aggregation of the existing
  learner records into one traceable snapshot for the Evolution Engine.
* ``evaluator``       (module 2) — not implemented yet.
* ``reflection_engine`` (module 3) — not implemented yet.

Nothing in this package writes to ``LearningProgress`` / ``ConceptMastery``.
The existing BKT + FSRS write paths in ``services/progress`` and
``services/loom_mastery`` stay the single source of truth.
"""
