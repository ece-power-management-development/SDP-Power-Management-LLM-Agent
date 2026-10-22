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

Open `http://127.0.0.1:8765/review` for the interactive AI Agent output review console. It loads live or demo context, accepts a structured agent response, runs an evidence and safety gate, renders claims and possible solutions in human-readable form, and records operator feedback.

## AI Agent output review

The review console checks the AI Agent response before it is presented for decision use. A response must include:

- evidence IDs for the summary, every claim, and every possible solution;
- source labels that keep observed, simulated, inferred, and recommended content separate;
- explicit unknowns and limitations;
- risks and constraints to check for each possible solution;
- `approval_required: true` for every possible solution.

Missing or stale evidence, conflicting context, direct command fields, and unapproved solutions are held by the validator. A human can select `request_recheck`, describe the issue, and produce a structured request at `GET /api/agent/recheck`. The AI Agent retrieves that request, corrects its response, and submits a new packet.

Agent review endpoints:

```text
POST /api/agent/submit       # context + structured agent output -> validation gate
GET  /api/agent/review-state # checked output, validation, history, and recheck request
GET  /api/agent/recheck      # latest operator feedback for the agent
POST /api/agent/review       # accept, request_recheck, or reject
GET  /api/agent/output-schema
```

The agent-facing contract is in `AGENT_REVIEW_CONTRACT.md`, the response schema is `agent_output.schema.json`, and the tool manifest is `agent_review_tools.json`.

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

## Install and run the local AI Agent

The package includes a local Ollama/PydanticAI runner. The installer downloads dependencies and the selected model once; after installation, inference uses the local Ollama server and does not need Internet access.

Windows PowerShell:

```powershell
.\install_local_agent.ps1
.\run_local_agent.ps1 -Demo
```

For a real HELICS federation:

```powershell
.\run_local_agent.ps1 -LiveHelics
```

Linux or macOS:

```bash
./install_local_agent.sh
./run_local_agent.sh --demo
```

The default model is `qwen3:8b`. Use `-Model qwen3:4b` on Windows or `MODEL=qwen3:4b` on Linux/macOS if local memory is limited. To verify an already prepared offline computer without downloading anything, use `-Offline` in PowerShell or `OFFLINE=1` in the shell installer. Offline mode requires the Python wheels, Ollama, and model to already be present.

The agent runner reads the HELICS packet, produces schema-constrained `agent_recommendation` JSON, submits it to the review gate, and incorporates any operator recheck request on the next cycle. It has no grid actuation path.

## Connect GridDyn

The interface uses the first available option:

1. Python package: `import griddyn` and `griddyn.load(case_path)`.
2. Command line: `gridDynMain case_path` with `gridDynMain` available on `PATH`.

Because GridDyn case formats and result fields may vary by build, the adapter intentionally does not guess voltage, flow, frequency, or failure metrics from arbitrary output. Add a case-specific result mapper in `app.py` before using numerical results for operational decisions.

## Safety boundary

This prototype does not issue breaker, generator, storage, demand-response, or load-control commands. Recommendations are advisory only. Use a validated optimizer, independent physics/constraint checks, and human operator approval before connecting it to an operational workflow.
