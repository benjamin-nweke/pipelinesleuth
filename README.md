# PipelineSleuth

> **Don't just detect pipeline failures. Investigate them.**

PipelineSleuth is an AI-powered developer infrastructure platform that autonomously investigates Python data pipeline failures, identifies evidence-backed root causes, proposes syntactically validated code corrections, and validates those corrections in an isolated execution sandbox.

---

## Key Product Capabilities

- **Autonomous Investigative Reasoning**: The AI engine inspects project artifacts, decides which diagnostic tools to call, interprets structured results, formulates and revises hypotheses, and determines when sufficient evidence exists.
- **Traceable Evidence Chain**: Every diagnosis is linked to concrete empirical evidence records (schema diffs, dataset profiles, log tracebacks, Python AST patterns). No fabricated line numbers or hallucinated causes.
- **Strict Human Control**: Original project files are never modified in-place. Patches require explicit human approval and are validated strictly in candidate workspace copies.
- **Contained Sandbox Execution**: Docker-based execution container with `--network none`, non-root user, memory/CPU quotas, and timeouts. Safe fallback policy that halts host execution if Docker is unavailable.
- **Real-Time Streaming**: Live investigation event timeline with Server-Sent Events (SSE).
- **Three Demonstration Scenarios**: Preconfigured, reproducible pipelines for Schema Drift, Data Quality Anomalies, and Type Mismatch failures.

---

## Architecture Overview

```
pipelinesleuth/
├── backend/
│   ├── app/
│   │   ├── agents/          # AI Investigation Engine, prompts, provider adapter
│   │   ├── api/             # FastAPI routers (dashboard, projects, investigations, demos, settings)
│   │   ├── core/            # Configuration and security settings
│   │   ├── database/        # Async SQLAlchemy session and SQLite persistence
│   │   ├── models/          # Relational ORM models (investigations, evidence, hypotheses, patches)
│   │   ├── sandbox/         # Docker container manager and workspace isolation
│   │   ├── schemas/         # Pydantic validation models
│   │   ├── services/        # Lifecycle services and SSE event broadcaster
│   │   └── tools/           # 10 diagnostic tools (inspect_project, inspect_schema, AST, etc.)
│   └── tests/               # Unit, integration, and E2E test suite (pytest)
├── frontend/                # React 18, TypeScript, Vite, Tailwind CSS, TanStack Query
├── demo/                    # 3 Reproducible test scenarios (code, data, logs, tests)
│   ├── schema_drift/
│   ├── data_quality/
│   └── type_mismatch/
├── docker/                  # Dockerfiles for sandbox and services
├── docs/                    # Architecture, security, and demo walkthrough documentation
├── scripts/                 # Setup, start, and test automation scripts
└── storage/                 # Persistent SQLite DB, uploads, and temporary workspaces
```

---

## System Requirements

- **Operating System**: macOS, Linux, or Windows (WSL2)
- **Python**: 3.11+ (Python 3.12 supported)
- **Node.js**: v18+ (Node v20+ recommended)
- **Docker**: Docker Desktop or Docker Engine (required for containerized patch validation)
- **AI Model Provider**:
  - Local: [Ollama](https://ollama.com/) with `llama3.1:8b` (or `mistral:7b`, `qwen2.5:7b`)
  - Remote: Any OpenAI-compatible API endpoint (GPT-4o, vLLM, etc.)

---

## Quickstart Guide

### 1. Initial Setup
Run the automated setup script to configure Python virtual environment, install dependencies, and build the frontend:
```bash
./scripts/setup.sh
```

Or manually:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt greenlet
npm install --legacy-peer-deps --prefix frontend
npm run build --prefix frontend
```

### 2. Configure AI Model Provider

#### Option A: Local Ollama (Recommended for offline developer workflows)
1. Install Ollama from [ollama.com](https://ollama.com).
2. Pull the model:
   ```bash
   ollama pull llama3.1:8b
   ```
3. Start the Ollama daemon:
   ```bash
   ollama serve
   ```

#### Option B: OpenAI-Compatible Provider
Configure `.env` or navigate to `/settings` in the UI to set your API endpoint and key:
```env
AI_PROVIDER=openai
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
```

### 3. Start PipelineSleuth
Launch the unified server (serves both API and Web UI on port 8000):
```bash
./scripts/start.sh
```

Open your browser to:
[http://localhost:8000](http://localhost:8000)

*(Optional for frontend developers)* To run Vite with hot-module replacement:
```bash
cd frontend && npm run dev
# Vite dev server available at http://localhost:5173
```

---

## Running Demo Scenarios

PipelineSleuth includes three reproducible failure scenarios ready for one-click demonstration:

1. Open the Dashboard at [http://localhost:8000](http://localhost:8000).
2. In the **Reproducible Test Scenarios** section, choose one of:
   - **Scenario A: Schema Drift**: Column `customer_id` was renamed upstream to `client_id`, triggering a KeyError during aggregation.
   - **Scenario B: Data Quality Failure**: Unexpected null values in `amount` cause a strict financial ledger check to halt.
   - **Scenario C: Type Mismatch**: Non-numeric strings (`'pending'`, `'N/A'`) in transactional amounts crash float conversion.
3. Click **Investigate**.
4. Watch the autonomous investigation loop unfold in real time:
   - Diagnostic tools inspect project files, parse stack traces, and profile datasets.
   - Hypotheses are formulated, tested against evidence, and revised if contradicted.
   - The agent reaches an evidence-backed root cause and synthesizes a syntactically valid code patch.
5. In **Patch Review**, examine the unified diff.
6. Click **Approve & Validate Fix** to run the fix in a temporary, isolated candidate container.
7. Export the final forensic report to **Markdown** or **HTML**.

---

## Running Automated Tests

Run the full pytest suite (18 unit, persistence, API, and E2E integration tests):
```bash
./scripts/run_tests.sh
```

Or directly:
```bash
PYTHONPATH=. .venv/bin/pytest backend/tests -v
```

---

## Diagnostic Tool Registry

The AI agent interacts with the codebase strictly through registered diagnostic tools:
1. `inspect_project`: File inventory, entry points, stages, configs, and tests.
2. `inspect_schema`: Column diffs, renames, type mismatches, and unexpected nullability.
3. `profile_dataset`: Row/col counts, distributions, null percentages, and dirty strings.
4. `analyze_logs`: Extracts Python exception types, tracebacks, line numbers, and stages.
5. `inspect_code`: Python AST static analysis (functions, data loaders, column lookups, joins, aggregations).
6. `trace_dependencies`: Data lineage and stage dependency graph.
7. `run_diagnostic`: Controlled diagnostic executions inside the sandbox.
8. `test_hypothesis`: Synthesizes evidence supporting vs. contradicting a theory.
9. `propose_patch`: Generates syntactically validated candidate diffs without touching original files.
10. `validate_patch`: Runs approved candidate fixes in an isolated scratch workspace.

---

## Known Limitations

- **Docker Requirement for Code Execution**: Sandboxed execution and candidate patch validation require a local Docker daemon. When Docker is not installed or running, diagnostic tools like schema inspection, AST code parsing, log analysis, and patch generation function normally, while containerized runs are clearly flagged as unavailable. Unsafe direct host execution is deliberately prohibited.
- **Single-Threaded SQLite Concurrency**: SQLite with `aiosqlite` is used for out-of-the-box local persistence without needing Postgres. For high-concurrency multi-tenant deployments, configure `DATABASE_URL` to PostgreSQL.

---

## License

Apache 2.0. Built for developers and data engineering teams who investigate pipeline failures.
