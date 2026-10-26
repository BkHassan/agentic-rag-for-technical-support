# Evaluation results

- Generated at: `2026-10-26T20:58:28.625920+00:00`
- Dataset: `evaluation/support_tickets.json` (version 1.1)
- Model: `gemini-3.5-flash-lite`; embeddings: `gemini-embedding-001`
- Architecture: coordinator: triage -> researcher (search tool) -> evidence gate -> writer
- Code: `915386d` (uncommitted changes)
- Sufficiency rule: best distance <= 0.3687 and contrast@5 >= 0.0442 (calibrated separately on `evaluation/calibration_tickets.json`)

**Run complete.** Passed **18/20** selected tickets (90%).

| Status | Tickets |
|---|---:|
| passed | 18 |
| failed | 1 |
| infrastructure_error | 1 |
| not_executed | 0 |

Only `passed` counts as a pass. Infrastructure errors and unexecuted tickets are not scored and are not excluded from the headline rate. These rates describe this fixed set only.

## Behaviors (scored tickets only)

| Behavior | Result |
|---|---:|
| Final outcome | 18/19 (95%) |
| Tool selection | 19/19 (100%) |
| Citation correctness (grounded tickets) | 8/8 (100%) |
| Evidence gate decision (diagnostic, retrieval ran) | 14/14 (100%) |
| Grounded answer when expected | 8/9 (89%) |
| Clarification when expected | 3/3 (100%) |
| Fallback when expected | 7/7 (100%) |

Citation correctness means every cited document was in the accepted evidence and at least one is an expected supporting document. It does not measure whether each recommendation is faithful to the passage; passing the evidence gate only means the passages are close to the ticket.

## Outcomes (rows: expected, columns: returned)

| Expected | grounded_answer | clarification_needed | fallback |
|---|---:|---:|---:|
| grounded_answer | 8 | 0 | 1 |
| clarification_needed | 0 | 3 | 0 |
| fallback | 0 | 0 | 7 |

## Known regressions

- `E-REL-004`: passed (expected grounded_answer, returned grounded_answer)

## Cost and operations

- Infrastructure errors: 1 of 20 executed (5%)
- Latency ms: avg 8094, p50 3208, p95 48695, max 65961 (n=20)
- Avg per ticket: Gemini calls incl. retries 2.30, retrieval executions 0.70, embedding requests 0.70
- Tokens (Gemini usage_metadata, 43/43 responses): prompt 22143, output 5697, thinking 0, total 27840 (avg 1392/ticket)
- Estimated cost: Not estimated: pass --input-usd-per-mtok and --output-usd-per-mtok to estimate.

## Not passed

- `E-REL-005` (relevant_documentation) **failed**: outcome: Expected grounded_answer but returned fallback
- `E-REL-006` (relevant_documentation) **infrastructure_error**: error: The analysis timed out.

## Comparison with baseline

Baseline `evaluation/results/perf_baseline.json` (gemini-3.5-flash-lite, 2026-10-26T20:48:45) vs this run (gemini-3.5-flash-lite), on 20 tickets both runs executed with identical text. Both sides are scored with the current scorer.

| Metric | Baseline | Multi-agent |
|---|---:|---:|
| passed | 18 | 18 |
| failed | 2 | 1 |
| infrastructure_error | 0 | 1 |
| Final outcome | 18/20 (90%) | 18/19 (95%) |
| Tool selection | 19/20 (95%) | 19/19 (100%) |
| Citation correctness | 8/8 (100%) | 8/8 (100%) |
| Evidence gate | 14/14 (100%) | 14/14 (100%) |
| Avg Gemini calls | 2.20 | 2.30 |
| Avg latency ms | 10609 | 8094 |
| p95 latency ms | 21057 | 48695 |

Changed tickets:
- `E-REL-004`: failed (clarification_needed) -> passed (grounded_answer)
- `E-REL-006`: passed (grounded_answer) -> infrastructure_error (None)

## Per-ticket results

| Ticket | Group | Status | Tool | Citations | Gate | Outcome (expected -> returned) | Calls | ms |
|---|---|---|:---:|:---:|:---:|---|---:|---:|
| E-REL-001 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 7529 |
| E-REL-002 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 3283 |
| E-REL-003 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 3260 |
| E-REL-004 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 3626 |
| E-REL-005 | relevant_documentation | failed | pass | skip | pass | grounded_answer -> fallback | 3 | 3208 |
| E-REL-006 | relevant_documentation | infrastructure_error | n/a | n/a | n/a | grounded_answer -> none | 3 | 65961 |
| E-REL-007 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 3858 |
| E-REL-008 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 3866 |
| E-REL-009 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 3810 |
| E-REL-010 | relevant_documentation | passed | pass | pass | pass | grounded_answer -> grounded_answer | 3 | 3332 |
| E-IRR-001 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 1956 |
| E-IRR-002 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 3 | 48695 |
| E-IRR-003 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 1856 |
| E-IRR-004 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 1679 |
| E-IRR-005 | insufficient_or_irrelevant_evidence | passed | pass | skip | pass | fallback -> fallback | 2 | 1847 |
| E-AMB-001 | ambiguous_or_underspecified | passed | pass | skip | skip | clarification_needed -> clarification_needed | 1 | 784 |
| E-AMB-002 | ambiguous_or_underspecified | passed | pass | skip | skip | clarification_needed -> clarification_needed | 1 | 832 |
| E-AMB-003 | ambiguous_or_underspecified | passed | pass | skip | skip | clarification_needed -> clarification_needed | 1 | 811 |
| E-NRT-001 | should_not_require_retrieval | passed | pass | skip | skip | fallback -> fallback | 1 | 747 |
| E-NRT-002 | should_not_require_retrieval | passed | pass | skip | skip | fallback -> fallback | 1 | 949 |
