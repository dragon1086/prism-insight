"""Append-only proposal, outcome, and feedback storage contracts."""

from prism_core.feedback.outcomes import (
    OutcomeRecord,
    OutcomeRepository,
    OutcomeState,
    StoredOutcome,
)
from prism_core.feedback.repository import (
    AppendDisposition,
    DecisionSnapshotRecord,
    FeedbackRepository,
    FeedbackRunRecord,
    LessonCandidateRecord,
    LessonEvidenceRecord,
    ProposalRecord,
    RetrospectiveRecord,
    StoredProposal,
    canonical_json,
)

__all__ = [
    "AppendDisposition",
    "DecisionSnapshotRecord",
    "FeedbackRepository",
    "FeedbackRunRecord",
    "LessonCandidateRecord",
    "LessonEvidenceRecord",
    "OutcomeRecord",
    "OutcomeRepository",
    "OutcomeState",
    "ProposalRecord",
    "RetrospectiveRecord",
    "StoredOutcome",
    "StoredProposal",
    "canonical_json",
]
