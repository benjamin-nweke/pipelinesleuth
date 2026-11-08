import React from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchInvestigationDetail, fetchReport } from '../services/api';
import { InvestigationNav } from '../components/common/InvestigationNav';
import { 
  FileText, 
  Download, 
  CheckCircle2, 
  AlertTriangle, 
  Layers, 
  Sparkles, 
  ShieldCheck,
  Code
} from 'lucide-react';

export const ReportPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();

  const { data: investigation } = useQuery({
    queryKey: ['investigation', id],
    queryFn: () => fetchInvestigationDetail(id!),
    enabled: !!id,
  });

  const { data: report, isLoading, isError } = useQuery({
    queryKey: ['report', id],
    queryFn: () => fetchReport(id!),
    enabled: !!id,
    refetchInterval: 3000,
  });

  if (!investigation) return null;

  return (
    <div className="min-h-screen flex flex-col bg-charcoal-950">
      <InvestigationNav
        investigationId={investigation.id}
        status={investigation.status}
        objective={investigation.objective}
        stage={investigation.stage}
        remainingBudget={investigation.remaining_budget}
      />

      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8 w-full space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-charcoal-100 flex items-center space-x-2">
              <FileText className="h-5 w-5 text-sleuth-amber-400" />
              <span>Forensic Investigation Report</span>
            </h1>
            <p className="text-sm text-charcoal-300 mt-1">
              Complete, evidence-backed diagnostic summary with traceable findings and validation results.
            </p>
          </div>

          {report && (
            <div className="flex items-center space-x-2 text-xs font-mono">
              <a
                href={`/api/reports/${report.id}/download/markdown`}
                download
                className="inline-flex items-center space-x-1.5 px-3 py-2 bg-charcoal-850 hover:bg-charcoal-800 border border-charcoal-700 text-charcoal-200 rounded-lg transition-colors"
              >
                <Download className="h-3.5 w-3.5 text-sleuth-amber-400" />
                <span>Export Markdown</span>
              </a>
              <a
                href={`/api/reports/${report.id}/download/html`}
                download
                className="inline-flex items-center space-x-1.5 px-3 py-2 bg-charcoal-850 hover:bg-charcoal-800 border border-charcoal-700 text-charcoal-200 rounded-lg transition-colors"
              >
                <Download className="h-3.5 w-3.5 text-emerald-400" />
                <span>Export HTML</span>
              </a>
            </div>
          )}
        </div>

        {report ? (
          <div className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-6 sm:p-8 space-y-8 font-sans">
            {/* Report Header */}
            <div className="border-b border-charcoal-800 pb-6 space-y-2">
              <div className="flex items-center space-x-3 text-xs font-mono">
                <span className="text-sleuth-amber-400 font-bold">{report.id}</span>
                <span className="text-charcoal-500">&bull;</span>
                <span className="text-charcoal-400">Generated: {new Date(report.created_at).toLocaleString()}</span>
              </div>
              <h2 className="text-2xl font-bold text-charcoal-100">{report.title}</h2>
              <div className="flex items-center space-x-2 pt-1 font-mono text-xs">
                <span className="px-2.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">
                  CONFIDENCE: {Math.round(investigation.confidence_score * 100)}%
                </span>
                <span className="px-2.5 py-0.5 rounded bg-charcoal-800 text-charcoal-300 border border-charcoal-700">
                  STATUS: {investigation.status.toUpperCase()}
                </span>
              </div>
            </div>

            {/* 1. Objective & Summary */}
            <div className="space-y-4">
              <div className="space-y-1">
                <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
                  1. Investigation Objective
                </h3>
                <p className="text-sm text-charcoal-200 leading-relaxed bg-charcoal-850 p-3 rounded-lg border border-charcoal-800 font-mono">
                  {investigation.objective}
                </p>
              </div>

              <div className="space-y-1">
                <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
                  2. Executive Summary
                </h3>
                <p className="text-sm text-charcoal-200 leading-relaxed">
                  {report.summary}
                </p>
              </div>
            </div>

            {/* 2. Root Cause Assessment */}
            <div className="bg-charcoal-850/80 rounded-xl border border-sleuth-amber-500/30 p-5 space-y-2">
              <div className="flex items-center space-x-2 text-xs font-mono text-sleuth-amber-400 font-bold uppercase">
                <Sparkles className="h-4 w-4" />
                <span>3. Root Cause Assessment (Evidence-Backed)</span>
              </div>
              <p className="text-sm text-charcoal-100 font-semibold leading-relaxed pt-1">
                {report.root_cause}
              </p>
              {investigation.confidence_justification && (
                <p className="text-xs text-charcoal-400 italic pt-1">
                  Justification: {investigation.confidence_justification}
                </p>
              )}
            </div>

            {/* 3. Affected Components */}
            <div className="space-y-2">
              <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
                4. Affected Pipeline Components
              </h3>
              <div className="flex flex-wrap gap-2">
                {report.affected_components.map((c, i) => (
                  <span
                    key={i}
                    className="px-3 py-1 rounded bg-charcoal-800 border border-charcoal-700 text-xs font-mono text-sky-300"
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>

            {/* 4. Supporting Evidence List */}
            <div className="space-y-3">
              <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
                5. Key Supporting Evidence Records
              </h3>
              <div className="space-y-2">
                {report.supporting_evidence.map((ev, i) => (
                  <div
                    key={i}
                    className="flex items-start space-x-3 p-3 rounded-lg bg-charcoal-850 border border-charcoal-800 text-xs"
                  >
                    <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                    <div>
                      <span className="font-mono text-sleuth-amber-400 font-semibold mr-2">[{ev.id}]</span>
                      <span className="font-mono uppercase text-charcoal-400 mr-2">({ev.type})</span>
                      <span className="text-charcoal-200">{ev.obs}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* 5. Hypotheses Evaluated vs Rejected */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-emerald-400">
                  Hypotheses Evaluated & Confirmed
                </h3>
                <div className="space-y-1.5">
                  {report.hypotheses_evaluated.map((h, i) => (
                    <div key={i} className="p-2.5 rounded bg-charcoal-850 border border-charcoal-800 text-xs text-charcoal-300">
                      <strong className="text-charcoal-100">{h.title}</strong>
                      <span className="ml-2 font-mono text-[10px] text-emerald-400">[{h.status}]</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-rose-400">
                  Hypotheses Contradicted / Rejected
                </h3>
                <div className="space-y-1.5">
                  {report.hypotheses_rejected.length > 0 ? (
                    report.hypotheses_rejected.map((h, i) => (
                      <div key={i} className="p-2.5 rounded bg-charcoal-850 border border-charcoal-800 text-xs text-charcoal-300">
                        <strong className="text-charcoal-100">{h.title}</strong>
                      </div>
                    ))
                  ) : (
                    <div className="text-xs text-charcoal-500 italic p-2.5">
                      No conflicting hypotheses recorded.
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* 6. Risks & Next Steps */}
            <div className="space-y-4 pt-4 border-t border-charcoal-800">
              {report.remaining_risks && (
                <div className="space-y-1">
                  <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
                    Remaining Operational Risks & Caveats
                  </h3>
                  <p className="text-xs text-charcoal-300 leading-relaxed bg-charcoal-850 p-3 rounded border border-charcoal-800">
                    {report.remaining_risks}
                  </p>
                </div>
              )}

              <div className="space-y-2">
                <h3 className="text-xs font-mono font-bold uppercase tracking-wider text-charcoal-400">
                  Recommended Next Steps
                </h3>
                <ol className="list-decimal list-inside space-y-1 text-xs text-charcoal-300 font-mono">
                  {report.next_steps.map((step, i) => (
                    <li key={i}>{step}</li>
                  ))}
                </ol>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-12 text-center text-charcoal-400 bg-charcoal-900 rounded-xl border border-charcoal-800 text-sm space-y-2">
            <FileText className="h-8 w-8 mx-auto text-charcoal-500" />
            <p className="text-charcoal-300 font-medium">Final report in preparation.</p>
            <p className="text-xs text-charcoal-500">
              The report will be automatically compiled once diagnostic investigations and validations complete.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
