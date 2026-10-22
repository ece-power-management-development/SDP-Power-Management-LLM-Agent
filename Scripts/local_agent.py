#!/usr/bin/env python3
"""Offline GridDyn/HELICS AI Agent runner.

The local model proposes a structured, advisory response from the exact
context packet it receives. The response is submitted to the review service;
it is never shown as trusted output and never sends a grid control command.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
DEFAULT_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
DEFAULT_CONTEXT_URL = os.environ.get("GRID_CONTEXT_URL", "http://127.0.0.1:8766/v1/context")
DEFAULT_REVIEW_URL = os.environ.get("REVIEW_SUBMIT_URL", "http://127.0.0.1:8765/api/agent/submit")
DEFAULT_RECHECK_URL = os.environ.get("RECHECK_URL", "http://127.0.0.1:8765/api/agent/recheck")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_json(url: str, *, method: str = "GET", payload: Any = None, timeout: float = 10.0) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - local endpoints are configured by the operator
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def require_local_url(url: str, label: str) -> None:
    host = urlparse(url).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{label} must point to localhost for offline operation; received {url}")


def load_context(url: str) -> dict[str, Any]:
    payload = request_json(url)
    if not isinstance(payload, dict) or payload.get("packet_type") != "grid_state_context":
        raise ValueError("The context endpoint did not return a grid_state_context packet.")
    return payload


def load_recheck(url: str) -> dict[str, Any] | None:
    payload = request_json(url)
    if not isinstance(payload, dict):
        return None
    request = payload.get("recheck_request")
    return request if isinstance(request, dict) else None


def build_agent(model_name: str, ollama_base_url: str) -> Any:
    """Build a PydanticAI agent with native structured output.

    Imports are intentionally lazy so the gateway and review UI can still run
    when the optional local-agent dependencies have not been installed yet.
    """
    try:
        from pydantic import BaseModel, Field
        from pydantic_ai import Agent
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider
        try:
            from pydantic_ai.output import NativeOutput
        except ImportError:
            NativeOutput = None  # type: ignore[assignment,misc]
    except ImportError as exc:
        raise RuntimeError(
            "Local agent dependencies are not installed. Run install_local_agent.ps1 or install_local_agent.sh."
        ) from exc

    from typing import Literal

    class Claim(BaseModel):
        claim_id: str
        text: str
        evidence_ids: list[str] = Field(min_length=1)
        source_kind: Literal["observed", "simulated", "inferred"]
        severity: str = "info"

    class PossibleSolution(BaseModel):
        solution_id: str
        title: str
        description: str
        rationale: str
        evidence_ids: list[str] = Field(min_length=1)
        expected_effect: str
        risks: list[str] = Field(min_length=1)
        constraints_to_check: list[str] = Field(min_length=1)
        approval_required: Literal[True] = True
        source_kind: Literal["recommended"] = "recommended"

    class AgentRecommendation(BaseModel):
        schema_version: str = "1.0.0"
        output_type: Literal["agent_recommendation"] = "agent_recommendation"
        response_id: str = Field(default_factory=lambda: f"agent-{uuid.uuid4().hex[:12]}")
        generated_at_utc: str = Field(default_factory=utc_now)
        summary: str
        summary_evidence_ids: list[str] = Field(min_length=1)
        claims: list[Claim] = Field(min_length=1)
        possible_solutions: list[PossibleSolution] = Field(min_length=1)
        unknowns: list[str] = Field(min_length=1)
        limitations: list[str] = Field(default_factory=list)

    model = OllamaModel(model_name, provider=OllamaProvider(base_url=ollama_base_url))
    output_type: Any = AgentRecommendation
    if NativeOutput is not None:
        output_type = NativeOutput(AgentRecommendation)
    return Agent(model, output_type=output_type), AgentRecommendation


def build_prompt(context: dict[str, Any], recheck: dict[str, Any] | None, schema: dict[str, Any]) -> str:
    recheck_text = json.dumps(recheck, indent=2, ensure_ascii=False) if recheck else "No pending operator recheck request."
    return f"""You are an offline GridDyn and HELICS analysis agent.

