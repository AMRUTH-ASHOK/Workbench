from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
import math


def now():
    return datetime.now(timezone.utc).isoformat()


DIMENSIONS = ["responsiveness", "technical_depth", "communication", "escalation_judgment", "resolution_effectiveness"]
LABELS = {"ai_sat": "AI SAT", "overall_se": "Overall SE", "responsiveness": "Responsiveness", "technical_depth": "Technical correctness", "communication": "Communication", "escalation_judgment": "Escalation judgment", "resolution_effectiveness": "Resolution effectiveness"}


def score_band(value):
    if value is None:
        return "unscored"
    return "good" if value >= 8 else "watch" if value > 7 else "attention"


def timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseRecord(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9]{18}$")
    number: str = Field(pattern=r"^\d{6,10}$")
    account: str = ""
    subject: str = ""
    status: str
    priority: str = ""
    owner_email: str
    owner_name: str = ""
    is_closed: bool = False
    closed_at: str | None = None
    updated_at: str
    source: str = "support-tooling-live"
    revision: str
    metadata: dict = Field(default_factory=dict)

    @field_validator("priority", "account", "subject", "owner_name", mode="before")
    @classmethod
    def empty_text(cls, value):
        return "" if value is None else value

    @field_validator("updated_at", "closed_at")
    @classmethod
    def valid_time(cls, value):
        return timestamp(value)


class Evaluation(Record):
    case_id: str = Field(pattern=r"^[a-zA-Z0-9]{18}$")
    result_id: str
    source: str
    phase: Literal["open", "closed"]
    produced_at: str
    covered_at: str | None = None
    model: str | None = None
    rubric: str | None = None
    score_status: str = "latest"
    scores: dict[str, float | None]
    reasoning: dict[str, str] = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)

    @field_validator("produced_at", "covered_at")
    @classmethod
    def valid_time(cls, value):
        return timestamp(value)

    @field_validator("scores")
    @classmethod
    def validate_scores(cls, scores):
        for key, value in scores.items():
            if key not in LABELS and key not in {"customer_engineer", "customer_product", "customer_effort"}:
                raise ValueError("Unknown score dimension")
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 10):
                raise ValueError("Scores must be finite values between 0 and 10")
        present = [scores[k] for k in DIMENSIONS if scores.get(k) is not None]
        if present:
            calculated = sum(present) / len(present)
            if scores.get("overall_se") is not None and abs(scores["overall_se"] - calculated) > .051:
                raise ValueError("Overall SE does not match non-null dimension mean")
            scores["overall_se"] = round(calculated, 4)
        return scores


class SourceStatus(Record):
    key: str
    state: Literal["ready", "needs_auth", "unavailable", "partial", "checking"]
    message: str
    checked_at: str = Field(default_factory=now)
    login_url: str | None = None


class Bundle(Record):
    schema_version: Literal[1] = 1
    run_id: str
    owner_email: str
    collected_at: str
    inventory_complete: bool
    inventory_source: str
    cases: list[CaseRecord]
    evaluations: list[Evaluation] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    usage: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Evidence(Record):
    title: str
    source: str = ""
    url: str | None = None
    detail: str = ""
    observed_at: str | None = None


class Concern(Record):
    title: str
    reason: str
    metric: str | None = None


class ProposedAction(Record):
    title: str
    owner: str
    reason: str = ""
    due_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$")
    waiting_on: str = "us"

    @field_validator("due_at")
    @classmethod
    def valid_due(cls, value):
        return timestamp(value)


class Coverage(Record):
    name: str
    state: str
    detail: str


class DraftInput(Record):
    kind: Literal["customer", "engineering", "internal", "feedback"]
    title: str
    body: str
    variant: Literal["standard", "detailed", "concise", "high_level"] = "standard"
    scenario: Literal["update", "follow_up"] = "update"
    use_when: str = ""
    review_notes: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class WritingObservation(Record):
    """Attributed, structural observations; never message bodies or case facts."""
    audience: Literal["customer", "engineering", "internal"]
    author_email: str
    source_message_ids: list[str] = Field(min_length=1, max_length=30)
    greeting: Literal["hi", "hello", "dear"] | None = None
    good_day: bool = False
    closing: Literal["best regards", "kind regards", "regards", "thanks", "thank you"] | None = None
    structure: list[Literal["numbered_issues", "observations_recommendations", "direct_reference", "short_specific_ask"]] = Field(default_factory=list)
    acknowledges_early: bool = False


class Analysis(Record):
    schema_version: Literal[1] = 1
    case_id: str = Field(pattern=r"^[a-zA-Z0-9]{18}$")
    run_id: str
    source_revision: str
    summary: str = Field(description="A concise operational case brief: issue, current status, latest meaningful update, blocker and next step. Put tool execution details in coverage or gaps.")
    concerns: list[Concern] = Field(default_factory=list)
    next_actions: list[ProposedAction] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    drafts: list[DraftInput] = Field(default_factory=list)
    coverage: list[Coverage] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    writing_observations: list[WritingObservation] = Field(default_factory=list)

    @field_validator("next_actions", mode="before")
    @classmethod
    def retain_ambiguous_timing_as_reason(cls, actions):
        # A model's date-only or relative proposal is not an agreed deadline.
        # Retain its wording without inventing a timezone or dropping the report.
        normalized = []
        for item in actions:
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            item = dict(item)
            if item.get("due_at"):
                try:
                    timestamp(item["due_at"])
                except (ValueError, TypeError):
                    item["reason"] = item.get("reason", "") + " Unconfirmed timing proposed by worker: " + str(item["due_at"])
                    item["due_at"] = None
            normalized.append(item)
        return normalized


class DraftPatch(Record):
    body: str | None = Field(default=None, max_length=100000)
    state: Literal["draft", "reviewed", "copied", "manually_sent", "submitted_manually"] | None = None
    learn_style: bool = False


class WritingPreferences(Record):
    learning_enabled: bool = True
    preferences: dict[Literal["customer", "engineering", "internal"], str] = Field(default_factory=dict)

    @field_validator("preferences")
    @classmethod
    def bounded_preferences(cls, value):
        if any(len(text) > 5000 for text in value.values()):
            raise ValueError("Writing preferences must be at most 5000 characters per audience")
        return value


class WritingExample(Record):
    audience: Literal["customer", "engineering", "internal"]
    body: str = Field(min_length=40, max_length=30000)
    source_label: str = Field(default="My sent message", min_length=1, max_length=300)


class ActionInput(Record):
    title: str = Field(min_length=1, max_length=2000)
    owner: str = Field(default="Me", max_length=300)
    due_at: str | None = None
    note: str = Field(default="", max_length=20000)

    @field_validator("due_at")
    @classmethod
    def valid_due(cls, value):
        return timestamp(value)


class LessonInput(Record):
    title: str = Field(min_length=1, max_length=2000)
    lesson: str = Field(min_length=1, max_length=20000)
    supporting_cases: list[str] = Field(default_factory=list)
    limitations: str = Field(default="", max_length=20000)
