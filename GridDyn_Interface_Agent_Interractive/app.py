#!/usr/bin/env python3
"""Local GridDyn operator interface.

The server intentionally binds to localhost. It can use:
  1. the GridDyn Python package,
  2. the gridDynMain executable, or
  3. an explicitly selected synthetic demo mode.

The interface is advisory only. It does not send control commands to a grid.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
CASE_DIR = ROOT / "cases"
REPORT_DIR = ROOT / "reports"
MAX_REQUEST_BYTES = 50 * 1024 * 1024

CASE_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... output truncated ..."


def detect_tooling() -> dict[str, Any]:
    python_package = importlib.util.find_spec("griddyn") is not None
    helics_package = importlib.util.find_spec("helics") is not None
    grid_dyn_main = shutil.which("gridDynMain") or shutil.which("gridDynMain.exe")
    helics_broker = shutil.which("helics_broker") or shutil.which("helics_broker.exe")
    if python_package:
        selected_mode = "python-package"
    elif grid_dyn_main:
        selected_mode = "gridDynMain"
    else:
        selected_mode = "demo-only"
    return {
        "python": sys.version.split()[0],
        "griddyn_python": python_package,
        "gridDynMain": grid_dyn_main or False,
        "helics_python": helics_package,
        "helics_broker": helics_broker or False,
        "selected_mode": selected_mode,
    }


def check(label: str, status: str, detail: str, source: str = "interface") -> dict[str, str]:
    return {"label": label, "status": status, "detail": detail, "source": source}


def base_analysis(mode: str, case_path: str | None = None) -> dict[str, Any]:
    return {
        "run_id": run_id(),
        "created_at": utc_now(),
        "mode": mode,
        "case_path": case_path or "",
        "status": "unknown",
        "summary": "",
        "metrics": [],
        "recommendations": [],
        "checks": [],
        "evidence": [],
        "unknowns": [],
        "raw_output": "",
        "report_markdown": "",
        "report_json_path": "",
        "report_markdown_path": "",
    }


def build_demo_analysis() -> dict[str, Any]:
    """Return synthetic data only; no operational claim is made."""
    analysis = base_analysis("demo", "synthetic-demo-case")
    analysis.update(
        {
            "status": "demo",
            "summary": "Synthetic interface check completed. No GridDyn case was executed.",
            "metrics": [
                {"label": "Line loading", "value": "108%", "detail": "synthetic warning", "severity": "warning"},
                {"label": "Bus voltage", "value": "0.94 pu", "detail": "synthetic warning", "severity": "warning"},
                {"label": "Frequency trend", "value": "-0.018 Hz/s", "detail": "synthetic trend", "severity": "warning"},
                {"label": "Data age", "value": "2.1 s", "detail": "synthetic timestamp", "severity": "neutral"},
            ],
            "recommendations": [
                {
                    "title": "No operational recommendation in demo mode",
                    "detail": "Connect a real GridDyn case and a validated optimizer before using recommendations.",
                    "basis": ["demo-only"],
                    "approval_required": True,
                }
            ],
            "checks": [
                check("Execution source", "warning", "Synthetic data; GridDyn was not called."),
                check("Decision boundary", "pass", "No control command or real-world action was generated."),
                check("Evidence provenance", "warning", "All values are labeled synthetic."),
            ],
            "evidence": [
                {"id": "demo-001", "kind": "synthetic", "detail": "Built-in interface fixture", "timestamp": utc_now()}
            ],
            "unknowns": [
                "No real topology, telemetry, equipment ratings, or operating policy were loaded.",
                "Failure probability and resource allocation are not calculated in demo mode.",
            ],
            "raw_output": "DEMO MODE\nNo GridDyn executable or Python package was called.\n",
        }
    )
    return finalize_analysis(analysis)


def summarize_object(value: Any) -> str:
    try:
        text = repr(value)
    except Exception as exc:  # pragma: no cover - defensive logging path
        text = f"<unprintable result: {exc}>"
    return truncate(text, 4000)


def run_grid_dyn_python(case_path: Path) -> dict[str, Any]:
    analysis = base_analysis("python-package", str(case_path))
    try:
        import griddyn as gd  # type: ignore

        sim = gd.load(case_path)
        result: Any = None
        operation = ""

        power_flow = getattr(sim, "power_flow", None)
        if power_flow is not None and callable(getattr(power_flow, "run", None)):
            result = power_flow.run()
            operation = "sim.power_flow.run()"
        else:
            legacy_power_flow = getattr(sim, "PFlow", None)
            if legacy_power_flow is not None and callable(getattr(legacy_power_flow, "run", None)):
                result = legacy_power_flow.run()
                operation = "sim.PFlow.run()"
            elif callable(getattr(sim, "run", None)):
                result = sim.run()
                operation = "sim.run()"
            else:
                raise RuntimeError("GridDyn loaded the case, but no supported run method was found.")

        analysis.update(
            {
                "status": "success",
                "summary": f"GridDyn Python adapter completed {operation}. Numerical results require case-specific field mapping.",
                "checks": [
                    check("GridDyn execution", "pass", operation, "griddyn Python package"),
                    check("Control boundary", "pass", "No control command was emitted by this adapter."),
                    check("Numerical mapping", "warning", "The interface does not invent metrics; map your GridDyn result fields before relying on them."),
                ],
                "evidence": [
                    {"id": "griddyn-run", "kind": "simulation", "detail": operation, "timestamp": utc_now()},
                    {"id": "griddyn-result", "kind": "simulation-result", "detail": summarize_object(result), "timestamp": utc_now()},
                ],
                "unknowns": [
                    "Case-specific voltage, flow, frequency, relay, and rating fields are not inferred automatically.",
                    "No failure probability or resource-optimization model is attached yet.",
                ],
                "raw_output": f"GridDyn Python result:\n{summarize_object(result)}\n",
            }
        )
    except Exception as exc:
        analysis.update(
            {
                "status": "error",
                "summary": "The GridDyn Python adapter could not complete the run.",
                "checks": [check("GridDyn execution", "fail", f"{type(exc).__name__}: {exc}", "griddyn Python package")],
                "unknowns": ["No recommendation was generated because execution failed."],
                "raw_output": traceback.format_exc(),
            }
        )
    return finalize_analysis(analysis)


def parse_cli_evidence(output: str) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    if re.search(r"\b(converged|convergence successful)\b", output, re.I):
        evidence.append({"id": "cli-convergence", "kind": "simulation-log", "detail": "Output contains a convergence indication.", "timestamp": utc_now()})
    if re.search(r"\b(not converged|failed|error|exception)\b", output, re.I):
        evidence.append({"id": "cli-warning", "kind": "simulation-log", "detail": "Output contains a failure or warning indication; inspect the raw log.", "timestamp": utc_now()})
    return evidence


def run_grid_dyn_cli(case_path: Path, executable: str) -> dict[str, Any]:
    analysis = base_analysis("gridDynMain", str(case_path))
    try:
        completed = subprocess.run(
            [executable, str(case_path)],
            cwd=str(case_path.parent),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        status = "success" if completed.returncode == 0 else "error"
        analysis.update(
            {
                "status": status,
                "summary": "gridDynMain completed." if status == "success" else f"gridDynMain exited with code {completed.returncode}.",
                "checks": [
                    check("gridDynMain execution", "pass" if status == "success" else "fail", f"Exit code {completed.returncode}", executable),
                    check("Raw evidence", "pass", "The unmodified process output is retained below.", executable),
                    check("Numerical mapping", "warning", "No metrics are inferred from text output; map your case output fields explicitly.", "interface"),
                ],
                "evidence": parse_cli_evidence(output),
                "unknowns": [
                    "Case-specific result files and numerical fields are not parsed automatically.",
                    "No failure probability or resource-optimization model is attached yet.",
                ],
                "raw_output": truncate(output),
            }
        )
    except subprocess.TimeoutExpired:
        analysis.update(
            {
                "status": "error",
                "summary": "gridDynMain exceeded the 180-second safety timeout.",
                "checks": [check("gridDynMain execution", "fail", "Process timed out.", executable)],
                "unknowns": ["No recommendation was generated because execution timed out."],
                "raw_output": "Process timeout after 180 seconds.\n",
            }
        )
    except Exception as exc:
        analysis.update(
            {
                "status": "error",
                "summary": "The gridDynMain adapter could not complete the run.",
                "checks": [check("gridDynMain execution", "fail", f"{type(exc).__name__}: {exc}", executable)],
                "unknowns": ["No recommendation was generated because execution failed."],
                "raw_output": traceback.format_exc(),
            }
        )
    return finalize_analysis(analysis)


def build_report_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# GridDyn interface analysis record",
        "",
        f"- Run ID: `{analysis['run_id']}`",
        f"- Created: `{analysis['created_at']}`",
        f"- Mode: `{analysis['mode']}`",
        f"- Status: `{analysis['status']}`",
        f"- Case: `{analysis['case_path'] or 'not supplied'}`",
        "",
        "## Summary",
        "",
        analysis["summary"],
        "",
        "## Metrics",
        "",
    ]
    if analysis["metrics"]:
        for metric in analysis["metrics"]:
            lines.append(f"- **{metric['label']}**: {metric['value']} ({metric['detail']})")
    else:
        lines.append("- No numerical metrics were mapped by the adapter.")
    lines.extend(["", "## Recommendations", ""])
    if analysis["recommendations"]:
        for item in analysis["recommendations"]:
            lines.append(f"- **{item['title']}**: {item['detail']}")
            lines.append(f"  - Basis: {', '.join(item.get('basis', [])) or 'not supplied'}")
            lines.append(f"  - Operator approval required: {item.get('approval_required', True)}")
    else:
        lines.append("- None generated.")
    lines.extend(["", "## Verification", ""])
    for item in analysis["checks"]:
        lines.append(f"- **{item['status'].upper()}** — {item['label']}: {item['detail']} [{item['source']}]")
    lines.extend(["", "## Unknowns", ""])
    for item in analysis["unknowns"] or ["None recorded."]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Safety boundary",
            "",
            "This interface is advisory. It does not issue breaker, generator, storage, or load-control commands.",
            "",
        ]
    )
    return "\n".join(lines)


def finalize_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    analysis["report_markdown"] = build_report_markdown(analysis)
    stem = f"analysis-{analysis['run_id']}"
    json_path = REPORT_DIR / f"{stem}.json"
    markdown_path = REPORT_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    markdown_path.write_text(analysis["report_markdown"], encoding="utf-8")
    analysis["report_json_path"] = str(json_path)
    analysis["report_markdown_path"] = str(markdown_path)
    return analysis


def resolve_case(payload: dict[str, Any]) -> Path | None:
    contents = payload.get("case_contents")
    if isinstance(contents, str) and contents:
        supplied_name = str(payload.get("case_name") or "uploaded_case.xml")
        safe_name = Path(supplied_name).name or "uploaded_case.xml"
        target = CASE_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}-{safe_name}"
        target.write_text(contents, encoding="utf-8")
        return target

    supplied_path = str(payload.get("case_path") or "").strip()
    if supplied_path:
        path = Path(os.path.expanduser(supplied_path)).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Case file does not exist: {path}")
        return path
    return None


def execute_request(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "auto")
    if mode == "demo":
        return build_demo_analysis()

    case_path = resolve_case(payload)
    if case_path is None:
        raise ValueError("Provide a GridDyn case file or select Demo mode.")

    tooling = detect_tooling()
    if mode == "auto":
        if tooling["griddyn_python"]:
            mode = "python"
        elif tooling["gridDynMain"]:
            mode = "cli"
        else:
            raise RuntimeError("GridDyn is not available. Install the Python package or add gridDynMain to PATH, then try again.")

    if mode == "python":
        if not tooling["griddyn_python"]:
            raise RuntimeError("The griddyn Python package is not available in this Python environment.")
        return run_grid_dyn_python(case_path)

    if mode == "cli":
        executable = tooling["gridDynMain"]
        if not executable:
            raise RuntimeError("gridDynMain was not found on PATH.")
        return run_grid_dyn_cli(case_path, executable)

    raise ValueError(f"Unknown run mode: {mode}")


class Handler(BaseHTTPRequestHandler):
    server_version = "GridDynInterface/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[grid-interface] " + (fmt % args) + "\n")

    def send_json(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json({"tooling": detect_tooling(), "server": "local-only"})
            return
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self.send_json({"error": "Request body is missing or too large."}, 413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object.")
            result = execute_request(payload)
            self.send_json({"analysis": result})
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local GridDyn operator interface.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; defaults to localhost.")
    parser.add_argument("--port", type=int, default=8765, help="Port; defaults to 8765.")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"GridDyn interface running at http://{args.host}:{args.port}")
    print(json.dumps(detect_tooling(), indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping GridDyn interface.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
