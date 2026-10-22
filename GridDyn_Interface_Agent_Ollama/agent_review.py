#!/usr/bin/env python3
"""Evidence-gated, human-readable review interface for AI Agent output.

This module does not call an LLM and does not execute grid controls. An AI
Agent submits a structured response plus the HELICS context packet it used.
The validator checks evidence references, data quality, source labels,
recommendation safety fields, and unsupported command-like fields before the
response is made available to the operator review console.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any


ALLOWED_SOURCE_KINDS = {"observed", "simulated", "inferred", "recommended"}
ALLOWED_REVIEW_DECISIONS = {"accept", "request_recheck", "reject"}
RECHECK_ACTIONS = {
    "unsupported_claim": "Remove the unsupported claim or cite current HELICS evidence that directly supports it.",
    "stale_data": "Re-read the HELICS context and do not treat held, stale, or missing values as current.",
    "missing_context": "Identify the missing signal, unit, timestamp, limit, topology fact, or operating constraint before recommending.",
    "wrong_interpretation": "Re-evaluate the value using its unit, semantic label, asset, simulation time, and source kind.",
    "unsafe_action": "Remove direct actuation instructions and return advisory options with approval_required set to true.",
    "unclear_solution": "Explain the expected effect, risks, constraints to check, and evidence for each possible solution.",
}
FORBIDDEN_COMMAND_KEYS = {
    "command",
    "commands",
    "control_request",
    "control_requests",
    "execute_now",
    "actuation",
    "breaker_command",
    "dispatch_command",
    "target_setpoint",
}
ABSOLUTE_LANGUAGE = re.compile(r"\b(guarantee|guaranteed|certainly|definitely|will fail|will prevent|no risk)\b", re.I)
NUMERIC_LANGUAGE = re.compile(r"(?<![A-Za-z])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:\s*(?:pu|hz|mw|mvar|kv|a|%|seconds?|s))?\b", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def safe_copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _evidence_index(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for observation in _list(context.get("observations")):
        if not isinstance(observation, dict):
            continue
        evidence_id = observation.get("evidence_id")
        if evidence_id:
            evidence[str(evidence_id)] = {
                "kind": "observation",
                "quality": str(observation.get("quality", "unknown")),
                "semantic": observation.get("semantic", ""),
                "asset_id": observation.get("asset_id"),
                "unit": observation.get("unit", "unspecified"),
            }
    for event in _list(context.get("events")):
        if not isinstance(event, dict):
            continue
        evidence_id = event.get("evidence_id") or event.get("event_id")
        if evidence_id:
            evidence[str(evidence_id)] = {
                "kind": "event",
                "quality": "fresh",
                "semantic": "helics_event",
                "asset_id": None,
                "unit": "event",
            }
    return evidence


def _context_shape_ok(context: Any) -> bool:
    return (
        isinstance(context, dict)
        and context.get("packet_type") == "grid_state_context"
        and isinstance(context.get("source"), dict)
        and isinstance(context.get("simulation"), dict)
        and isinstance(context.get("observations"), list)
        and isinstance(context.get("events"), list)
    )


def build_demo_context() -> dict[str, Any]:
    """Create an explicitly synthetic context for exercising the review UI."""
    return {
        "schema_version": "1.0.0",
        "packet_type": "grid_state_context",
        "packet_id": new_id("ctx-demo"),
        "generated_at_utc": utc_now(),
        "source": {
            "federate": "GridDynAgentGateway",
            "mode": "demo",
            "system": "GridDyn through HELICS",
            "helics_version": None,
            "gateway_status": "demo",
            "gateway_status_detail": "Synthetic data only; no HELICS federation is connected.",
        },
        "simulation": {"time_s": 3.0, "time_unit": "s", "time_known": True},
        "observations": [
            {
                "observation_id": "GridDyn/demo/bus_N07/voltage",
                "semantic": "bus_voltage",
                "asset_id": "N07",
                "value": 0.94,
                "unit": "pu",
                "data_type": "double",
                "source_kind": "simulated",
                "source_interface": "GridDyn/demo/bus_N07/voltage",
                "helics_key": "GridDyn/demo/bus_N07/voltage",
                "sim_time_s": 3.0,
                "last_update_time_s": 3.0,
                "updated_at_utc": utc_now(),
                "quality": "fresh",
                "evidence_id": "obs:GridDyn/demo/bus_N07/voltage",
            },
            {
                "observation_id": "GridDyn/demo/line_N14/loading",
                "semantic": "line_loading",
                "asset_id": "N14",
                "value": 1.08,
                "unit": "pu",
                "data_type": "double",
                "source_kind": "simulated",
                "source_interface": "GridDyn/demo/line_N14/loading",
                "helics_key": "GridDyn/demo/line_N14/loading",
                "sim_time_s": 3.0,
                "last_update_time_s": 3.0,
                "updated_at_utc": utc_now(),
                "quality": "fresh",
                "evidence_id": "obs:GridDyn/demo/line_N14/loading",
            },
        ],
        "events": [],
        "alerts": [],
        "quality": {
            "overall": "usable",
            "stale_observation_count": 0,
            "missing_observation_count": 0,
            "conflict_count": 0,
        },
        "unknowns": [
            "The context is synthetic and contains no real topology, equipment ratings, or operating policy.",
            "No calibrated failure probability is available.",
        ],
        "decision_boundary": {
            "mode": "advisory_only",
            "write_operations": [],
            "required_for_any_recommendation": ["evidence_ids", "units", "quality status", "human operator approval"],
        },
    }


def build_demo_agent_output(context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or build_demo_context()
    observations = {item.get("semantic"): item for item in _list(context.get("observations")) if isinstance(item, dict)}
    voltage_id = observations.get("bus_voltage", {}).get("evidence_id", "obs:GridDyn/demo/bus_N07/voltage")
    loading_id = observations.get("line_loading", {}).get("evidence_id", "obs:GridDyn/demo/line_N14/loading")
    return {
        "schema_version": "1.0.0",
        "output_type": "agent_recommendation",
        "response_id": new_id("agent-demo"),
        "generated_at_utc": utc_now(),
        "summary": "The received packet contains a simulated high line-loading observation and a simulated low bus-voltage observation. These signals warrant validation and contingency analysis; they do not by themselves prove an equipment failure.",
        "summary_evidence_ids": [loading_id, voltage_id],
        "claims": [
            {
                "claim_id": "claim-line-loading",
                "text": "Line N14 loading is 1.08 pu in the received simulated packet.",
                "evidence_ids": [loading_id],
                "source_kind": "simulated",
                "severity": "warning",
            },
            {
                "claim_id": "claim-bus-voltage",
                "text": "Bus N07 voltage is 0.94 pu in the received simulated packet.",
                "evidence_ids": [voltage_id],
                "source_kind": "simulated",
                "severity": "warning",
            },
        ],
        "possible_solutions": [
            {
                "solution_id": "solution-line-validation",
                "title": "Validate the line-loading condition",
                "description": "Run an independent power-flow and contingency study for line N14 using its equipment rating and the current topology.",
                "rationale": "A loading observation is evidence for investigation, not proof that a thermal or protection limit has been exceeded.",
                "evidence_ids": [loading_id],
                "expected_effect": "Determine whether the condition persists and identify the limiting constraint.",
                "risks": ["The displayed loading may not be comparable to the equipment rating without a confirmed unit and limit."],
                "constraints_to_check": ["equipment rating", "topology status", "N-1 contingency policy", "simulation convergence"],
                "approval_required": True,
                "source_kind": "recommended",
            },
            {
                "solution_id": "solution-voltage-validation",
                "title": "Investigate voltage support near bus N07",
                "description": "Compare validated voltage-support options in a study model after confirming the bus-voltage unit, limit, and available reactive resources.",
                "rationale": "The simulated voltage observation may indicate a constraint, but the operating limit and available controls are not present in the packet.",
                "evidence_ids": [voltage_id],
                "expected_effect": "Identify feasible voltage-support options without assuming a control action is safe or available.",
                "risks": ["A voltage response cannot be predicted from this packet alone."],
                "constraints_to_check": ["voltage limit", "reactive reserve", "equipment capability", "independent power-flow validation"],
                "approval_required": True,
                "source_kind": "recommended",
            },
        ],
        "unknowns": [
            "The packet is synthetic and does not include equipment ratings, operating limits, topology status, or calibrated risk data.",
            "No recommendation should be converted into a control command without an independent validated study and operator approval.",
        ],
        "limitations": ["This demonstration response is not a live LLM result."],
    }


def _walk_for_forbidden_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            child_path = f"{path}.{key}" if path else str(key)
            if key_text in FORBIDDEN_COMMAND_KEYS:
                found.append(child_path)
            found.extend(_walk_for_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_for_forbidden_keys(child, f"{path}[{index}]"))
    return found


def validate_agent_output(output: Any, context: Any) -> dict[str, Any]:
    """Validate structured agent output before the human review surface renders it."""
    checks: list[dict[str, Any]] = []
    errors = 0
    warnings = 0

    def add(code: str, status: str, message: str, *, path: str = "", evidence_ids: list[str] | None = None) -> None:
        nonlocal errors, warnings
        if status == "fail":
            errors += 1
        elif status == "warning":
            warnings += 1
        checks.append(
            {
                "code": code,
                "status": status,
                "severity": "error" if status == "fail" else "warning" if status == "warning" else "info",
                "message": message,
                "path": path,
                "evidence_ids": evidence_ids or [],
            }
        )

    if not _context_shape_ok(context):
        add("context_shape", "fail", "The submitted context is not a GridDyn/HELICS grid_state_context packet.", path="context")
        context = {}
    evidence = _evidence_index(context)
    quality = context.get("quality", {}) if isinstance(context, dict) else {}
    source = context.get("source", {}) if isinstance(context, dict) else {}

    if not isinstance(output, dict):
        add("output_shape", "fail", "The AI Agent response must be a JSON object following the agent output contract.", path="output")
        output = {}

    required = ["output_type", "response_id", "generated_at_utc", "summary", "summary_evidence_ids", "claims", "possible_solutions", "unknowns"]
    for field in required:
        if field not in output:
            add("missing_field", "fail", f"Required field '{field}' is missing.", path=f"output.{field}")

    if output.get("output_type") != "agent_recommendation":
        add("output_type", "fail", "output_type must be 'agent_recommendation'.", path="output.output_type")

    if not evidence:
        add("evidence_available", "fail", "The context contains no evidence records to support an agent response.", path="context.observations")
    else:
        add("evidence_available", "pass", f"{len(evidence)} evidence record(s) are available for checking.", path="context")

    overall = str(quality.get("overall", "unknown")) if isinstance(quality, dict) else "unknown"
    if overall == "error":
        add("context_quality", "fail", "The HELICS context reports an error; the response is held.", path="context.quality.overall")
    elif overall in {"unknown", "degraded"}:
        add("context_quality", "warning", f"The HELICS context quality is '{overall}'; recommendations require additional review.", path="context.quality.overall")
    else:
        add("context_quality", "pass", "The context reports usable overall quality.", path="context.quality.overall")

    if str(source.get("mode", "")) == "demo":
        add("synthetic_context", "warning", "The context is synthetic demo data; no operational conclusion may be drawn from it.", path="context.source.mode")
    if int(quality.get("conflict_count", 0) or 0) > 0:
        add("conflicting_context", "fail", "The context reports conflicting observations.", path="context.quality.conflict_count")

    checked_evidence: set[str] = set()

    def check_refs(refs: Any, path: str, label: str) -> list[str]:
        nonlocal errors, warnings
        if not isinstance(refs, list) or not refs:
            add("evidence_required", "fail", f"{label} must cite at least one evidence_id.", path=path)
            return []
        normalized = [str(item) for item in refs]
        for evidence_id in normalized:
            record = evidence.get(evidence_id)
            if record is None:
                add("evidence_missing", "fail", f"Evidence id '{evidence_id}' does not exist in the submitted context.", path=path, evidence_ids=[evidence_id])
                continue
            checked_evidence.add(evidence_id)
            evidence_quality = record.get("quality", "unknown")
            if evidence_quality in {"stale", "missing", "invalid", "unknown"}:
                add("evidence_not_current", "fail", f"Evidence '{evidence_id}' is marked {evidence_quality}; it cannot support a current claim.", path=path, evidence_ids=[evidence_id])
            elif evidence_quality == "held":
                add("evidence_held", "warning", f"Evidence '{evidence_id}' is held rather than freshly updated; explain that limitation.", path=path, evidence_ids=[evidence_id])
        return normalized

    summary_ids = check_refs(output.get("summary_evidence_ids"), "output.summary_evidence_ids", "The summary")
    if _text(output.get("summary")) and NUMERIC_LANGUAGE.search(_text(output.get("summary"))) and not summary_ids:
        add("numeric_summary_evidence", "fail", "A summary containing numeric information must cite evidence_ids.", path="output.summary")
    if ABSOLUTE_LANGUAGE.search(_text(output.get("summary"))):
        add("absolute_language", "warning", "The summary uses absolute or certainty language; rewrite it conditionally unless directly proven.", path="output.summary")

    claims = output.get("claims")
    if not isinstance(claims, list) or not claims:
        add("claims_present", "fail", "The response must contain at least one structured claim.", path="output.claims")
    else:
        for index, claim in enumerate(claims):
            path = f"output.claims[{index}]"
            if not isinstance(claim, dict):
                add("claim_shape", "fail", "Each claim must be an object.", path=path)
                continue
            if not _text(claim.get("text")):
                add("claim_text", "fail", "Each claim needs readable text.", path=f"{path}.text")
            check_refs(claim.get("evidence_ids"), f"{path}.evidence_ids", "Each claim")
            source_kind = str(claim.get("source_kind", ""))
            if source_kind not in ALLOWED_SOURCE_KINDS - {"recommended"}:
                add("claim_source_kind", "fail", "Claims must be labeled observed, simulated, or inferred; recommended is not an observation.", path=f"{path}.source_kind")
            if ABSOLUTE_LANGUAGE.search(_text(claim.get("text"))):
                add("claim_absolute_language", "warning", "The claim uses certainty language; request a qualified statement or stronger evidence.", path=f"{path}.text")

    solutions = output.get("possible_solutions")
    if not isinstance(solutions, list) or not solutions:
        add("solutions_present", "fail", "The response must contain at least one possible solution or explicitly explain why none is safe to propose.", path="output.possible_solutions")
    else:
        for index, solution in enumerate(solutions):
            path = f"output.possible_solutions[{index}]"
            if not isinstance(solution, dict):
                add("solution_shape", "fail", "Each possible solution must be an object.", path=path)
                continue
            for field in ("title", "description", "rationale", "expected_effect"):
                if not _text(solution.get(field)):
                    add("solution_field", "fail", f"Each possible solution needs '{field}'.", path=f"{path}.{field}")
            check_refs(solution.get("evidence_ids"), f"{path}.evidence_ids", "Each possible solution")
            if solution.get("source_kind") != "recommended":
                add("solution_source_kind", "fail", "Possible solutions must be labeled source_kind='recommended'.", path=f"{path}.source_kind")
            if solution.get("approval_required") is not True:
                add("approval_required", "fail", "Every possible solution must require human approval.", path=f"{path}.approval_required")
            if not _list(solution.get("risks")):
                add("solution_risks", "warning", "Include at least one risk or limitation for each possible solution.", path=f"{path}.risks")
            if not _list(solution.get("constraints_to_check")):
                add("solution_constraints", "warning", "Include constraints to check before an operator considers a solution.", path=f"{path}.constraints_to_check")
            if ABSOLUTE_LANGUAGE.search(" ".join(_text(solution.get(field)) for field in ("title", "description", "rationale", "expected_effect"))):
                add("solution_absolute_language", "warning", "The solution uses certainty language; rewrite it as a conditional option.", path=path)

    forbidden_paths = _walk_for_forbidden_keys(output)
    if forbidden_paths:
        add("direct_actuation", "fail", "The response contains command or actuation fields. This interface accepts advisory options only.", path=", ".join(forbidden_paths))

    if _list(output.get("unknowns")):
        add("unknowns_present", "pass", "The response preserves explicit unknowns and limitations.", path="output.unknowns")
    else:
        add("unknowns_present", "warning", "The response has no explicit unknowns; ask the agent to state what it cannot determine.", path="output.unknowns")

    if checked_evidence:
        add("evidence_traceability", "pass", f"Checked {len(checked_evidence)} referenced evidence record(s).", path="output")
    add("independent_validation", "pass", "Human approval and independent physics/constraint validation remain required before action.", path="decision_boundary")

    status = "blocked" if errors else "needs_review" if warnings else "passed"
    return {
        "validation_id": new_id("validation"),
        "checked_at_utc": utc_now(),
        "status": status,
        "ready_for_human": status != "blocked",
        "presentation_gate": "HOLD" if status == "blocked" else "REVIEW" if status == "needs_review" else "READY",
        "error_count": errors,
        "warning_count": warnings,
        "checks": checks,
        "evidence_checked": sorted(checked_evidence),
        "instructions": "Do not convert recommendations into controls. If the operator finds an issue, request a recheck and provide specific feedback.",
    }


def load_live_context(url: str = "http://127.0.0.1:8766/v1/context", timeout: float = 2.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - URL defaults to localhost and is user-configurable only in code
        payload = json.loads(response.read().decode("utf-8"))
    if not _context_shape_ok(payload):
        raise ValueError("The HELICS gateway returned an invalid context packet.")
    return payload


def _recheck_actions(issue_types: list[str]) -> list[str]:
    actions = [RECHECK_ACTIONS[item] for item in issue_types if item in RECHECK_ACTIONS]
    return actions or ["Re-read the current context, verify every claim against evidence_ids, and return a corrected structured response."]


class ReviewStore:
    """Process-local review state for one operator console session."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.context: dict[str, Any] | None = None
        self.agent_output: dict[str, Any] | None = None
        self.validation: dict[str, Any] | None = None
        self.latest_review: dict[str, Any] | None = None
        self.review_history: list[dict[str, Any]] = []
        self.recheck_request: dict[str, Any] | None = None

    def set_context(self, context: Any) -> dict[str, Any]:
        if not _context_shape_ok(context):
            raise ValueError("Context must be a GridDyn/HELICS grid_state_context packet.")
        with self.lock:
            self.context = safe_copy(context)
            self.agent_output = None
            self.validation = None
            self.latest_review = None
            self.recheck_request = None
            return self.snapshot()

    def submit(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Submission must be a JSON object.")
        output = payload.get("output")
        context = payload.get("context", self.context)
        if context is None:
            raise ValueError("Submit the HELICS context packet with the AI Agent output before validation.")
        validation = validate_agent_output(output, context)
        with self.lock:
            self.context = safe_copy(context) if _context_shape_ok(context) else None
            self.agent_output = safe_copy(output) if isinstance(output, dict) else {"raw": safe_copy(output)}
            self.validation = validation
            self.latest_review = None
            self.recheck_request = None
            return self.snapshot()

    def record_review(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Review must be a JSON object.")
        decision = str(payload.get("decision", ""))
        if decision not in ALLOWED_REVIEW_DECISIONS:
            raise ValueError(f"decision must be one of: {', '.join(sorted(ALLOWED_REVIEW_DECISIONS))}.")
        feedback = _text(payload.get("feedback")).strip()
        if decision in {"request_recheck", "reject"} and not feedback:
            raise ValueError("Provide feedback when rejecting or requesting a recheck.")
        issue_types = [str(item) for item in _list(payload.get("issue_types"))]
        with self.lock:
            if self.agent_output is None or self.validation is None:
                raise ValueError("Submit and validate an AI Agent response before recording human review.")
            if decision == "accept" and self.validation.get("status") == "blocked":
                raise ValueError("A blocked response cannot be accepted. Request a recheck or reject it first.")
            review = {
                "review_id": new_id("review"),
                "created_at_utc": utc_now(),
                "decision": decision,
                "feedback": feedback,
                "issue_types": issue_types,
                "selected_solution_ids": [str(item) for item in _list(payload.get("selected_solution_ids"))],
                "validator_status_at_review": self.validation.get("status"),
            }
            self.latest_review = review
            self.review_history.insert(0, review)
            self.review_history = self.review_history[:25]
            if decision in {"request_recheck", "reject"}:
                self.recheck_request = {
                    "request_type": "agent_recheck",
                    "request_id": new_id("recheck"),
                    "created_at_utc": utc_now(),
                    "status": "pending_agent_recheck",
                    "previous_response_id": self.agent_output.get("response_id") if isinstance(self.agent_output, dict) else None,
                    "context_packet_id": self.context.get("packet_id") if self.context else None,
                    "operator_feedback": feedback,
                    "issue_types": issue_types,
                    "required_actions": _recheck_actions(issue_types),
                    "must_return": [
                        "corrected structured output",
                        "evidence_ids for the summary, every claim, and every possible solution",
                        "explicit unknowns and limitations",
                        "approval_required=true for every possible solution",
                    ],
                }
            else:
                self.recheck_request = None
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            context = safe_copy(self.context)
            return {
                "service": "GridDyn AI Agent Output Review",
                "read_only_to_grid": True,
                "context": context,
                "context_summary": {
                    "packet_id": context.get("packet_id") if context else None,
                    "source_mode": context.get("source", {}).get("mode") if context else None,
                    "simulation_time_s": context.get("simulation", {}).get("time_s") if context else None,
                    "quality": context.get("quality", {}).get("overall") if context else None,
                    "observation_count": len(context.get("observations", [])) if context else 0,
                    "event_count": len(context.get("events", [])) if context else 0,
                },
                "agent_output": safe_copy(self.agent_output),
                "validation": safe_copy(self.validation),
                "latest_review": safe_copy(self.latest_review),
                "review_history": safe_copy(self.review_history),
                "recheck_request": safe_copy(self.recheck_request),
            }
