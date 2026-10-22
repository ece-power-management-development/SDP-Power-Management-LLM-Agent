# AI Agent contract

The gateway exposes a read-only, typed context packet at:

```text
GET http://127.0.0.1:8766/v1/context
```

The packet separates:

- `observations`: values received through HELICS subscriptions, including units, source interface, simulation time, data quality, and evidence IDs;
- `events`: messages received through HELICS endpoints, including source, destination, simulation time, and decoded JSON/text payload;
- `quality`: stale, missing, and conflicting data counts;
- `unknowns`: information that is not available or is not safe to infer;
- `decision_boundary`: explicit read-only and anti-inference rules.

## Recommended agent loop

1. Call `/v1/health`.
2. Call `/v1/context`.
3. Stop with `insufficient_evidence` if the gateway is not connected or the packet quality is degraded for the requested conclusion.
4. Use only values whose `quality` is `fresh` or explicitly explain held values.
5. Keep `source_kind` labels separate: `observed`, `simulated`, `inferred`, and `recommended` are not interchangeable.
6. Cite `evidence_id` values in every claim.
7. Send proposed claims to `/v1/agent/validate` before presenting them to a human.
8. Present recommendations as advisory options requiring operator approval. Do not issue control commands through this gateway.

Example validation request:

```json
{
  "claims": [
    {
      "text": "Line N14 is above its configured loading threshold.",
      "evidence_ids": ["obs:GridDyn/line_N14/loading"]
    }
  ]
}
```

The gateway does not create a failure probability merely because a signal is high. A calibrated risk model and an independently validated GridDyn/optimization run must supply that conclusion.
