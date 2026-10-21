#!/usr/bin/env python3
"""Read-only HELICS gateway for an AI Agent observing GridDyn.

The gateway is deliberately separated from control logic. It reads HELICS
subscriptions and endpoint messages, normalizes them into an agent context
packet, and exposes read-only HTTP/JSON tools on localhost.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "helics_agent_config.json"
SCHEMA_FILE = ROOT / "agent_context.schema.json"
TOOLS_FILE = ROOT / "agent_tools.json"
MAX_HTTP_BODY = 2 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def safe_json(value: Any) -> Any:
    """Convert common HELICS/Python values into strict JSON values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, complex):
        return {"real": value.real, "imaginary": value.imag}
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    return str(value)


def parse_json_or_text(payload: str) -> tuple[Any, str]:
    try:
        return json.loads(payload), "json"
    except (TypeError, json.JSONDecodeError):
        return payload, "text"


def flatten_tags(tags: Any) -> dict[str, str]:
    if isinstance(tags, dict):
        return {str(key): str(value) for key, value in tags.items()}
    if isinstance(tags, list):
        output: dict[str, str] = {}
        for item in tags:
            if isinstance(item, dict) and "name" in item:
                output[str(item["name"])] = str(item.get("value", ""))
        return output
    return {}


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def configured_interface_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for item in config.get("subscriptions", []) + config.get("inputs", []):
        if not isinstance(item, dict):
            continue
        item = copy.deepcopy(item)
        item["tags"] = flatten_tags(item.get("tags"))
        specs.append(item)
    return specs


def context_template(federate_name: str, mode: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "packet_type": "grid_state_context",
        "packet_id": new_id("ctx"),
        "generated_at_utc": utc_now(),
        "source": {
            "federate": federate_name,
            "mode": mode,
            "system": "GridDyn through HELICS",
        },
        "simulation": {
            "time_s": None,
            "time_unit": "s",
            "time_known": False,
        },
        "observations": [],
        "events": [],
        "alerts": [],
        "quality": {
            "overall": "unknown",
            "stale_observation_count": 0,
            "missing_observation_count": 0,
            "conflict_count": 0,
        },
        "unknowns": ["No HELICS data has been received yet."],
        "decision_boundary": {
            "mode": "advisory_only",
            "read_operations": ["get_grid_context", "get_grid_events", "get_gateway_health", "validate_agent_claims"],
            "write_operations": [],
            "must_not_infer": [
                "asset failure probability without a calibrated risk model",
                "operating limits not present in the packet",
                "a control action from a descriptive event alone",
            ],
            "required_for_any_recommendation": [
                "evidence_ids",
                "HELICS simulation time",
                "units",
                "quality status",
                "independent constraint validation",
                "human operator approval",
            ],
        },
    }


