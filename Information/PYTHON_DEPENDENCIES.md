# Python dependencies

The project is split into installable profiles so a demo or review-only
deployment does not need every optional component.

| Profile | Requirement file | Direct Python dependencies | Used for |
| --- | --- | --- | --- |
| Core | `requirements-core.txt` | None; Python standard library only | GridDyn adapter UI, review console, schemas, tests |
| HELICS | `requirements-helics.txt` | `helics` | Live HELICS federate access in `agent_gateway.py` |
| Local agent | `requirements-local-agent.txt` | `pydantic-ai-slim[openai]` | Local Ollama model, PydanticAI, and schema-constrained recommendations |
| All | `requirements-all.txt` | HELICS + local-agent profile | Complete live workflow |

`pip` installs the transitive dependencies of the direct packages. The exact
wheel files selected for a machine are recorded by
`download_python_dependencies.py` in the generated wheelhouse manifest.

GridDyn itself is not pinned as a PyPI dependency because GridDyn builds,
case formats, and Python bindings vary by installation. The interface detects
an installed `griddyn` module or a `gridDynMain` executable when available.

Ollama is a local model service, not a Python dependency. Its model files are
downloaded separately by the installer or by the Ollama CLI.
