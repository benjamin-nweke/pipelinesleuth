import React from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchInvestigationDetail, fetchValidationRuns } from '../services/api';
import { InvestigationNav } from '../components/common/InvestigationNav';
import { StatusBadge } from '../components/common/StatusBadge';
import { 
  CheckCircle2, 
  XCircle, 
  Terminal, 
  HardDrive, 
  ShieldCheck, 
  Clock, 
  ArrowRight,
  AlertCircle
} from 'lucide-react';

export const ValidationPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();

  const { data: investigation } = useQuery({
    queryKey: ['investigation', id],
    queryFn: () => fetchInvestigationDetail(id!),
    enabled: !!id,
  });

  const { data: validationRuns, isLoading } = useQuery({
    queryKey: ['validationRuns', id],
    queryFn: () => fetchValidationRuns(id!),
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

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 w-full space-y-6">
        <div>
          <h1 className="text-xl font-bold text-charcoal-100 flex items-center space-x-2">
            <CheckCircle2 className="h-5 w-5 text-sleuth-amber-400" />
            <span>Isolated Validation Results</span>
          </h1>
          <p className="text-sm text-charcoal-300 mt-1">
            Genuine execution outputs and regression results captured from candidate workspace sandbox execution.
          </p>
        </div>

        {validationRuns && validationRuns.length > 0 ? (
          <div className="space-y-6">
            {validationRuns.map((run) => (
              <div
                key={run.id}
                className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-6 space-y-6"
              >
                {/* Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-charcoal-800 pb-4">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-3">
                      <span className="font-mono text-sm font-bold text-sleuth-amber-400">{run.id}</span>
                      <StatusBadge status={run.status} />
                      <span className="text-xs font-mono text-charcoal-400">
                        Exit code: {run.exit_code}
                      </span>
                    </div>
                    {run.patch_id && (
                      <div className="text-xs font-mono text-charcoal-400">
                        Validated Patch: <span className="text-charcoal-200">{run.patch_id}</span>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center space-x-4 text-xs font-mono text-charcoal-400">
                    <div className="flex items-center space-x-1">
                      <Clock className="h-3.5 w-3.5" />
                      <span>{run.execution_time_ms} ms</span>
                    </div>
                    <div>
                      {new Date(run.created_at).toLocaleTimeString()}
                    </div>
                  </div>
                </div>

                {/* Test Metrics / Summary */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 font-mono text-center">
                  <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-800">
                    <span className="text-[10px] text-charcoal-500 uppercase">Passed Tests</span>
                    <div className="text-xl font-bold text-emerald-400">{run.test_summary.passed || 0}</div>
                  </div>
                  <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-800">
                    <span className="text-[10px] text-charcoal-500 uppercase">Failed Tests</span>
                    <div className="text-xl font-bold text-rose-400">{run.test_summary.failed || 0}</div>
                  </div>
                  <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-800">
                    <span className="text-[10px] text-charcoal-500 uppercase">Errors</span>
                    <div className="text-xl font-bold text-amber-400">{run.test_summary.errors || 0}</div>
                  </div>
                  <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-800">
                    <span className="text-[10px] text-charcoal-500 uppercase">Total Tests</span>
                    <div className="text-xl font-bold text-charcoal-100">{run.test_summary.total || 0}</div>
                  </div>
                </div>

                {/* Console Output: stdout & stderr */}
                <div className="space-y-4">
                  {run.stdout && (
                    <div className="space-y-1.5">
                      <div className="flex items-center space-x-2 text-xs font-mono text-charcoal-400">
                        <Terminal className="h-3.5 w-3.5 text-emerald-400" />
                        <span>Standard Output (stdout):</span>
                      </div>
                      <pre className="bg-charcoal-950 border border-charcoal-800 rounded-lg p-4 font-mono text-xs text-charcoal-200 overflow-x-auto max-h-60 whitespace-pre-wrap leading-relaxed">
                        {run.stdout}
                      </pre>
                    </div>
                  )}

                  {run.stderr && (
                    <div className="space-y-1.5">
                      <div className="flex items-center space-x-2 text-xs font-mono text-charcoal-400">
                        <AlertCircle className="h-3.5 w-3.5 text-rose-400" />
                        <span>Standard Error (stderr):</span>
                      </div>
                      <pre className="bg-charcoal-950 border border-rose-900/40 rounded-lg p-4 font-mono text-xs text-rose-300 overflow-x-auto max-h-60 whitespace-pre-wrap leading-relaxed">
                        {run.stderr}
                      </pre>
                    </div>
                  )}
                </div>

                {/* Link to Final Report */}
                {run.status === 'passed' && (
                  <div className="pt-2 flex justify-end">
                    <Link
                      to={`/investigations/${investigation.id}/report`}
                      className="inline-flex items-center space-x-2 px-4 py-2 bg-sleuth-amber-500 hover:bg-sleuth-amber-600 text-charcoal-950 font-bold rounded-lg text-xs transition-colors"
                    >
                      <span>View Final Investigation Report</span>
                      <ArrowRight className="h-3.5 w-3.5" />
                    </Link>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="p-12 text-center text-charcoal-400 bg-charcoal-900 rounded-xl border border-charcoal-800 text-sm space-y-2">
            <ShieldCheck className="h-8 w-8 mx-auto text-charcoal-500" />
            <p className="text-charcoal-300 font-medium">No validation runs performed yet.</p>
            <p className="text-xs text-charcoal-500">
              When an approved patch is authorized in Patch Review, candidate execution results will display here.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
