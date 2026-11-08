# PipelineSleuth Security Architecture & Policies

PipelineSleuth treats all uploaded project files, logs, and datasets as untrusted user inputs. The system implements defense-in-depth measures across ingestion, static analysis, model interaction, and execution.

---

## 1. Non-Negotiable Human Control Principles
1. **Zero Silent Modification**: Original uploaded files are never overwritten or patched in-place.
2. **Explicit Approval Boundary**: Patches cannot be validated or executed without human review and approval.
3. **Workspace Isolation**: Candidate patches are copied into temporary, isolated scratch workspaces (`storage/workspaces/cand_*`) and safely pruned upon completion.

---

## 2. Ingestion & File Handling Security
- **Path Traversal Prevention**: Filenames are strictly sanitized with `Path(name).name`. Target paths are resolved and verified against root project boundaries using `str(resolved).startswith(str(base))`.
- **Extension Allowlist**: Only explicit extensions (`.py`, `.csv`, `.parquet`, `.json`, `.log`, `.txt`, `.yaml`, `.yml`) are accepted. Binaries and executables (`.exe`, `.dll`, `.so`, `.sh`) are rejected.
- **Upload Size Quota**: File uploads are capped at 50MB per file to mitigate denial-of-service risks.

---

## 3. Static Analysis vs. Runtime Execution
- Source code analysis is conducted purely via **Python AST parsing (`ast.parse`)** without importing or running untrusted code.
- Dataset schemas and distributions are computed via read-only Pandas / PyArrow profiling with bounded row sampling.

---

## 4. Container Sandbox Containment
When code must be executed for diagnostics or validation:
- **Docker Isolation**: Code runs exclusively inside an isolated Docker container (`python:3.11-slim`).
- **Network Isolation**: Strict `--network none` flag blocks all outbound and inbound socket access.
- **Non-Root Execution**: Runs under unprivileged user `sleuthuser` (UID 1000).
- **Resource Constraints**: Capped memory (`--memory 1g`) and CPU quota (`--cpus 1.0`).
- **Execution Timeouts**: Processes are strictly terminated after 60 seconds to prevent infinite loops.
- **No Unsafe Host Fallback**: If Docker is not running or unavailable, the sandbox manager explicitly **refuses host execution**, returning a clear security notice and instructions to start Docker.

---

## 5. Model Provider Security
- **No Prompt Injection Execution**: System instructions instruct the agent to treat data files and log traces as data, never as commands.
- **Structured JSON Schema**: Model outputs are strictly validated through Pydantic (`AgentDecision`). Unrecognized tool names or arbitrary shell commands are rejected by the Tool Registry.
- **Masked Credentials**: API keys are masked before persisting or sending to the frontend UI.
