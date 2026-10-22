# AI Agent output and human review contract

The review console is available at:

```text
http://127.0.0.1:8765/review
```

The AI Agent submits two objects to `POST /api/agent/submit`:

```json
{
  "context": {"packet_type": "grid_state_context"},
  "output": {"output_type": "agent_recommendation"}
}
```

The `context` must be the exact GridDyn/HELICS packet used for reasoning. The `output` must contain:

- `summary` and `summary_evidence_ids`;
- structured `claims`, each with `text`, `evidence_ids`, and a source kind of `observed`, `simulated`, or `inferred`;
- `possible_solutions`, each with evidence, rationale, expected effect, risks, constraints to check, `source_kind: recommended`, and `approval_required: true`;
- explicit `unknowns` and limitations.

The server checks evidence IDs against the submitted context. Missing, stale, held, invalid, or conflicting evidence is surfaced before the response is made available for operator decision use. Demo data is always marked for additional review. Command-like fields such as `control_request`, `target_setpoint`, and `execute_now` are blocked.

## Agent loop

1. Read `/v1/context` from the HELICS Agent Gateway.
2. Produce a structured `agent_recommendation` without inventing limits, ratings, failure probabilities, or topology facts.
3. Submit the context and response to `/api/agent/submit`.
4. Read the returned validation gate. Treat `blocked` as a correction state; treat `needs_review` as conditional and explicitly limited.
5. If the operator requests a recheck, retrieve `/api/agent/recheck`.
6. Re-read the current context, correct the specific issues, and submit a new response with new evidence checks.
7. Keep every solution advisory. This service has no grid actuation path.

## Human review payload

```json
{
  "decision": "request_recheck",
  "issue_types": ["unsupported_claim", "missing_context"],
  "feedback": "The line-loading value is present, but no equipment rating is included. Recheck the claim and state the missing limit.",
  "selected_solution_ids": ["solution-line-validation"]
}
```

The generated recheck request is available through `GET /api/agent/recheck`.
