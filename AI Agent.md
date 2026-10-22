GridDyn/HELICS project, use a local model runtime plus an agent framework.

| Option | Best use |
|---|---|
| Ollama + PydanticAI | Best starting choice for your Python gateway. Ollama runs models locally, and PydanticAI supports structured output through local Ollama models. [Ollama](https://docs.ollama.com/index?utm_source=chatgpt.com) |
| LM Studio + PydanticAI | Easiest graphical setup on Windows. It provides a local API server and can operate offline. [LM Studio](https://lmstudio.ai/docs/app/offline?utm_source=chatgpt.com) |
| llama.cpp + PydanticAI | Best for maximum offline control, CPU/GPU flexibility, JSON schemas, and tool calling. [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md?utm_source=chatgpt.com) |
| vLLM + LangGraph | Best for a powerful GPU workstation or multiple simultaneous users. vLLM provides an OpenAI-compatible local server; LangGraph manages stateful agent workflows. [vLLM](https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server/?utm_source=chatgpt.com) |
| LlamaIndex + Ollama | Best if the agent must search local documents, operating procedures, or GridDyn reports. [LlamaIndex Python Documentation](https://llamaindex.openml.io/python/framework/understanding/using_llms/?utm_source=chatgpt.com) |

My recommendation for your project:

```text
Ollama
   ↓
PydanticAI agent
   ↓
HELICS/GridDyn context at localhost:8766
   ↓
Structured response with evidence_ids
   ↓
Review validator at localhost:8765
   ↓
Human-readable review console
```

GridDyn_Interface.zip[Download the updated package](sandbox:/workspace/scratch/6abf635dc3bb/GridDyn_Interface.zip)

Individual files:

- :chatgpt-content-reference{index="4"}[install_local_agent.ps1](sandbox:/workspace/scratch/6abf635dc3bb/grid-interface/install_local_agent.ps1)
- :chatgpt-content-reference{index="5"}[install_local_agent.sh](sandbox:/workspace/scratch/6abf635dc3bb/grid-interface/install_local_agent.sh)
- :chatgpt-content-reference{index="6"}[local_agent.py](sandbox:/workspace/scratch/6abf635dc3bb/grid-interface/local_agent.py)
- :chatgpt-content-reference{index="7"}[run_local_agent.ps1](sandbox:/workspace/scratch/6abf635dc3bb/grid-interface/run_local_agent.ps1)

The installer sets up:

```text
Ollama
PydanticAI
Qwen3 local model
GridDyn/HELICS gateway
AI Agent
Hallucination review console
Human recheck workflow
```

Ollama runs locally on port `11434`, and PydanticAI uses its local OpenAI-compatible endpoint with structured output. [GitHub](https://github.com/pydantic/pydantic-ai/blob/main/docs/models/ollama.md?utm_source=chatgpt.com) The default model is `qwen3:8b`, which is available in Ollama’s official model library. [ollama.com](https://ollama.com/library/qwen3?utm_source=chatgpt.com)

On Windows PowerShell:

```powershell
cd path\to\grid-interface
Set-ExecutionPolicy -Scope Process Bypass
.\install_local_agent.ps1
.\run_local_agent.ps1 -Demo
```

For live HELICS data:

```powershell
.\run_local_agent.ps1 -LiveHelics
```

For a smaller model:

```powershell
.\install_local_agent.ps1 -Model qwen3:4b
.\run_local_agent.ps1 -Model qwen3:4b -Demo
```

On Linux/macOS:

```bash
cd path/to/grid-interface
./install_local_agent.sh
./run_local_agent.sh --demo
```

The initial installation requires Internet access to download Ollama, Python dependencies, and the model. After installation, the AI Agent uses only local services. The runner also rejects non-local URLs to prevent accidental cloud use.

The implementation passed 15 tests, Python validation, shell-script validation, and ZIP integrity checks.
Use Ollama with a local instruction-tuned model, then require PydanticAI to return the structured format already defined in your project. The AI Agent should never validate itself; keep the deterministic evidence checker and independent GridDyn/physics validation.

Offline operation requires downloading the software, model files, and dependencies first. After that, disconnect the computer or block external network access. Do not use cloud model names or external tools.