class GatewayState:
    """Thread-safe latest context plus a bounded event history."""

    def __init__(self, federate_name: str, mode: str) -> None:
        self.lock = threading.RLock()
        self.federate_name = federate_name
        self.mode = mode
        self.connected = False
        self.status = "starting"
        self.status_detail = "Gateway is starting."
        self.helics_version: str | None = None
        self.last_update_utc: str | None = None
        self.last_error: str | None = None
        self.stop_event = threading.Event()
        self.events: deque[dict[str, Any]] = deque(maxlen=500)
        self.observations: dict[str, dict[str, Any]] = {}
        self.context = context_template(federate_name, mode)

    def set_status(self, status: str, detail: str, connected: bool | None = None) -> None:
        with self.lock:
            self.status = status
            self.status_detail = detail
            if connected is not None:
                self.connected = connected
            if status == "error":
                self.last_error = detail
            self._refresh_context()

    def set_helics_version(self, version: str | None) -> None:
        with self.lock:
            self.helics_version = version
            self._refresh_context()

    def set_sim_time(self, time_s: float) -> None:
        with self.lock:
            self.context["simulation"] = {
                "time_s": safe_json(float(time_s)),
                "time_unit": "s",
                "time_known": True,
            }
            self.last_update_utc = utc_now()
            self._refresh_context()

    def record_observation(
        self,
        *,
        interface_name: str,
        value: Any,
        data_type: str,
        units: str,
        sim_time_s: float | None,
        last_update_time_s: float | None,
        updated: bool,
        spec: dict[str, Any] | None = None,
    ) -> None:
        spec = spec or {}
        tags = flatten_tags(spec.get("tags"))
        asset_id = tags.get("asset_id") or spec.get("asset_id")
        semantic = tags.get("semantic") or spec.get("semantic") or "unclassified_signal"
        source_kind = tags.get("source_kind") or spec.get("source_kind") or "simulated"
        observation_id = tags.get("observation_id") or spec.get("observation_id") or interface_name
        if updated:
            quality = "fresh"
        elif last_update_time_s is None:
            quality = "missing"
        else:
            quality = "held"
        if value is None:
            quality = "missing"
        if last_update_time_s is not None and sim_time_s is not None:
            max_age = float(tags.get("max_age_s", spec.get("max_age_s", 0)) or 0)
            if max_age > 0 and sim_time_s - last_update_time_s > max_age:
                quality = "stale"

        item = {
            "observation_id": observation_id,
            "semantic": semantic,
            "asset_id": asset_id,
            "value": safe_json(value),
            "unit": units or "unspecified",
            "data_type": data_type or "unknown",
            "source_kind": source_kind,
            "source_interface": interface_name,
            "helics_key": spec.get("key") or spec.get("target") or interface_name,
            "sim_time_s": safe_json(sim_time_s),
            "last_update_time_s": safe_json(last_update_time_s),
            "updated_at_utc": utc_now(),
            "quality": quality,
            "evidence_id": f"obs:{observation_id}",
        }
        with self.lock:
            self.observations[observation_id] = item
            self.last_update_utc = item["updated_at_utc"]
            self._refresh_context()

    def record_event(
        self,
        *,
        source: str,
        destination: str,
        payload: Any,
        payload_encoding: str,
        sim_time_s: float | None,
    ) -> None:
        event = {
            "event_id": new_id("evt"),
            "source_endpoint": source,
            "destination_endpoint": destination,
            "payload": safe_json(payload),
            "payload_encoding": payload_encoding,
            "sim_time_s": safe_json(sim_time_s),
            "received_at_utc": utc_now(),
            "source_kind": "helics_message",
        }
        event["evidence_id"] = event["event_id"]
        with self.lock:
            self.events.appendleft(event)
            self.last_update_utc = event["received_at_utc"]
            self._refresh_context()

    def inject_demo(self, sim_time_s: float) -> None:
        self.set_status("demo", "Synthetic data only; no HELICS federation is connected.", connected=False)
        self.set_sim_time(sim_time_s)
        self.record_observation(
            interface_name="GridDyn/demo/bus_N07/voltage",
            value=0.94,
            data_type="double",
            units="pu",
            sim_time_s=sim_time_s,
            last_update_time_s=sim_time_s,
            updated=True,
            spec={"key": "GridDyn/demo/bus_N07/voltage", "tags": {"asset_id": "N07", "semantic": "bus_voltage", "source_kind": "simulated"}},
        )
        self.record_observation(
            interface_name="GridDyn/demo/line_N14/loading",
            value=1.08,
            data_type="double",
            units="pu",
            sim_time_s=sim_time_s,
            last_update_time_s=sim_time_s,
            updated=True,
            spec={"key": "GridDyn/demo/line_N14/loading", "tags": {"asset_id": "N14", "semantic": "line_loading", "source_kind": "simulated"}},
        )

    def _refresh_context(self) -> None:
        observations = list(self.observations.values())
        stale_count = sum(item["quality"] == "stale" for item in observations)
        missing_count = sum(item["quality"] == "missing" for item in observations)
        if self.status == "error":
            overall = "error"
        elif missing_count or stale_count:
            overall = "degraded"
        elif observations:
            overall = "usable"
        else:
            overall = "unknown"

        unknowns: list[str] = []
        if not self.connected and self.mode != "demo":
            unknowns.append("HELICS federation is not connected; no live GridDyn state is available.")
        if not observations:
            unknowns.append("No observations are available.")
        if missing_count:
            unknowns.append(f"{missing_count} observation(s) are missing.")
        if stale_count:
            unknowns.append(f"{stale_count} observation(s) are stale under their configured age limits.")
        if self.last_error:
            unknowns.append(f"Gateway error: {self.last_error}")

        self.context.update(
            {
                "generated_at_utc": utc_now(),
                "source": {
                    "federate": self.federate_name,
                    "mode": self.mode,
                    "system": "GridDyn through HELICS",
                    "helics_version": self.helics_version,
                    "gateway_status": self.status,
                    "gateway_status_detail": self.status_detail,
                },
                "observations": observations,
                "events": list(self.events),
                "quality": {
                    "overall": overall,
                    "stale_observation_count": stale_count,
                    "missing_observation_count": missing_count,
                    "conflict_count": 0,
                },
                "unknowns": unknowns,
            }
        )

    def context_snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_context()
            return copy.deepcopy(self.context)

    def health_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "service": "GridDyn HELICS Agent Gateway",
                "status": self.status,
                "status_detail": self.status_detail,
                "connected": self.connected,
                "federate_name": self.federate_name,
                "mode": self.mode,
                "helics_version": self.helics_version,
                "simulation_time_s": self.context["simulation"]["time_s"],
                "observation_count": len(self.observations),
                "event_count": len(self.events),
                "last_update_utc": self.last_update_utc,
                "last_error": self.last_error,
                "read_only": True,
            }

    def events_snapshot(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            return copy.deepcopy(list(self.events)[: max(1, min(limit, 500))])

    def validate_claims(self, claims: Any) -> dict[str, Any]:
        """Check that an agent's claims cite current packet evidence."""
        context = self.context_snapshot()
        known = {item["evidence_id"] for item in context["observations"]}
        known.update(item.get("evidence_id", item["event_id"]) for item in context["events"])
        known.update(item["event_id"] for item in context["events"])
        results = []
        if not isinstance(claims, list):
            return {"valid": False, "results": [{"valid": False, "reason": "claims must be a list"}]}
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                results.append({"index": index, "valid": False, "reason": "claim must be an object"})
                continue
            evidence_ids = claim.get("evidence_ids", [])
            missing = [item for item in evidence_ids if item not in known]
            valid = bool(evidence_ids) and not missing
            results.append(
                {
                    "index": index,
                    "claim": claim.get("text", ""),
                    "valid": valid,
                    "evidence_ids": evidence_ids,
                    "missing_evidence_ids": missing,
                    "reason": "Evidence references resolve in the current packet." if valid else "Every claim must cite existing evidence_ids.",
                }
            )
        return {"valid": all(item["valid"] for item in results), "results": results, "known_evidence_ids": sorted(known)}

    def preview_command(self, request: Any) -> dict[str, Any]:
        """Explicitly reject actuation while retaining a machine-readable reason."""
        return {
            "accepted": False,
            "executed": False,
            "reason_code": "READ_ONLY_GATEWAY",
            "message": "This gateway exposes no control or actuation path. A human-approved, independently validated control service must handle commands.",
            "request_received": safe_json(request),
        }


class HelicsWorker(threading.Thread):
    def __init__(self, state: GatewayState, config_path: Path, demo: bool, once: bool = False) -> None:
        super().__init__(daemon=True)
        self.state = state
        self.config_path = config_path
        self.demo = demo
        self.once = once
        self.config: dict[str, Any] = {}

    def run(self) -> None:
        if self.demo:
            self.run_demo()
            return
        try:
            import helics as h  # type: ignore
        except Exception as exc:
            self.state.set_status("error", f"PyHELICS is unavailable: {type(exc).__name__}: {exc}", connected=False)
            return

        fed = None
        try:
            self.config = load_config(self.config_path)
            name = str(self.config.get("name", self.state.federate_name))
            self.state.federate_name = name
            self.state.set_helics_version(str(h.helicsGetVersion()))
            fed = h.helicsCreateCombinationFederateFromConfig(str(self.config_path))
            h.helicsFederateEnterExecutingMode(fed)
            self.state.set_status("connected", "Combination federate entered executing mode.", connected=True)

            specs = configured_interface_specs(self.config)
            period = float(self.config.get("period", 1.0))
            requested_time = 0.0
            while not self.state.stop_event.is_set():
                granted_time = h.helicsFederateRequestTime(fed, requested_time)
                self.state.set_sim_time(float(granted_time))
                self.collect_inputs(h, fed, specs, float(granted_time))
                self.collect_messages(h, fed, float(granted_time))
                if self.once:
                    break
                requested_time = float(granted_time) + max(period, 1e-9)
        except Exception as exc:
            self.state.set_status("error", f"HELICS gateway failed: {type(exc).__name__}: {exc}", connected=False)
            self.state.context["unknowns"].append(traceback.format_exc(limit=3))
        finally:
            if fed is not None:
                for function_name in ("helicsFederateFinalize", "helicsFederateFree"):
                    function = getattr(h, function_name, None)
                    if function is not None:
                        try:
                            function(fed)
                        except Exception:
                            pass
            if self.state.status == "connected":
                self.state.set_status("stopped", "HELICS gateway stopped.", connected=False)

    def run_demo(self) -> None:
        self.state.set_status("demo", "Synthetic data only; no HELICS federation is connected.", connected=False)
        sim_time = 0.0
        while not self.state.stop_event.is_set():
            self.state.inject_demo(sim_time)
            if self.once:
                break
            sim_time += 1.0
            self.state.stop_event.wait(1.0)

    def collect_inputs(self, helics: Any, fed: Any, specs: list[dict[str, Any]], sim_time_s: float) -> None:
        spec_by_name: dict[str, dict[str, Any]] = {}
        for spec in specs:
            for key in (spec.get("key"), spec.get("target"), spec.get("name")):
                if key:
                    spec_by_name[str(key)] = spec

        count = int(helics.helicsFederateGetInputCount(fed))
        for index in range(max(0, count)):
            inp = helics.helicsFederateGetInputByIndex(fed, index)
            name = str(helics.helicsInputGetName(inp))
            target = str(helics.helicsInputGetTarget(inp))
            spec = spec_by_name.get(name) or spec_by_name.get(target) or {}
            data_type = str(helics.helicsInputGetType(inp) or spec.get("type", "unknown"))
            units = str(helics.helicsInputGetUnits(inp) or spec.get("units", ""))
            value = self.read_input_value(helics, inp, data_type)
            updated = bool(helics.helicsInputIsUpdated(inp))
            last_update = float(helics.helicsInputLastUpdateTime(inp))
            if not math.isfinite(last_update) or last_update < 0:
                last_update_value: float | None = None
            else:
                last_update_value = last_update
            self.state.record_observation(
                interface_name=name or target,
                value=value,
                data_type=data_type,
                units=units,
                sim_time_s=sim_time_s,
                last_update_time_s=last_update_value,
                updated=updated,
                spec={**spec, "key": spec.get("key") or target},
            )

    @staticmethod
    def read_input_value(helics: Any, inp: Any, data_type: str) -> Any:
        kind = data_type.lower()
        if "vector" in kind:
            return helics.helicsInputGetVector(inp)
        if "complex" in kind:
            return helics.helicsInputGetComplex(inp)
        if "bool" in kind:
            return helics.helicsInputGetBoolean(inp)
        if "int" in kind:
            return helics.helicsInputGetInteger(inp)
        if any(token in kind for token in ("double", "float", "time")):
            return helics.helicsInputGetDouble(inp)
        value = helics.helicsInputGetString(inp)
        parsed, encoding = parse_json_or_text(value)
        return parsed if encoding == "json" else value

    def collect_messages(self, helics: Any, fed: Any, sim_time_s: float) -> None:
        while True:
            message = helics.helicsFederateGetMessage(fed)
            if message is None or not helics.helicsMessageIsValid(message):
                break
            raw_payload = str(helics.helicsMessageGetString(message))
            payload, encoding = parse_json_or_text(raw_payload)
            self.state.record_event(
                source=str(helics.helicsMessageGetSource(message)),
                destination=str(helics.helicsMessageGetDestination(message)),
                payload=payload,
                payload_encoding=encoding,
                sim_time_s=float(helics.helicsMessageGetTime(message) or sim_time_s),
            )


class AgentHandler(BaseHTTPRequestHandler):
    server_version = "GridDynHelicsAgentGateway/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[agent-gateway] " + (fmt % args))

    @property
    def gateway(self) -> GatewayState:
        return self.server.gateway_state  # type: ignore[attr-defined]

    def send_json(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_HTTP_BODY:
            raise ValueError("Request body is missing or too large.")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            self.send_json(self.gateway.health_snapshot())
            return
        if parsed.path in ("/v1/context", "/v1/agent-context"):
            self.send_json(self.gateway.context_snapshot())
            return
        if parsed.path == "/v1/events":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", [50])[0])
            except ValueError:
                limit = 50
            self.send_json({"events": self.gateway.events_snapshot(limit)})
            return
        if parsed.path == "/v1/schema":
            self.send_json(json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))
            return
        if parsed.path == "/v1/tools":
            self.send_json(json.loads(TOOLS_FILE.read_text(encoding="utf-8")))
            return
        if parsed.path == "/":
            self.send_json({"service": "GridDyn HELICS Agent Gateway", "read_only": True, "tools": "/v1/tools"})
            return
        self.send_json({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self.read_json()
            if parsed.path == "/v1/agent/validate":
                claims = body.get("claims") if isinstance(body, dict) else None
                self.send_json(self.gateway.validate_claims(claims))
                return
            if parsed.path == "/v1/command/preview":
                self.send_json(self.gateway.preview_command(body), 403)
                return
            self.send_json({"error": "not_found"}, 404)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only HELICS gateway for an AI Agent observing GridDyn.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="HELICS combination-federate config JSON.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address; keep localhost for local agents.")
    parser.add_argument("--port", type=int, default=8766, help="HTTP port; defaults to 8766.")
    parser.add_argument("--demo", action="store_true", help="Use synthetic observations instead of connecting to HELICS.")
    parser.add_argument("--once", action="store_true", help="Read one time step and stop the HELICS worker.")
    args = parser.parse_args()

    config = load_config(args.config) if args.config.exists() else {}
    name = str(config.get("name", "GridDynAgentGateway"))
    mode = "demo" if args.demo else "helics"
    state = GatewayState(name, mode)
    worker = HelicsWorker(state, args.config, args.demo, args.once)
    worker.start()

    server = ThreadingHTTPServer((args.host, args.port), AgentHandler)
    server.gateway_state = state  # type: ignore[attr-defined]
    print(f"AI Agent gateway listening at http://{args.host}:{args.port}")
    print("Read-only tools: GET /v1/context, GET /v1/events, GET /v1/health, POST /v1/agent/validate")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AI Agent gateway.")
    finally:
        state.stop_event.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
