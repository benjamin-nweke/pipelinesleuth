export type InvestigationStatus = 
  | 'created' 
  | 'investigating' 
  | 'awaiting_patch_approval' 
  | 'validating' 
  | 'completed' 
  | 'inconclusive' 
  | 'failed';

export type InvestigationStage =
  | 'context_collection'
  | 'artifact_inspection'
  | 'failure_localization'
  | 'hypothesis_generation'
  | 'diagnostic_selection'
  | 'evidence_collection'
  | 'hypothesis_testing'
  | 'root_cause_assessment'
  | 'patch_generation'
  | 'awaiting_approval'
  | 'validation'
  | 'regression_testing'
  | 'report_generation';

export interface ProjectFile {
  id: string;
  project_id: string;
  relative_path: string;
  file_type: 'code' | 'data' | 'log' | 'config' | 'test';
  size_bytes: number;
  storage_path: string;
  metadata_json: Record<string, any>;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  pipeline_type: string;
  entry_point?: string;
  created_at: string;
  updated_at: string;
  files: ProjectFile[];
}

export interface InvestigationEvent {
  id: string;
  investigation_id: string;
  event_type: string;
  title: string;
  payload: Record<string, any>;
  created_at: string;
}

export interface EvidenceRecord {
  id: string;
  investigation_id: string;
  source_file: string;
  evidence_type: string;
  line_number?: number;
  column_name?: string;
  execution_stage?: string;
  observation: string;
  associated_hypothesis_id?: string;
  verification_status: string;
  timestamp: string;
}

export interface Hypothesis {
  id: string;
  investigation_id: string;
  title: string;
  description: string;
  status: 'formulated' | 'testing' | 'confirmed' | 'rejected' | 'inconclusive';
  confidence: number;
  reasoning?: string;
  created_at: string;
  updated_at: string;
}

export interface ToolExecution {
  id: string;
  investigation_id: string;
  tool_name: string;
  input_arguments: Record<string, any>;
  output_result: Record<string, any>;
  error_message?: string;
  execution_time_ms: number;
  status: 'success' | 'error' | 'timeout';
  created_at: string;
}

export interface ValidationRun {
  id: string;
  investigation_id: string;
  patch_id?: string;
  status: 'passed' | 'failed' | 'error' | 'skipped';
  exit_code: number;
  stdout: string;
  stderr: string;
  test_summary: {
    passed?: number;
    failed?: number;
    errors?: number;
    total?: number;
    details?: string[];
  };
  regression_results: Record<string, any>;
  execution_time_ms: number;
  created_at: string;
}

export interface PatchProposal {
  id: string;
  investigation_id: string;
  target_file: string;
  original_code_context: string;
  proposed_code: string;
  unified_diff: string;
  explanation: string;
  supporting_evidence_ids: string[];
  expected_impact?: string;
  potential_risks?: string;
  status: 'proposed' | 'approved' | 'rejected' | 'validated' | 'validation_failed';
  created_at: string;
  validation_runs: ValidationRun[];
}

export interface InvestigationReport {
  id: string;
  investigation_id: string;
  title: string;
  summary: string;
  root_cause: string;
  affected_components: string[];
  supporting_evidence: Array<{ id: string; type: string; obs: string }>;
  hypotheses_evaluated: Array<{ id: string; title: string; status: string }>;
  hypotheses_rejected: Array<{ id: string; title: string }>;
  proposed_correction: Record<string, any>;
  validation_summary: Record<string, any>;
  remaining_risks?: string;
  next_steps: string[];
  markdown_content: string;
  created_at: string;
}

export interface Investigation {
  id: string;
  project_id: string;
  objective: string;
  status: InvestigationStatus;
  stage: InvestigationStage;
  confidence_score: number;
  confidence_justification?: string;
  remaining_budget: number;
  root_cause_summary?: string;
  created_at: string;
  updated_at: string;
}

export interface InvestigationDetail extends Investigation {
  events: InvestigationEvent[];
  evidence_records: EvidenceRecord[];
  hypotheses: Hypothesis[];
  tool_executions: ToolExecution[];
}

export interface DashboardStats {
  metrics: {
    total_investigations: number;
    active_investigations: number;
    confirmed_diagnoses: number;
    validated_fixes: number;
  };
  status_distribution: Record<string, number>;
  recent_investigations: Array<{
    id: string;
    project_id: string;
    objective: string;
    status: InvestigationStatus;
    stage: InvestigationStage;
    confidence_score: number;
    created_at: string;
  }>;
  recent_projects: Array<{
    id: string;
    name: string;
    pipeline_type: string;
    entry_point?: string;
    created_at: string;
  }>;
  recent_events: Array<{
    id: string;
    investigation_id: string;
    event_type: string;
    title: string;
    created_at: string;
  }>;
}

export interface SettingsData {
  ai_provider: string;
  ollama_base_url: string;
  ollama_model: string;
  openai_base_url: string;
  openai_model: string;
  has_openai_key: boolean;
  max_tool_calls: number;
  sandbox_timeout_seconds: number;
  docker_available: boolean;
  extra_config: Record<string, any>;
  updated_at: string;
}

export interface DemoScenario {
  key: string;
  name: string;
  description: string;
  directory: string;
  pipeline_type: string;
  entry_point: string;
  default_objective: string;
}
