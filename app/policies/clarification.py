"""Application-owned decision for when a ticket needs clarification.

Gemini extracts phrases from the ticket (`TicketCompleteness`); this module decides.
A troubleshooting ticket needs clarification when it does not describe an observable
symptom, because there is nothing specific to troubleshoot. A missing system alone
does not trigger it: a specific symptom ("returns 503") usually identifies the area
by itself. When clarification is needed, a missing system is asked about too.
Unlike the documentation fallback, the gap is in the ticket, so the reporter can
close it and search waits until they do.

Extracted phrases are only trusted when every word appears in the ticket and at
least one word is specific, so a paraphrased or generic extraction ("the
application", "something is wrong") counts as missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas import TicketCompleteness

CLARIFICATION_MESSAGE = (
    "More detail is needed before documented troubleshooting steps can be "
    "recommended. Please answer the questions below."
)

QUESTION_AFFECTED_SYSTEM = (
    "Which system or feature is affected (for example the website, sign-in, VPN, "
    "file uploads, or a specific database)?"
)
QUESTION_OBSERVABLE_SYMPTOM = (
    "What exactly happens? Please include the exact error message or code, or "
    "describe what you see instead of the expected result."
)
QUESTION_SCOPE = "When did it start, and is it affecting one user, a team, or everyone?"

_STOPWORDS = {
    "a", "about", "after", "afternoon", "all", "an", "and", "any", "anymore", "are", "as",
    "asap", "at", "be", "been", "but", "by", "can", "can't", "cannot", "could", "do",
    "does", "doesn't", "don't", "for", "from", "has", "have", "i", "i'm", "in", "is",
    "isn't", "it's", "maybe", "me", "morning", "my", "not", "now", "of", "on", "or", "our",
    "please", "since", "so", "some", "that", "the", "their", "them", "there", "they",
    "this", "to", "today", "up", "urgent", "urgently", "was", "we", "we're", "week", "were",
    "when", "with", "won't", "yesterday", "you", "your",
}

# Words that name no specific system or failure on their own.
_GENERIC = {
    "access", "acting", "app", "application", "apps", "behavior", "behaviour", "broke",
    "broken", "bug", "complain", "complaining", "complaints", "error", "errors",
    "everything", "fail", "failed", "failing", "fails", "failure", "help", "issue",
    "issues", "it", "nothing", "odd", "problem", "problems", "seeing", "server", "servers",
    "service", "slow", "something", "stopped", "strange", "stuff", "system", "systems",
    "team", "thing", "things", "user", "users", "weird", "work", "working", "works", "wrong",
}

_TOKEN = re.compile(r"[a-z0-9_.'\-]+")


@dataclass(frozen=True)
class ClarificationDecision:
    needed: bool
    missing: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    reason: str = ""
    # Specific ticket words outside the extracted system name. Empty means the ticket
    # says nothing beyond naming a system, so no symptom can have been missed.
    unexplained_detail: list[str] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    text = text.lower().replace("\u2019", "'")
    return [token.strip(".-'") for token in _TOKEN.findall(text) if token.strip(".-'")]


def _specific_words(phrase: str | None, ticket_text: str, exclude: set[str] | None = None) -> set[str]:
    """Specific words of an extracted phrase, or nothing if the phrase is not from the ticket."""
    if not phrase:
        return set()
    words = _tokens(phrase)
    if not words or not set(words) <= set(_tokens(ticket_text)):
        return set()
    return {word for word in words if word not in _STOPWORDS | _GENERIC | (exclude or set())}


def decide_clarification(
    description: str,
    completeness: TicketCompleteness | None,
) -> ClarificationDecision:
    """Decide whether to ask the reporter for detail before searching documentation."""
    if completeness is None:
        return ClarificationDecision(needed=False, reason="Completeness was not assessed.")
    if not completeness.is_troubleshooting_request:
        return ClarificationDecision(needed=False, reason="Not a troubleshooting request.")

    system_words = _specific_words(completeness.affected_system, description)
    # A symptom must say what fails, not just repeat the system name.
    symptom_words = _specific_words(
        completeness.observable_symptom, description, exclude=system_words
    )

    if symptom_words:
        reason = "Ticket describes an observable symptom."
        if not system_words:
            reason += " The affected system is not named, but the symptom is specific."
        return ClarificationDecision(needed=False, reason=reason)

    missing: list[str] = []
    questions: list[str] = []
    if not system_words:
        missing.append("affected_system")
        questions.append(QUESTION_AFFECTED_SYSTEM)
    missing.append("observable_symptom")
    questions.append(QUESTION_OBSERVABLE_SYMPTOM)

    questions.append(QUESTION_SCOPE)
    ticket_words = set(_tokens(description)) - _STOPWORDS - _GENERIC - system_words
    return ClarificationDecision(
        needed=True,
        missing=missing,
        questions=questions,
        reason="Ticket does not name: " + ", ".join(name.replace("_", " ") for name in missing) + ".",
        unexplained_detail=sorted(ticket_words),
    )
