"""Internal multi-agent contracts. The HTTP response schema stays in app.schemas."""

from app.agents.state import (
    ClarificationRecord,
    DraftAnswer,
    EvidenceDecision,
    ResearchRequest,
    RetrievalResult,
    Stage,
    StageStatus,
    TicketRunState,
    WorkflowStatus,
    clarification_stage,
    draft_stage,
    evidence_stage,
    research_stage,
)

__all__ = [
    "ClarificationRecord",
    "DraftAnswer",
    "EvidenceDecision",
    "ResearchRequest",
    "RetrievalResult",
    "Stage",
    "StageStatus",
    "TicketRunState",
    "WorkflowStatus",
    "clarification_stage",
    "draft_stage",
    "evidence_stage",
    "research_stage",
]
