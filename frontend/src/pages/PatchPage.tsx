import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchInvestigationDetail, fetchPatches, handlePatchAction } from '../services/api';
import { InvestigationNav } from '../components/common/InvestigationNav';
import { StatusBadge } from '../components/common/StatusBadge';
import { DiffViewer } from '../components/common/DiffViewer';
import { 
  GitPullRequest, 
  Check, 
  X, 
  AlertTriangle, 
  ShieldAlert, 
  Sparkles, 
  FileCode,
  ArrowRight,
  Loader2
} from 'lucide-react';

export const PatchPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [rejectReason, setRejectReason] = useState('');
  const [showRejectModal, setShowRejectModal] = useState<string | null>(null);

  const { data: investigation } = useQuery({
    queryKey: ['investigation', id],
    queryFn: () => fetchInvestigationDetail(id!),
    enabled: !!id,
  });

  const { data: patches, isLoading } = useQuery({
    queryKey: ['patches', id],
    queryFn: () => fetchPatches(id!),
    enabled: !!id,
    refetchInterval: 3000,
  });

  const actionMutation = useMutation({
    mutationFn: async ({ patchId, action, reason }: { patchId: string; action: 'approve' | 'reject'; reason?: string }) => {
      return handlePatchAction(id!, patchId, action, reason);
    },
    onSuccess: (updatedPatch, variables) => {
      queryClient.invalidateQueries({ queryKey: ['patches', id] });
      queryClient.invalidateQueries({ queryKey: ['investigation', id] });
      setShowRejectModal(null);
      setRejectReason('');
      if (variables.action === 'approve') {
        navigate(`/investigations/${id}/validation`);
      }
    },
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
            <GitPullRequest className="h-5 w-5 text-sleuth-amber-400" />
            <span>Candidate Patch Review</span>
          </h1>
          <p className="text-sm text-charcoal-300 mt-1">
            Human authorization required. Approved patches are validated inside an isolated sandbox copy without modifying original files.
          </p>
        </div>

        {patches && patches.length > 0 ? (
          <div className="space-y-6">
            {patches.map((patch) => (
              <div
                key={patch.id}
                className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-6 space-y-5"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-charcoal-800 pb-4">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-3">
                      <span className="font-mono text-sm font-bold text-sleuth-amber-400">
                        {patch.id}
                      </span>
                      <StatusBadge status={patch.status} />
                    </div>
                    <div className="text-xs font-mono text-charcoal-400 flex items-center space-x-1">
                      <FileCode className="h-3.5 w-3.5 text-sky-400" />
                      <span className="text-charcoal-200">{patch.target_file}</span>
                    </div>
                  </div>

                  {/* Actions for proposed patch */}
                  {patch.status === 'proposed' && (
                    <div className="flex items-center space-x-3">
                      <button
                        onClick={() => setShowRejectModal(patch.id)}
                        disabled={actionMutation.isPending}
                        className="px-4 py-2 bg-charcoal-800 hover:bg-rose-950/40 text-charcoal-300 hover:text-rose-400 border border-charcoal-700 hover:border-rose-800 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-colors disabled:opacity-50"
                      >
                        <X className="h-3.5 w-3.5" />
                        <span>Reject Patch</span>
                      </button>

                      <button
                        onClick={() => actionMutation.mutate({ patchId: patch.id, action: 'approve' })}
                        disabled={actionMutation.isPending}
                        className="px-5 py-2 bg-emerald-500 hover:bg-emerald-600 text-charcoal-950 rounded-lg text-xs font-bold flex items-center space-x-1.5 shadow-sm shadow-emerald-500/20 transition-all disabled:opacity-50"
                      >
                        {actionMutation.isPending ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Check className="h-3.5 w-3.5 stroke-[3]" />
                        )}
                        <span>Approve & Validate Fix</span>
                      </button>
                    </div>
                  )}

                  {patch.status === 'validated' && (
                    <div className="text-xs font-mono text-emerald-400 font-semibold bg-emerald-950/40 px-3 py-1.5 rounded border border-emerald-800/60 flex items-center space-x-1.5">
                      <Check className="h-4 w-4" />
                      <span>Validated in Sandbox (Passed)</span>
                    </div>
                  )}
                </div>

                {/* Explanation & Impact */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs leading-relaxed">
                  <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-800 space-y-1">
                    <span className="font-mono text-sleuth-amber-400 font-bold uppercase text-[10px]">
                      Explanation & Rationale:
                    </span>
                    <p className="text-charcoal-200 pt-1">{patch.explanation}</p>
                  </div>

                  <div className="bg-charcoal-850 p-4 rounded-lg border border-charcoal-800 space-y-1">
                    <span className="font-mono text-sky-400 font-bold uppercase text-[10px]">
                      Expected Impact & Potential Risks:
                    </span>
                    <p className="text-charcoal-200 pt-1">
                      <strong className="text-charcoal-100">Impact:</strong> {patch.expected_impact || 'Fixes failure'}
                    </p>
                    {patch.potential_risks && (
                      <p className="text-charcoal-400 pt-1">
                        <strong className="text-charcoal-300">Risks:</strong> {patch.potential_risks}
                      </p>
                    )}
                  </div>
                </div>

                {/* Unified Diff */}
                <div className="space-y-2">
                  <span className="text-xs font-mono uppercase text-charcoal-400 font-bold">
                    Unified Code Diff:
                  </span>
                  <DiffViewer diff={patch.unified_diff} filename={patch.target_file} />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-12 text-center text-charcoal-400 bg-charcoal-900 rounded-xl border border-charcoal-800 text-sm space-y-2">
            <GitPullRequest className="h-8 w-8 mx-auto text-charcoal-500" />
            <p className="text-charcoal-300 font-medium">No patch proposals formulated yet.</p>
            <p className="text-xs text-charcoal-500">
              The agent will synthesize and propose a patch once it verifies a confirmed root cause.
            </p>
          </div>
        )}

        {/* Reject Confirmation Modal */}
        {showRejectModal && (
          <div className="fixed inset-0 bg-charcoal-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="bg-charcoal-900 border border-charcoal-700 rounded-xl max-w-md w-full p-6 space-y-4 shadow-2xl">
              <h3 className="text-base font-bold text-charcoal-100 flex items-center space-x-2">
                <AlertTriangle className="h-5 w-5 text-rose-400" />
                <span>Reject Proposed Patch</span>
              </h3>
              <p className="text-xs text-charcoal-300">
                Rejecting this patch prevents it from being applied or executed. Please supply an optional reason:
              </p>
              <textarea
                rows={3}
                placeholder="e.g. Incompatible with downstream schema or violates data privacy rule..."
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg p-2.5 text-xs text-charcoal-100 focus:outline-none focus:border-rose-500"
              />
              <div className="flex justify-end space-x-3 pt-2">
                <button
                  onClick={() => setShowRejectModal(null)}
                  className="px-4 py-2 bg-charcoal-800 hover:bg-charcoal-750 text-charcoal-300 rounded text-xs font-semibold"
                >
                  Cancel
                </button>
                <button
                  onClick={() => actionMutation.mutate({ patchId: showRejectModal, action: 'reject', reason: rejectReason })}
                  className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded text-xs font-bold"
                >
                  Confirm Rejection
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
