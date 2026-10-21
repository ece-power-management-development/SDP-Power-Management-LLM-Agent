# SDP-Power-Management-LLM-Agent
1. Agentic Tool Calling (Dynamic Analysis)

This is the most robust method for querying grid states and running "what-if" scenarios. You leverage GridDyn's Python bindings to create callable functions, then provide those functions to the LLM as tools.

1. Action Wrappers: Write strict Python functions that wrap specific GridDyn operations (e.g.,
run_power_flow(), get_bus_voltage(), apply_fault()).

Tool Schema Definition: Provide the LLM with a JSON schema defining these functions, their parameters (e.g., bus_id: int ,fault_duration_ms: float), and expected outputs.

Execution Loop: When a user asks "What happens if we lose Generator 3?", the LLM generates a tool call. Your Python middleware executes the GridDyn command and returns the numerical transient stability results back to the LLM. The LLM then translates the raw data (e.g., "Hz dropped to 59.2") into a natural language summary.
 
2. Configuration & Topology Generator (System Setup)

If your goal is to have the LLM build or modify grid models rather than run them dynamically, use it to manipulate GridDyn's underlying configuration files.

RAG for Syntax: GridDyn uses specific data formats for network topologies. Feed the LLM documentation and examples of valid GridDyn configurations using Retrieval-Augmented Generation (RAG).
 
Prompting for Scenarios: Instruct the LLM to translate natural language (e.g., "Add a 50 MW solar plant at Bus 4") into the required configuration syntax.
 
Validation Pipeline: Never feed LLM-generated configurations directly into GridDyn. Pass the output through a separate parsing script to validate syntax and verify basic physics (e.g., ensuring total generation capacity can meet the load) before initializing the simulation.

3. HELICS Co-Simulation (Real-Time Control)

Because GridDyn was built to integrate seamlessly with the HELICS (Hierarchical Engine for Large-scale Infrastructure Co-Simulation) ecosystem, you can deploy the LLM as an independent control federate.

The LLM Federate: Wrap the LLM in a Python script configured as a HELICS federate.

Data Exchange: GridDyn runs the physics simulation and publishes grid state data (voltages, phase angles, line limits) to the HELICS broker at each time step.
 
Decision Making: The LLM federate subscribes to this data, uses it to generate operational decisions (e.g., dispatching generation, shedding load), and publishes setpoints back to GridDyn in simulated real-time.

4. Required Implementation Guardrails

LLMs will inevitably hallucinate physical parameters. Your middleware layer must enforce strict boundaries.

Type & Range Enforcement: Hardcode bounds in your Python middleware so the LLM cannot command a generator to output negative megawatts or exceed its physical thermal limits.
 
State Reset Protocols: Ensure your middleware creates a clean instance of the GridDyn model for each new LLM query. This prevents compounding errors where a hallucinated command from a previous turn breaks the current simulation.
 
Read-Only Defaults: Default all LLM permissions to read-only (querying states and topologies). Require manual human approval before executing any LLM-generated script that alters grid topology or simulation parameters.
