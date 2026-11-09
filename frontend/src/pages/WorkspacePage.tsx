import React, { useEffect, useState, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchInvestigationDetail, fetchProject } from '../services/api';
import { InvestigationNav } from '../components/common/InvestigationNav';
import { StatusBadge } from '../components/common/StatusBadge';
import { 
  Activity, 
  Terminal, 
  FileCode, 
  Database, 
  FileText, 
  GitPullRequest, 
  ShieldAlert, 
  ArrowRight, 
  Clock, 
  Sparkles, 
  CheckCircle2, 
  XCircle,
  HelpCircle,
  ChevronRight,
  Layers,
  Wrench,
  Cpu
} from 'lucide-react';

export const WorkspacePage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const timelineEndRef = useRef<HTMLDivElement>(null);

  const { data: investigation, isLoading, isError } = useQuery({
    queryKey: ['investigation', id],
    queryFn: () => fetchInvestigationDetail(id!),
    enabled: !!id,
    refetchInterval: 3000,
  });

  const { data: project } = useQuery({
    queryKey: ['project', investigation?.project_id],
    queryFn: () => fetchProject(investigation!.project_id),
    enabled: !!investigation?.project_id,
  });

  // Server-Sent Events (SSE) live stream
  const [liveEvents, setLiveEvents] = useState<any[]>([]);

  useEffect(() => {
    if (!id) return;
    const eventSource = new EventSource(`/api/investigations/${id}/events/stream`);

    eventSource.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data);
        setLiveEvents((prev) => [...prev, parsed]);
        queryClient.invalidateQueries({ queryKey: ['investigation', id] });
      } catch (err) {
        // Heartbeat or ping
      }
    };

    eventSource.addEventListener('stage_change', () => {
      queryClient.invalidateQueries({ queryKey: ['investigation', id] });
    });

    eventSource.addEventListener('thought', (e: any) => {
      try {
        const parsed = JSON.parse(e.data);
        setLiveEvents((prev) => [...prev, parsed]);
      } catch (_) {}
    });

    eventSource.addEventListener('tool_call', () => {
      queryClient.invalidateQueries({ queryKey: ['investigation', id] });
    });

    eventSource.addEventListener('patch_generated', () => {
      queryClient.invalidateQueries({ queryKey: ['investigation', id] });
    });

    eventSource.addEventListener('validation_result', () => {
      queryClient.invalidateQueries({ queryKey: ['investigation', id] });
    });

    return () => {
      eventSource.close();
    };
  }, [id, queryClient]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-charcoal-950 text-charcoal-300 font-mono text-sm">
        <Activity className="h-5 w-5 animate-spin mr-2 text-sleuth-amber-400" />
        Loading investigation session...
      </div>
    );
  }

  if (isError || !investigation) {
    return (
      <div className="max-w-xl mx-auto mt-16 p-6 bg-charcoal-900 border border-rose-800 rounded-xl text-center space-y-4">
        <ShieldAlert className="h-10 w-10 text-rose-400 mx-auto" />
        <h2 className="text-lg font-semibold text-charcoal-100">Investigation Session Not Found</h2>
        <p className="text-sm text-charcoal-400">Could not retrieve investigation record #{id}.</p>
        <Link to="/" className="inline-block px-4 py-2 bg-charcoal-800 text-charcoal-200 rounded text-xs font-mono">
          Return to Dashboard
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-charcoal-950">
      <InvestigationNav
        investigationId={investigation.id}
        status={investigation.status}
        objective={investigation.objective}
        stage={investigation.stage}
        remainingBudget={investigation.remaining_budget}
      />

      {/* Pending Patch Approval Banner */}
      {investigation.status === 'awaiting_patch_approval' && (
        <div className="bg-orange-950/70 border-b border-orange-800/80 px-4 py-3">
          <div className="max-w-7xl mx-auto flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <GitPullRequest className="h-5 w-5 text-orange-400 animate-pulse" />
              <div>
                <span className="text-sm font-semibold text-orange-200">
                  Candidate Patch Ready for Human Review:
                </span>
                <span className="text-xs text-orange-300 ml-2">
                  The agent has formulated an evidence-backed correction. Human authorization required before validation.
                </span>
              </div>
            </div>
            <Link
              to={`/investigations/${investigation.id}/patches`}
              className="px-3 py-1.5 bg-orange-500 hover:bg-orange-600 text-charcoal-950 font-bold text-xs rounded transition-colors"
            >
              Review Patch &rarr;
            </Link>
          </div>
        </div>
      )}

      {/* 3-Panel Main Layout */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 w-full flex-1 grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* LEFT PANEL: Project Artifacts & Files (3 cols) */}
        <div className="lg:col-span-3 space-y-4">
          <div className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-4 space-y-3">
            <h2 className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400 flex items-center justify-between">
              <span>Project Artifacts</span>
              <span className="text-charcoal-500">{project?.files.length || 0}</span>
            </h2>

            <div className="space-y-1.5 overflow-y-auto max-h-[480px] pr-1">
              {project?.files.map((file) => (
                <div
                  key={file.id}
                  className="p-2.5 rounded bg-charcoal-850 hover:bg-charcoal-800 border border-charcoal-750/70 text-xs font-mono space-y-1 transition-colors"
                >
                  <div className="flex items-center space-x-2">
                    {file.file_type === 'code' && <FileCode className="h-3.5 w-3.5 text-sky-400 shrink-0" />}
                    {file.file_type === 'data' && <Database className="h-3.5 w-3.5 text-emerald-400 shrink-0" />}
                    {file.file_type === 'log' && <FileText className="h-3.5 w-3.5 text-rose-400 shrink-0" />}
                    {file.file_type === 'test' && <CheckCircle2 className="h-3.5 w-3.5 text-amber-400 shrink-0" />}
                    {file.file_type === 'config' && <Layers className="h-3.5 w-3.5 text-purple-400 shrink-0" />}
                    <span className="truncate text-charcoal-200 font-medium">{file.relative_path}</span>
                  </div>
                  <div className="flex justify-between text-[10px] text-charcoal-500 pl-5">
                    <span className="uppercase">{file.file_type}</span>
                    <span>{(file.size_bytes / 1024).toFixed(1)} KB</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Quick Actions / Links */}
          <div className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-4 space-y-2">
            <span className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
              Investigation Views
            </span>
            <div className="space-y-1 pt-1 text-xs">
              <Link
                to={`/investigations/${investigation.id}/evidence`}
                className="flex items-center justify-between p-2 rounded hover:bg-charcoal-850 text-charcoal-300 hover:text-charcoal-100 transition-colors"
              >
                <span>Evidence Explorer</span>
                <span className="font-mono text-sleuth-amber-400">{investigation.evidence_records.length} records</span>
              </Link>
              <Link
                to={`/investigations/${investigation.id}/patches`}
                className="flex items-center justify-between p-2 rounded hover:bg-charcoal-850 text-charcoal-300 hover:text-charcoal-100 transition-colors"
              >
                <span>Patch Proposals</span>
                <ChevronRight className="h-3.5 w-3.5 text-charcoal-500" />
              </Link>
              <Link
                to={`/investigations/${investigation.id}/validation`}
                className="flex items-center justify-between p-2 rounded hover:bg-charcoal-850 text-charcoal-300 hover:text-charcoal-100 transition-colors"
              >
                <span>Validation Runs</span>
                <ChevronRight className="h-3.5 w-3.5 text-charcoal-500" />
              </Link>
              <Link
                to={`/investigations/${investigation.id}/report`}
                className="flex items-center justify-between p-2 rounded hover:bg-charcoal-850 text-charcoal-300 hover:text-charcoal-100 transition-colors"
              >
                <span>Diagnostic Report</span>
                <ChevronRight className="h-3.5 w-3.5 text-charcoal-500" />
              </Link>
            </div>
          </div>
        </div>

        {/* MAIN PANEL: Live Investigation Timeline & Activity (6 cols) */}
        <div className="lg:col-span-6 space-y-4">
          <div className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-4 space-y-4">
            <div className="flex items-center justify-between border-b border-charcoal-800 pb-3">
              <div className="flex items-center space-x-2">
                <Terminal className="h-4 w-4 text-sleuth-amber-400" />
                <h2 className="text-sm font-semibold text-charcoal-100">Live Investigation Timeline</h2>
              </div>
              <div className="flex items-center space-x-2 text-xs font-mono text-charcoal-400">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></span>
                <span>Active Stream</span>
              </div>
            </div>

            {/* Event list */}
            <div className="space-y-3 max-h-[620px] overflow-y-auto pr-2">
              {investigation.events.map((ev) => (
                <div
                  key={ev.id}
                  className="rounded-lg p-3 bg-charcoal-850/80 border border-charcoal-750 text-xs font-mono space-y-1.5"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-2">
                      {ev.event_type === 'thought' && <Cpu className="h-3.5 w-3.5 text-sky-400" />}
                      {ev.event_type === 'tool_call' && <Wrench className="h-3.5 w-3.5 text-sleuth-amber-400" />}
                      {ev.event_type === 'tool_result' && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />}
                      {ev.event_type === 'evidence_found' && <Sparkles className="h-3.5 w-3.5 text-purple-400" />}
                      {ev.event_type === 'stage_change' && <Layers className="h-3.5 w-3.5 text-orange-400" />}
                      {ev.event_type === 'error' && <XCircle className="h-3.5 w-3.5 text-rose-400" />}
                      <span className="font-semibold text-charcoal-200">{ev.title}</span>
                    </div>
                    <span className="text-[10px] text-charcoal-500">
                      {new Date(ev.created_at).toLocaleTimeString()}
                    </span>
                  </div>

                  {/* Payload detail rendering */}
                  {ev.payload && (
                    <div className="bg-charcoal-900/90 rounded p-2 text-charcoal-300 font-mono text-[11px] leading-relaxed border border-charcoal-800 space-y-1">
                      {ev.payload.focus && (
                        <p className="text-sky-300"><span className="text-charcoal-400 font-semibold">Focus:</span> {ev.payload.focus}</p>
                      )}
                      {ev.payload.tool_reason && (
                        <p className="text-sleuth-amber-300"><span className="text-charcoal-400 font-semibold">Diagnostic Rationale:</span> {ev.payload.tool_reason}</p>
                      )}
                      {ev.payload.finding_summary && (
                        <p className="text-emerald-300"><span className="text-charcoal-400 font-semibold">Findings:</span> {ev.payload.finding_summary}</p>
                      )}
                      {ev.payload.hypothesis_impact && (
                        <p className="text-purple-300"><span className="text-charcoal-400 font-semibold">Hypothesis Impact:</span> {ev.payload.hypothesis_impact}</p>
                      )}
                      {ev.payload.next_steps && (
                        <p className="text-charcoal-300"><span className="text-charcoal-400 font-semibold">Next Step:</span> {ev.payload.next_steps}</p>
                      )}
                      {ev.payload.summary && (
                        <p className="text-emerald-300">{ev.payload.summary}</p>
                      )}
                      {ev.payload.observation && (
                        <p className="text-purple-300">Observation: {ev.payload.observation}</p>
                      )}
                      {ev.payload.error && (
                        <p className="text-rose-400">{ev.payload.error}</p>
                      )}
                      {ev.payload.arguments && (
                        <p className="text-charcoal-400">Args: {JSON.stringify(ev.payload.arguments)}</p>
                      )}
                    </div>
                  )}
                </div>
              ))}
              <div ref={timelineEndRef} />
            </div>
          </div>
        </div>

        {/* RIGHT PANEL: Root Cause Diagnosis & Hypotheses (3 cols) */}
        <div className="lg:col-span-3 space-y-4">
          {/* Diagnosis & Confidence Card */}
          <div className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-4 space-y-3">
            <span className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
              Current Diagnosis
            </span>

            {/* Confidence Gauge */}
            <div className="space-y-1.5">
              <div className="flex justify-between items-baseline font-mono text-xs">
                <span className="text-charcoal-300">Confidence</span>
                <span className="text-base font-bold text-sleuth-amber-400">
                  {Math.round(investigation.confidence_score * 100)}%
                </span>
              </div>
              <div className="w-full h-2 rounded-full bg-charcoal-800 overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-sleuth-amber-500 to-sleuth-orange-500 rounded-full transition-all duration-500"
                  style={{ width: `${Math.round(investigation.confidence_score * 100)}%` }}
                />
              </div>
            </div>

            {investigation.confidence_justification && (
              <p className="text-xs text-charcoal-400 italic leading-relaxed bg-charcoal-850 p-2.5 rounded border border-charcoal-800">
                "{investigation.confidence_justification}"
              </p>
            )}

            {investigation.root_cause_summary ? (
              <div className="pt-2 border-t border-charcoal-800 space-y-1">
                <span className="text-[11px] font-mono text-emerald-400 uppercase font-semibold">
                  Root Cause Identified:
                </span>
                <p className="text-xs text-charcoal-200 leading-relaxed font-medium">
                  {investigation.root_cause_summary}
                </p>
              </div>
            ) : (
              <div className="pt-2 border-t border-charcoal-800 text-xs text-charcoal-500 italic">
                Gathering diagnostic evidence to isolate root cause...
              </div>
            )}
          </div>

          {/* Active Hypotheses */}
          <div className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
                Hypotheses
              </span>
              <span className="text-xs font-mono text-charcoal-500">
                {investigation.hypotheses.length}
              </span>
            </div>

            <div className="space-y-2.5 max-h-[350px] overflow-y-auto pr-1">
              {investigation.hypotheses.map((h) => (
                <div
                  key={h.id}
                  className="p-3 rounded-lg bg-charcoal-850 border border-charcoal-750 text-xs space-y-1.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-[10px] text-sleuth-amber-400 font-bold">{h.id}</span>
                    <StatusBadge status={h.status} size="sm" />
                  </div>
                  <h4 className="font-medium text-charcoal-100 line-clamp-2">{h.title}</h4>
                  <p className="text-[11px] text-charcoal-400 leading-relaxed line-clamp-3">
                    {h.description}
                  </p>
                  <div className="flex items-center justify-between text-[10px] font-mono text-charcoal-500 pt-1">
                    <span>Confidence: {Math.round(h.confidence * 100)}%</span>
                  </div>
                </div>
              ))}
              {investigation.hypotheses.length === 0 && (
                <div className="text-xs text-charcoal-500 py-3 text-center">
                  Formulating initial hypotheses...
                </div>
              )}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};

