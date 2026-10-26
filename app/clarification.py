"""Compatibility wrapper. The clarification policy lives in app.policies."""

from app.policies.clarification import (
    CLARIFICATION_MESSAGE,
    QUESTION_AFFECTED_SYSTEM,
    QUESTION_OBSERVABLE_SYMPTOM,
    QUESTION_SCOPE,
    ClarificationDecision,
    decide_clarification,
)

__all__ = [
    "CLARIFICATION_MESSAGE",
    "ClarificationDecision",
    "decide_clarification",
]
