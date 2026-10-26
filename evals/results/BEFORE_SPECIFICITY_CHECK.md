# Evaluation results

- Generated at: `2026-10-25T15:56:27.216670+00:00`
- Dataset: `evals/support_tickets.json`
- Model: `gemini-3.5-flash-lite`
- Sufficiency rule: best distance <= 0.3687 and contrast@5 >= 0.0442 (calibrated in `evals/results/CALIBRATION.md`)
- Tickets scored: **19/20** (95%)
- API errors excluded from the scores: 0
- Stopped early for daily quota: False

These rates describe this fixed set only. They are not a production reliability claim.

## Core behaviors

| Behavior | Passed | Total | Rate |
|---|---:|---:|---:|
| Tool selection | 19 | 20 | 95% |
| Answer grounding | 9 | 9 | 100% |
| Fallback / clarification outcome | 19 | 20 | 95% |

## Outcomes (rows: expected, columns: returned)

| Expected | grounded_answer | clarification_needed | fallback |
|---|---:|---:|---:|
| grounded_answer | 9 | 1 | 0 |
| clarification_needed | 0 | 3 | 0 |
| fallback | 0 | 0 | 7 |

## Operational notes

- API errors: 0
- Avg latency: 11238 ms
- Max latency: 46162 ms

## Failures

- `E-REL-004` (relevant_documentation): tool_selection, outcome
  - tool_selection: Failed to request retrieval when documentation was needed
  - outcome: Expected grounded_answer but returned clarification_needed

## Per-ticket results

| Ticket | Group | Pass | Tool | Grounding | Outcome (expected -> returned) | Latency ms |
|---|---|:---:|:---:|:---:|---|---:|
| E-REL-001 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 10777 |
| E-REL-002 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 9871 |
| E-REL-003 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 10291 |
| E-REL-004 | relevant_documentation | fail | fail | skip | fail grounded_answer -> clarification_needed | 2354 |
| E-REL-005 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 46162 |
| E-REL-006 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 10784 |
| E-REL-007 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 11631 |
| E-REL-008 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 10866 |
| E-REL-009 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 30859 |
| E-REL-010 | relevant_documentation | pass | pass | pass | pass grounded_answer -> grounded_answer | 10290 |
| E-IRR-001 | insufficient_or_irrelevant_evidence | pass | pass | skip | pass fallback -> fallback | 11528 |
| E-IRR-002 | insufficient_or_irrelevant_evidence | pass | pass | skip | pass fallback -> fallback | 11888 |
| E-IRR-003 | insufficient_or_irrelevant_evidence | pass | pass | skip | pass fallback -> fallback | 12044 |
| E-IRR-004 | insufficient_or_irrelevant_evidence | pass | pass | skip | pass fallback -> fallback | 8650 |
| E-IRR-005 | insufficient_or_irrelevant_evidence | pass | pass | skip | pass fallback -> fallback | 12835 |
| E-AMB-001 | ambiguous_or_underspecified | pass | pass | skip | pass clarification_needed -> clarification_needed | 2514 |
| E-AMB-002 | ambiguous_or_underspecified | pass | pass | skip | pass clarification_needed -> clarification_needed | 2228 |
| E-AMB-003 | ambiguous_or_underspecified | pass | pass | skip | pass clarification_needed -> clarification_needed | 2369 |
| E-NRT-001 | should_not_require_retrieval | pass | pass | skip | pass fallback -> fallback | 3763 |
| E-NRT-002 | should_not_require_retrieval | pass | pass | skip | pass fallback -> fallback | 3064 |
