import {
  DashboardStats,
  Project,
  Investigation,
  InvestigationDetail,
  EvidenceRecord,
  Hypothesis,
  PatchProposal,
  ValidationRun,
  InvestigationReport,
  SettingsData,
  DemoScenario
} from '../types';

const API_BASE = '/api';

export async function fetchDashboardStats(): Promise<DashboardStats> {
  const res = await fetch(`${API_BASE}/dashboard/stats`);
  if (!res.ok) throw new Error('Failed to fetch dashboard stats');
  return res.json();
}

export async function fetchProjects(): Promise<Project[]> {
  const res = await fetch(`${API_BASE}/projects`);
  if (!res.ok) throw new Error('Failed to fetch projects');
  return res.json();
}

export async function fetchProject(id: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch project ${id}`);
  return res.json();
}

export async function createProject(data: FormData): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects`, {
    method: 'POST',
    body: data,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create project');
  }
  return res.json();
}

export async function uploadProjectFiles(projectId: string, files: File[]): Promise<any[]> {
  const formData = new FormData();
  files.forEach(f => formData.append('files', f));

  const res = await fetch(`${API_BASE}/projects/${projectId}/files`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to upload files');
  }
  return res.json();
}

export async function fetchInvestigations(): Promise<Investigation[]> {
  const res = await fetch(`${API_BASE}/investigations`);
  if (!res.ok) throw new Error('Failed to fetch investigations');
  return res.json();
}

export async function fetchInvestigationDetail(id: string): Promise<InvestigationDetail> {
  const res = await fetch(`${API_BASE}/investigations/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch investigation ${id}`);
  return res.json();
}

export async function createInvestigation(projectId: string, objective: string): Promise<Investigation> {
  const res = await fetch(`${API_BASE}/investigations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId, objective }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to start investigation');
  }
  return res.json();
}

export async function fetchEvidence(investigationId: string): Promise<EvidenceRecord[]> {
  const res = await fetch(`${API_BASE}/investigations/${investigationId}/evidence`);
  if (!res.ok) throw new Error('Failed to fetch evidence');
  return res.json();
}

export async function fetchHypotheses(investigationId: string): Promise<Hypothesis[]> {
  const res = await fetch(`${API_BASE}/investigations/${investigationId}/hypotheses`);
  if (!res.ok) throw new Error('Failed to fetch hypotheses');
  return res.json();
}

export async function fetchPatches(investigationId: string): Promise<PatchProposal[]> {
  const res = await fetch(`${API_BASE}/investigations/${investigationId}/patches`);
  if (!res.ok) throw new Error('Failed to fetch patches');
  return res.json();
}

export async function handlePatchAction(
  investigationId: string,
  patchId: string,
  action: 'approve' | 'reject',
  reason?: string
): Promise<PatchProposal> {
  const res = await fetch(`${API_BASE}/investigations/${investigationId}/patches/${patchId}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, reason }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to ${action} patch`);
  }
  return res.json();
}

export async function fetchValidationRuns(investigationId: string): Promise<ValidationRun[]> {
  const res = await fetch(`${API_BASE}/investigations/${investigationId}/validation`);
  if (!res.ok) throw new Error('Failed to fetch validation runs');
  return res.json();
}

export async function fetchReport(investigationId: string): Promise<InvestigationReport> {
  const res = await fetch(`${API_BASE}/investigations/${investigationId}/report`);
  if (!res.ok) throw new Error('Investigation report not available');
  return res.json();
}

export async function fetchSettings(): Promise<SettingsData> {
  const res = await fetch(`${API_BASE}/settings`);
  if (!res.ok) throw new Error('Failed to fetch settings');
  return res.json();
}

export async function updateSettings(data: Partial<SettingsData>): Promise<SettingsData> {
  const res = await fetch(`${API_BASE}/settings`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to update settings');
  return res.json();
}

export async function testConnectivity(): Promise<{ success: boolean; message: string; latency_ms: number; model: string }> {
  const res = await fetch(`${API_BASE}/settings/test-connectivity`, { method: 'POST' });
  if (!res.ok) throw new Error('Connectivity test failed');
  return res.json();
}

export async function fetchDemos(): Promise<DemoScenario[]> {
  const res = await fetch(`${API_BASE}/demos`);
  if (!res.ok) throw new Error('Failed to fetch demo scenarios');
  return res.json();
}

export async function loadDemoScenario(key: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/demos/${key}/load`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed to load demo scenario ${key}`);
  return res.json();
}
