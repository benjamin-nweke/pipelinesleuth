# PipelineSleuth Architecture

PipelineSleuth is an autonomous AI developer infrastructure system that investigates Python data pipeline failures, identifies evidence-backed root causes, formulates syntactically valid code corrections, and validates fixes in isolated sandboxes.

---

## 1. High-Level Subsystems

```mermaid
flowchart TD
    User["Developer / Data Engineer"] -->|HTTP / SSE| UI["Frontend Application (React + Vite + Tailwind)"]
    UI -->|REST / SSE API| API["Backend API Layer (FastAPI)"]
    
    subgraph Core Intelligence
        API --> Engine["AI Investigation Engine"]
        Engine --> Adapter["Provider Capability Adapter"]
        Adapter --> LLM["Ollama / OpenAI Model Provider"]
        Engine --> Registry["Diagnostic Tool Registry"]
    end

    subgraph Diagnostics
        Registry --> T1["inspect_project"]
        Registry --> T2["inspect_schema"]
        Registry --> T3["profile_dataset"]
        Registry --> T4["analyze_logs"]
        Registry --> T5["inspect_code (Python AST)"]
        Registry --> T6["trace_dependencies"]
        Registry --> T7["run_diagnostic"]
        Registry --> T8["test_hypothesis"]
        Registry --> T9["propose_patch"]
        Registry --> T10["validate_patch"]
    end

    subgraph Security & Execution
        T7 & T10 --> Sandbox["Execution Sandbox Manager"]
        Sandbox --> Docker["Restricted Docker Container (--network none)"]
    end

    subgraph Persistence
        API & Engine --> DB[("SQLite Database via SQLAlchemy Async")]
    end
```

---

## 2. Investigation State Machine

The investigation loop transitions through explicit stages:

```mermaid
stateDiagram-v2
    [*] --> ContextCollection
    ContextCollection --> ArtifactInspection
    ArtifactInspection --> FailureLocalization
    FailureLocalization --> HypothesisGeneration
    HypothesisGeneration --> DiagnosticSelection
    DiagnosticSelection --> EvidenceCollection
    EvidenceCollection --> HypothesisTesting
    HypothesisTesting --> RootCauseAssessment
    
    RootCauseAssessment --> PatchGeneration: Sufficient Evidence
    RootCauseAssessment --> Inconclusive: Inconclusive / Budget Limit
    HypothesisTesting --> DiagnosticSelection: Revise / More Evidence Needed
    
    PatchGeneration --> AwaitingApproval: Patch Formulated
    AwaitingApproval --> IsolatedValidation: User Approved
    AwaitingApproval --> Inconclusive: User Rejected
    
    IsolatedValidation --> RegressionTesting: Exit Code 0
    IsolatedValidation --> Inconclusive: Validation Failed
    RegressionTesting --> ReportGeneration
    ReportGeneration --> [*]
```

---

## 3. Database Schema

The relational SQLite schema maintains full traceability:
- **`projects`**: Project metadata, pipeline type, candidate entry point.
- **`project_files`**: Ingested files classified as code, data, log, config, or test.
- **`investigations`**: Session objective, current stage, status, confidence score, remaining budget.
- **`investigation_events`**: Chronological log of agent thoughts, stage transitions, tool calls, results.
- **`evidence_records`**: Traceable empirical facts tagged by file, line number, column, and verification status.
- **`hypotheses`**: Hypotheses formulated, status (`formulated`, `testing`, `confirmed`, `rejected`, `inconclusive`), and confidence.
- **`tool_executions`**: Audited diagnostic tool invocations with arguments, results, and execution durations.
- **`patch_proposals`**: Syntactically verified candidate patches with unified diffs and human approval status.
- **`validation_runs`**: Real exit codes, test summaries, stdout, and stderr from candidate executions.
- **`investigation_reports`**: Final synthesized reports ready for Markdown/HTML export.

