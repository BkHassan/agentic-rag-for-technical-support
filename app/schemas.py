"""Pydantic schemas for support-ticket input and agent responses."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TicketCategory(str, Enum):
    """Classification categories used by the technical support agent."""

    NETWORK = "network"
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    APPLICATION = "application"
    OTHER = "other"


class SupportTicket(BaseModel):
    """Incoming support ticket for the agent to classify and troubleshoot."""

    model_config = ConfigDict(str_strip_whitespace=True)

    ticket_id: str = Field(..., min_length=1, description="Unique ticket identifier")
    description: str = Field(
        ...,
        min_length=1,
        description="Free-text description of the user's technical issue",
    )

    @field_validator("ticket_id", "description")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class TicketClassification(BaseModel):
    """Result of classifying a support ticket (independent of retrieval)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: TicketCategory = Field(..., description="Predicted technical category")
    rationale: str = Field(
        ...,
        min_length=1,
        description="Brief explanation of why this category was chosen",
    )


class DocumentSource(BaseModel):
    """A retrieved knowledge-base chunk cited in the agent response."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str = Field(..., description="Original document filename")
    doc_id: str | None = Field(default=None, description="Stable document identifier")
    chunk_index: int | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, description="Document category metadata")
    distance: float | None = Field(
        default=None,
        description="Chroma cosine distance (lower is closer / more relevant)",
    )
    excerpt: str | None = Field(
        default=None,
        description="Optional short preview of the retrieved chunk text",
    )


class GroundedAdvice(BaseModel):
    """LLM draft of evidence-grounded advice (assembled into AgentResponse by the host)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    recommendations: list[str] = Field(
        default_factory=list,
        description=(
            "Troubleshooting steps supported by the retrieved documentation. "
            "Each item should be a concrete action grounded in evidence text."
        ),
    )
    cited_sources: list[str] = Field(
        default_factory=list,
        description="Filenames from the evidence that actually support the recommendations",
    )
    summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Short explanation that separates documented guidance from uncertainty. "
            "Do not present unsupported claims as facts."
        ),
    )


class AgentResponse(BaseModel):
    """Structured agent output for a support ticket."""

    model_config = ConfigDict(str_strip_whitespace=True)

    ticket_id: str = Field(..., min_length=1)
    category: TicketCategory = Field(
        ...,
        description="Classified ticket category",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Ordered troubleshooting recommendations",
    )
    sources: list[DocumentSource] = Field(
        default_factory=list,
        description="Supporting document sources used as evidence",
    )
    sufficient_evidence: bool = Field(
        ...,
        description="True when retrieval returned relevant evidence for the ticket",
    )
    fallback_triggered: bool = Field(
        ...,
        description="True when the safe fallback path was used due to weak or missing evidence",
    )
    summary: str | None = Field(
        default=None,
        description="Optional short explanation of the diagnosis or fallback reason",
    )

    @field_validator("recommendations")
    @classmethod
    def recommendations_must_be_non_empty_strings(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        return cleaned