Your output will be checked by a deterministic validator before a human sees it. Return only the structured agent_recommendation object requested by your output schema.

Rules:
1. Use only facts present in the context packet below.
2. Every summary, claim, and possible solution must cite one or more exact evidence_ids from the context.
3. Preserve the distinction between observed, simulated, inferred, and recommended content.
4. Never invent an equipment rating, operating limit, topology fact, failure probability, or resource availability.
5. If information is missing, say so in unknowns and propose validation steps rather than claiming a violation or failure.
6. Possible solutions are advisory options only. They must include risks, constraints_to_check, expected_effect, and approval_required=true.
7. Never return command, control_request, target_setpoint, dispatch_command, actuation, or execute_now fields.
8. Use conditional language. Do not claim that a grid asset will fail or that a solution is guaranteed.
9. If an operator recheck request is provided, correct the identified issue rather than defending the previous response.

JSON schema:
{json.dumps(schema, indent=2, ensure_ascii=False)}

Current GridDyn/HELICS context:
{json.dumps(context, indent=2, ensure_ascii=False)}

Operator recheck request:
{recheck_text}
"""


def generate_output(agent: Any, output_model: Any, prompt: str) -> dict[str, Any]:
    result = agent.run_sync(prompt)
    output = getattr(result, "output", None)
    if output is None:
        output = getattr(result, "data", None)
    if output is None:
        raise RuntimeError("PydanticAI returned no structured output.")
    if hasattr(output, "model_dump"):
        payload = output.model_dump(mode="json")
    elif hasattr(output, "dict"):
        payload = output.dict()
    elif isinstance(output, dict):
        payload = output
    else:
        raise RuntimeError(f"Unsupported PydanticAI output type: {type(output).__name__}")
    if not isinstance(payload, dict):
        raise RuntimeError("The local agent output was not a JSON object.")
    return payload


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    require_local_url(args.ollama_base_url, "Ollama URL")
    require_local_url(args.context_url, "context URL")
    require_local_url(args.review_url, "review URL")
    require_local_url(args.recheck_url, "recheck URL")
    context = load_context(args.context_url)
    recheck = load_recheck(args.recheck_url)
    schema_path = Path(args.schema).expanduser().resolve()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    agent, output_model = build_agent(args.model, args.ollama_base_url)
    prompt = build_prompt(context, recheck, schema)
    output = generate_output(agent, output_model, prompt)
    submission = {"context": context, "output": output}
    if args.no_submit:
        print(json.dumps({"output": output, "submitted": False}, indent=2, ensure_ascii=False))
        return {"output": output, "submitted": False}
    response = request_json(args.review_url, method="POST", payload=submission, timeout=args.http_timeout)
    print(json.dumps({"output": output, "review_state": response}, indent=2, ensure_ascii=False))
    return response


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Ollama/PydanticAI GridDyn agent.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Installed Ollama model, default qwen3:8b.")
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL, help="Local Ollama OpenAI-compatible URL.")
    parser.add_argument("--context-url", default=DEFAULT_CONTEXT_URL, help="HELICS context URL.")
    parser.add_argument("--review-url", default=DEFAULT_REVIEW_URL, help="Review submission URL.")
    parser.add_argument("--recheck-url", default=DEFAULT_RECHECK_URL, help="Operator recheck request URL.")
    parser.add_argument("--schema", default=str(ROOT / "agent_output.schema.json"), help="Agent output JSON Schema.")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between runs in watch mode.")
    parser.add_argument("--once", action="store_true", help="Generate and submit one response, then exit.")
    parser.add_argument("--no-submit", action="store_true", help="Print the response without sending it to the review service.")
    parser.add_argument("--http-timeout", type=float, default=10.0, help="HTTP timeout for local services.")
    args = parser.parse_args()

    while True:
        try:
            run_once(args)
        except KeyboardInterrupt:
            print("Stopping local agent.")
            return 0
        except Exception as exc:
            print(f"local agent error: {type(exc).__name__}: {exc}", file=sys.stderr)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
