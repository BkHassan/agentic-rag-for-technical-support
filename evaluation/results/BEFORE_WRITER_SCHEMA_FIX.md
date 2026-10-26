# Evaluation results

- Generated at: `2026-10-26T20:41:50.654840+00:00`
- Dataset: `evaluation/support_tickets.json` (version 1.1)
- Model: `gemini-3.5-flash-lite`; embeddings: `gemini-embedding-001`
- Architecture: coordinator: triage -> researcher (search tool) -> evidence gate -> writer
- Code: `915386d` (uncommitted changes)
- Sufficiency rule: best distance <= 0.3687 and contrast@5 >= 0.0442 (calibrated separately on `evaluation/calibration_tickets.json`)

**Run complete.** Passed **10/20** selected tickets (50%).

| Status | Tickets |
|---|---:|
| passed | 10 |
| failed | 1 |
| infrastructure_error | 9 |
| not_executed | 0 |

Only `passed` counts as a pass. Infrastructure errors and unexecuted tickets are not scored and are not excluded from the headline rate. These rates describe this fixed set only.

## Behaviors (scored tickets only)

| Behavior | Result |
|---|---:|
| Final outcome | 10/11 (91%) |
| Tool selection | 10/11 (91%) |
| Citation correctness (grounded tickets) | 0/0 (n/a) |
| Evidence gate decision (diagnostic, retrieval ran) | 5/5 (100%) |
| Grounded answer when expected | 0/1 (0%) |
| Clarification when expected | 3/3 (100%) |
| Fallback when expected | 7/7 (100%) |

Citation correctness means every cited document was in the accepted evidence and at least one is an expected supporting document. It does not measure whether each recommendation is faithful to the passage; passing the evidence gate only means the passages are close to the ticket.

## Outcomes (rows: expected, columns: returned)

| Expected | grounded_answer | clarification_needed | fallback |
|---|---:|---:|---:|
| grounded_answer | 0 | 1 | 0 |
| clarification_needed | 0 | 3 | 0 |
| fallback | 0 | 0 | 7 |

## Known regressions

- `E-REL-004`: failed (expected grounded_answer, returned clarification_needed)

## Cost and operations

- Infrastructure errors: 9 of 20 executed (45%)
- Latency ms: avg 6630, p50 7492, p95 9667, max 10598 (n=20)
- Avg per ticket: Gemini calls incl. retries 2.15, retrieval executions 0.70, embedding requests 0.70
- Tokens (Gemini usage_metadata, 34/34 responses): prompt 13544, output 2028, thinking 0, total 15572 (avg 779/ticket)
- Estimated cost: Not estimated: pass --input-usd-per-mtok and --output-usd-per-mtok to estimate.

## Not passed

- `E-REL-001` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-002` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-003` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-004` (relevant_documentation) **failed**: tool_selection: Failed to request retrieval when documentation was needed; outcome: Expected grounded_answer but returned clarification_needed
- `E-REL-005` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-006` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-007` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-008` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-009` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.
- `E-REL-010` (relevant_documentation) **infrastructure_error**: error: The language model is temporarily unavailable.

## Comparison with baseline

Baseline `evaluation/results/before_specificity_check.json` (gemini-3.5-flash-lite, 2026-10-25T15:56:27) vs this run (gemini-3.5-flash-lite), on 20 tickets both runs executed with identical text. Both sides are scored with the current scorer.

| Metric | Baseline | Multi-agent |
|---|---:|---:|
| passed | 19 | 10 |
| failed | 1 | 1 |
| infrastructure_error | 0 | 9 |
| Final outcome | 19/20 (95%) | 10/11 (91%) |
| Tool selection | 19/20 (95%) | 10/11 (91%) |
| Citation correctness | 9/9 (100%) | 0/0 (n/a) |
| Evidence gate | 14/14 (100%) | 5/5 (100%) |
| Avg Gemini calls | n/a | 2.15 |
| Avg latency ms | 11238 | 6630 |
| p95 latency ms | 30859 | 9667 |

Changed tickets:
- `E-REL-001`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-002`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-003`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-005`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-006`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-007`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-008`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-009`: passed (grounded_answer) -> infrastructure_error (None)
- `E-REL-010`: passed (grounded_answer) -> infrastructure_error (None)

## Per-ticket results

| Ticket | Group | Status | Tool | Citations | Gate | Outcome (expected -> returned) | Calls | ms |
|---|---|---|:---:|:---:|:---:|---|---:|---:|
| E-REL-001 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 9667 |
| E-REL-002 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 9434 |
| E-REL-003 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 8286 |
| E-REL-004 | relevant_documentation | failed | fail | skip | skip | grounded_answer -> clarification_needed | 1 | 2928 |
| E-REL-005 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 8117 |
| E-REL-006 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 8272 |
| E-REL-007 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 8162 |
| E-REL-008 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 8394 |
| E-REL-009 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 8518 |
| E-REL-010 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 10598 |
| E-IRR-001 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 8539 |
| E-IRR-002 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 7492 |
| E-IRR-003 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 6582 |
| E-IRR-004 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 6929 |
| E-IRR-005 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 7220 |
| E-AMB-001 | ambiguous_or_underspecified | passed | pass | skip | skip | clarification_needed -> clarification_needed | 1 | 2593 |
| E-AMB-002 | ambiguous_or_underspecified | passed | pass | skip | skip | clarification_needed -> clarification_needed | 1 | 2734 |
| E-AMB-003 | ambiguous_or_underspecified | passed | pass | skip | skip | clarification_needed -> clarification_needed | 1 | 2731 |
| E-NRT-001 | should_not_require_retrieval | passed | pass | skip | skip | fallback -> fallback | 1 | 2978 |
| E-NRT-002 | should_not_require_retrieval | passed | pass | skip | skip | fallback -> fallback | 1 | 2431 |
