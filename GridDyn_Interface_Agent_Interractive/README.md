# GridDyn Interface

This is a local, advisory-only interface for connecting a computer and an AI Agent to GridDyn. It provides:

- automatic detection of the GridDyn Python package, `gridDynMain`, and HELICS;
- GridDyn case upload or local-path selection;
- Python-package and command-line execution adapters;
- preserved raw output and verification notes;
- synthetic demo mode when GridDyn is not installed;
- human-readable Markdown and JSON analysis records in `reports/`;
- a read-only HELICS combination-federate gateway for an AI Agent;
- typed context packets with units, simulation time, provenance, quality, evidence IDs, unknowns, and bounded event history;
- an agent tool manifest and claim-evidence validator.

## Run it

From this directory:

```bash
python app.py
```

Open `http://127.0.0.1:8765` in a browser. The server binds to localhost by default.

## Run the AI Agent gateway

The gateway is a separate local service. It reads HELICS values and endpoint messages, then exposes normalized JSON for an AI Agent. It never sends controls to GridDyn or to a HELICS endpoint.

To exercise the interface without GridDyn or HELICS:

```bash
python agent_gateway.py --demo
```

The demo gateway listens on `http://127.0.0.1:8766`. Useful endpoints are:

```text
GET  /v1/health
GET  /v1/context
GET  /v1/events
GET  /v1/tools
GET  /v1/schema
POST /v1/agent/validate
POST /v1/command/preview   # always rejected; safety check
```

For a real HELICS federation, install PyHELICS, start the broker used by the federation, review the signal names and units in `helics_agent_config.json`, then run:

```bash
python agent_gateway.py --config helics_agent_config.json
```

The template subscriptions are examples. Change their `key`/`target` values and semantic tags to match the actual GridDyn publications. GridDyn event messages should be routed to the configured `GridDynAgentGateway/events` endpoint if event context is required.

The AI Agent should follow `AGENT_CONTRACT.md`, use `agent_tools.json` as its tool manifest, and cite `evidence_id` values in every claim. Missing, stale, held, conflicting, or synthetic data must remain explicitly labeled; the gateway does not estimate a failure probability or invent an operating limit.

## Connect GridDyn

The interface uses the first available option:

1. Python package: `import griddyn` and `griddyn.load(case_path)`.
2. Command line: `gridDynMain case_path` with `gridDynMain` available on `PATH`.

Because GridDyn case formats and result fields may vary by build, the adapter intentionally does not guess voltage, flow, frequency, or failure metrics from arbitrary output. Add a case-specific result mapper in `app.py` before using numerical results for operational decisions.

## Safety boundary

This prototype does not issue breaker, generator, storage, demand-response, or load-control commands. Recommendations are advisory only. Use a validated optimizer, independent physics/constraint checks, and human operator approval before connecting it to an operational workflow.
