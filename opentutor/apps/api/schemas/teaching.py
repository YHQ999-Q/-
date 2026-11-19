"""Response schemas for METAMIND Teaching / Learner Model endpoints (module 1)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class ConceptProgressView(BaseModel):
    status: str | None = None
    mastery_score: float | None = None
    quiz_attempts: int = 0
    quiz_correct: int = 0
    gap_type: str | None = None
    time_spent_minutes: int = 0
    fsrs_state: str | None = None


class ConceptState(BaseModel):
    concept_id: str
    name: str
    description: str | None = None
    content_node_id: str | None = None
    goal_slug: str | None = None

    state: str = Field(
        description="not_assessed | assessed_below_mastery | mastered",
    )
    state_reason: str
    mastery_score: float | None = Field(
        default=None, description="None when never assessed — never fabricated as 0.0",
    )
    mastery_source: str
    mastery_threshold: float
    mastered_min_attempts: int

    direct_attempts: int
    direct_attempts_basis: str
    correct: int
    wrong: int
    accuracy: float | None = None
    accuracy_basis: str | None = None
    practice_result_counts: dict[str, int] = Field(default_factory=dict)
    concept_mastery_counts: dict[str, int] = Field(default_factory=dict)
    counts_consistent: bool = False
    last_practiced_at: str | None = None
    next_review_at: str | None = None
    stability_days: float | None = None
    error_categories: dict[str, int] = Field(default_factory=dict)
    evidence_practice_result_ids: list[str] = Field(default_factory=list)
    progress: ConceptProgressView
    content_node_missing: bool = False


class AssessmentSummary(BaseModel):
    state: str = Field(
        description="Course-level view: not_assessed | partially_assessed | all_mastered. "
        "Concept-level states are not_assessed | assessed_below_mastery | mastered.",
    )
    concepts_total: int
    concepts_assessed: int
    concepts_mastered: int
    overall_mastery: float | None = None
    overall_mastery_basis: str


class ReviewDueItem(BaseModel):
    concept_id: str
    name: str
    next_review_at: str | None = None
    mastery_score: float | None = None


class GoalStateView(BaseModel):
    goal_id: str
    goal_slug: str | None = None
    title: str
    objective: str
    success_metric: str | None = None
    status: str
    concept_id: str | None = None
    concept_state: str | None = None
    concept_mastery: float | None = None


class ActivityView(BaseModel):
    quiz_attempts: int
    quiz_correct: int
    accuracy: float | None = None
    time_spent_minutes: int
    last_studied_at: str | None = None
    gap_types: list[str] = Field(default_factory=list)
    practice_results: int


class EvidenceView(BaseModel):
    practice_results: int
    wrong_answers: int
    mastery_snapshots: int
    sources: list[str] = Field(default_factory=list)


class LearnerStateResponse(BaseModel):
    schema_version: str
    generated_at: str
    student_id: str
    course_id: str
    course_name: str
    assessment_summary: AssessmentSummary
    concepts: list[ConceptState]
    weak_concepts: list[ConceptState]
    review_due: list[ReviewDueItem]
    goals: list[GoalStateView]
    error_profile: dict[str, Any]
    activity: ActivityView
    evidence: EvidenceView
    warnings: list[str] = Field(default_factory=list)


class SnapshotWriteRequest(BaseModel):
    course_id: uuid.UUID
    run_tag: str | None = Field(
        default=None,
        max_length=12,
        description="Optional run label, e.g. 'run01' / 'run02'. Stored in the snapshot tag.",
    )
    concept_ids: list[uuid.UUID] | None = Field(
        default=None, description="Restrict the snapshot to specific concept ids.",
    )


class SnapshotWriteResponse(BaseModel):
    run_tag: str | None = None
    schema_version: str
    written: list[dict[str, Any]]
    skipped: list[dict[str, Any]]


class SnapshotPoint(BaseModel):
    snapshot_id: str
    content_node_id: str | None = None
    mastery_score: float
    tag: str | None = None
    run_tag: str | None = None
    state: str | None = None
    recorded_at: str | None = None


class SnapshotHistoryResponse(BaseModel):
    concept_id: str | None = None
    concept_name: str | None = None
    content_node_id: str | None = None
    include_upstream: bool = False
    points: list[SnapshotPoint]
    reason: str | None = None


# ── Module 2: Evaluator ────────────────────────────────────────────────────


class EvaluationRequest(BaseModel):
    course_id: uuid.UUID
    objective_slug: str | None = Field(
        default=None, max_length=64, description="Learning objective key, e.g. 'g1'"
    )
    objective_id: uuid.UUID | None = Field(
        default=None, description="Knowledge node id; alternative to objective_slug"
    )
    session_id: str | None = Field(
        default=None, max_length=64, description="Chat session id or demo run label"
    )
    run_tag: str | None = Field(
        default=None, max_length=32, description="Demo run label, e.g. 'run01' / 'run02'"
    )
    strategy_version: str | None = Field(
        default=None, max_length=64, description="Teaching strategy/skill version used"
    )
    problem_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Objective items to score; defaults to all items of the objective's concept",
    )
    open_answer: str | None = Field(
        default=None, max_length=4000, description="Student's free-text explanation"
    )
    open_question: str | None = Field(default=None, max_length=2000)
    transfer_case: str | None = Field(
        default=None, max_length=2000, description="The new case for transfer scoring"
    )
    transfer_answer: str | None = Field(default=None, max_length=4000)
    rubric_version: str | None = Field(
        default=None,
        max_length=32,
        description="Reject the request when this does not match the implemented rubric",
    )


class EvaluationScores(BaseModel):
    understanding: int | None = None
    application: int | None = None
    transfer: int | None = None
    retention: int | None = Field(
        default=None, description="Always null for immediate evaluation: retention needs a delayed test"
    )


class EvaluationModelInfo(BaseModel):
    provider: str | None = None
    name: str | None = None


class EvaluationResponse(BaseModel):
    evaluation_id: str
    student_id: str
    course_id: str
    session_id: str | None = None
    run_tag: str | None = None
    objective_slug: str | None = None
    objective_id: str | None = None
    objective_text: str | None = None
    rubric_version: str
    strategy_version: str | None = None
    status: str = Field(description="completed | partial | failed")
    verdict: str | None = Field(
        description="achieved | partially_achieved | not_achieved | insufficient_evidence"
    )
    scores: EvaluationScores
    dimensions_measured: int
    unmeasured_dimensions: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] | None = None
    rationale: dict[str, Any] | None = None
    inputs: dict[str, Any] | None = None
    content_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    model: EvaluationModelInfo
    created_at: str | None = None


class EvaluationListResponse(BaseModel):
    course_id: str
    count: int
    evaluations: list[EvaluationResponse]


class RubricResponse(BaseModel):
    rubric_version: str
    scale: dict[str, Any]
    deferred: dict[str, str]
    dimensions: dict[str, Any]
    verdict_rules: list[str]


# ── Module 3: Reflection Engine ────────────────────────────────────────────


class ReflectionRequest(BaseModel):
    course_id: uuid.UUID
    evaluation_id: uuid.UUID = Field(description="The evaluation to reflect on")
    subjective_feedback: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional student self-report; stored and cited as subjective, not objective evidence",
    )


class ReflectionProblem(BaseModel):
    statement: str | None = None
    severity: str | None = None
    dimension: str | None = None


class ReflectionResponse(BaseModel):
    reflection_id: str
    evaluation_id: str
    student_id: str
    course_id: str
    objective_slug: str | None = None
    objective_id: str | None = None
    session_id: str | None = None
    run_tag: str | None = None
    strategy_version: str | None = None
    rubric_version: str | None = None
    status: str = Field(
        description="completed | insufficient_evidence | no_improvement_needed | failed"
    )
    problem: ReflectionProblem
    cause_hypothesis: dict[str, Any] | None = None
    improvement_proposal: dict[str, Any] | None = None
    verification_plan: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    input_package: dict[str, Any] | None = None
    input_hash: str | None = None
    attempts: int = 0
    model: EvaluationModelInfo
    error_code: str | None = None
    error_message: str | None = None
    created_at: str | None = None


class ReflectionListResponse(BaseModel):
    course_id: str
    count: int
    reflections: list[ReflectionResponse]


# ── Module 4: Skill Memory + Strategy Selector ─────────────────────────────


class SkillImportResponse(BaseModel):
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    total: int = 0


class SkillVersionView(BaseModel):
    version_id: str
    skill_id: str
    version: int
    status: str
    trigger_conditions: list[str] = Field(default_factory=list)
    applicable_objectives: list[str] = Field(default_factory=list)
    target_dimensions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_effect: str | None = None
    verification: dict[str, Any] = Field(default_factory=dict)
    reused_skills: list[str] = Field(default_factory=list)
    origin_kind: str | None = None
    change_summary: str | None = None
    source_reflection_id: str | None = None
    source_evaluation_id: str | None = None
    created_by: str | None = None
    content_hash: str | None = None
    # Frozen reflection snapshot so a version can show its origin even if the
    # reflection row is later removed (P0-1). Exposed read-only for auditing.
    provenance: dict[str, Any] | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class SkillView(BaseModel):
    skill_id: str
    slug: str
    name: str
    description: str | None = None
    origin: str
    enabled: bool
    current_version: int
    version_count: int
    source_reflection_id: str | None = None
    source_evaluation_id: str | None = None
    seed_path: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SkillDetailResponse(BaseModel):
    skill: SkillView
    current_version_detail: SkillVersionView | None = None
    history_stats: dict[str, Any] = Field(default_factory=dict)


class SkillListResponse(BaseModel):
    count: int
    skills: list[SkillView]


class VersionListResponse(BaseModel):
    skill_id: str
    slug: str
    current_version: int
    count: int
    versions: list[SkillVersionView]


class VersionDiffResponse(BaseModel):
    skill_id: str
    slug: str
    from_version: int | None = None
    to_version: int
    summary: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    changes: dict[str, Any] = Field(default_factory=dict)


class SkillToggleRequest(BaseModel):
    enabled: bool


class SkillRollbackRequest(BaseModel):
    target_version: int = Field(ge=1)


class SkillMutationResponse(BaseModel):
    skill: SkillView
    validation: dict[str, Any] = Field(default_factory=dict)
    action: str


class EvolveFromReflectionRequest(BaseModel):
    course_id: uuid.UUID
    reflection_id: uuid.UUID
    enable_if_valid: bool = True
    skill_slug: str | None = Field(
        default=None, max_length=80, description="Force the target slug (update that skill)"
    )


class EvolutionResponse(BaseModel):
    status: str = Field(description="created | version_added | duplicate | skipped")
    reason: str
    skill: SkillView | None = None
    version: SkillVersionView | None = None
    validation: dict[str, Any] | None = None


class SkillSelectionRequest(BaseModel):
    course_id: uuid.UUID
    objective_slug: str | None = Field(default=None, max_length=64)
    error_type: str | None = Field(default=None, max_length=40)
    scene: str | None = Field(default=None, max_length=50)
    message: str | None = Field(default=None, max_length=2000)
    weakness_dimension: str | None = Field(default=None, max_length=24)
    session_id: str | None = Field(default=None, max_length=64)
    run_tag: str | None = Field(
        default=None, max_length=32, description="Demo run label, e.g. 'run01' / 'run02'"
    )
    persist: bool = True


class CandidateView(BaseModel):
    skill_id: str
    slug: str
    name: str
    version_id: str
    version: int
    score: float
    matched_conditions: list[str] = Field(default_factory=list)
    condition_total: int = 0
    objective_match: bool = False
    dimension_match: bool = False
    error_match: bool = False
    scene_match: bool = False
    history: dict[str, Any] = Field(default_factory=dict)


class SkillSelectionResponse(BaseModel):
    selected: CandidateView | None = None
    fallback_to_native_tutor: bool
    reason: str
    candidates: list[CandidateView] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    invocation_id: str | None = None


class InvocationView(BaseModel):
    invocation_id: str
    skill_id: str
    version_id: str
    version: int
    session_id: str | None = None
    run_tag: str | None = None
    objective_slug: str | None = None
    scene: str | None = None
    status: str
    match_score: float | None = None
    selected_at: str | None = None
    injected_at: str | None = None
    evaluated_at: str | None = None
    executed_at: str | None = None
    prompt_hash: str | None = None
    prompt_injection_verified: bool | None = None
    execution_state: str | None = None
    evaluation_id: str | None = None
    selection: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    notes: str | None = None
    lifecycle_note: str | None = None


class InvocationListResponse(BaseModel):
    course_id: str
    count: int
    invocations: list[InvocationView]


class InvocationInjectRequest(BaseModel):
    prompt: str | None = Field(
        default=None, max_length=60000,
        description="The FULL system prompt that reached the LLM (P0-3). Injection is only "
                    "confirmed when the skill marker is present in it.",
    )
    marker: str | None = Field(
        default=None, max_length=200,
        description="Expected skill marker, e.g. '<!-- skill:socratic_questioning@v2 -->'",
    )


class InvocationEvaluateRequest(BaseModel):
    evaluation_id: uuid.UUID
    target_dimension: str | None = Field(default=None, max_length=24)
