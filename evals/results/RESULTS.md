# Evaluation results

Fixed dataset: [`evals/support_tickets.json`](../support_tickets.json) (20 tickets).

## How to reproduce

1. Set `GOOGLE_API_KEY` in `.env` (never commit the real key).
2. From the project root:

```bash
python -m evals.run_evaluation
```

Optional:

```bash
python -m evals.run_evaluation --force-reingest
python -m evals.run_evaluation --ticket-id E-REL-001
python -m evals.run_evaluation --limit 5
```

Artifacts:

| File | Purpose |
|---|---|
| `latest.json` | Full machine-readable report (gitignored) |
| `latest.md` | Human summary of the last run (gitignored) |
| `RESULTS.md` | Documented snapshot (this file; overwritten on full-suite runs) |

Offline scoring tests (no API key):

```bash
pytest tests/test_eval_scoring.py -q
```

## Behaviors scored

| Behavior | Pass condition |
|---|---|
| Tool selection | Gemini requests retrieval iff `retrieval_appropriate` is true |
| Answer grounding | For non-fallback tickets, citations overlap expected docs and come from retrieved evidence |
| Fallback | Fallback when evidence is insufficient; grounded recommendations when evidence is adequate |

## Live run status

**Not executed in the publish environment** — no `GOOGLE_API_KEY` / Chroma index was available when documenting and publishing.

After a local full run, replace this section with the generated summary table, list failing ticket IDs (if any), and note fixes.

### Placeholder summary table

| Behavior | Passed | Total | Rate |
|---|---:|---:|---:|
| Tool selection | — | 20 | pending live run |
| Answer grounding | — | applicable non-fallback tickets | pending live run |
| Fallback behavior | — | 20 | pending live run |

## Limits of this evaluation

A successful run on this 20-ticket set demonstrates the intended **tool selection**, **grounding**, and **fallback** behaviors **on those cases**.

It does **not**:

- establish production-level reliability or SLA readiness
- prove generalization beyond the curated docs and tickets
- remove Gemini non-determinism, rate limits, or threshold sensitivity
- replace broader QA, security review, or human escalation processes

Treat published numbers (when present) as evidence of mechanism on a frozen eval set, not as a production certification.
