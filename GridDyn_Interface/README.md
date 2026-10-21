# GridDyn Interface

This is a local, advisory-only browser interface for connecting a computer to GridDyn. It provides:

- automatic detection of the GridDyn Python package, `gridDynMain`, and HELICS;
- GridDyn case upload or local-path selection;
- Python-package and command-line execution adapters;
- preserved raw output and verification notes;
- synthetic demo mode when GridDyn is not installed;
- human-readable Markdown and JSON analysis records in `reports/`.

## Run it

From this directory:

```bash
python app.py
```

Open `http://127.0.0.1:8765` in a browser. The server binds to localhost by default.

## Connect GridDyn

The interface uses the first available option:

1. Python package: `import griddyn` and `griddyn.load(case_path)`.
2. Command line: `gridDynMain case_path` with `gridDynMain` available on `PATH`.

Because GridDyn case formats and result fields may vary by build, the adapter intentionally does not guess voltage, flow, frequency, or failure metrics from arbitrary output. Add a case-specific result mapper in `app.py` before using numerical results for operational decisions.

## Safety boundary

This prototype does not issue breaker, generator, storage, demand-response, or load-control commands. Recommendations are not generated autonomously. Use a validated optimizer and independent physics/constraint checks before connecting it to an operational workflow.
