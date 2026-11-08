import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchInvestigationDetail, fetchEvidence } from '../services/api';
import { InvestigationNav } from '../components/common/InvestigationNav';
import { StatusBadge } from '../components/common/StatusBadge';
import { 
  FileSearch, 
  Filter, 
  Database, 
  FileCode, 
  FileText, 
  Sparkles, 
  ArrowRight,
  ExternalLink
} from 'lucide-react';

export const EvidencePage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [filterType, setFilterType] = useState<string>('all');

  const { data: investigation } = useQuery({
    queryKey: ['investigation', id],
    queryFn: () => fetchInvestigationDetail(id!),
    enabled: !!id,
  });

  const { data: evidenceList } = useQuery({
    queryKey: ['evidence', id],
    queryFn: () => fetchEvidence(id!),
    enabled: !!id,
  });

  const filtered = evidenceList?.filter(e => {
    if (filterType === 'all') return true;
    return e.evidence_type === filterType;
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
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-charcoal-100 flex items-center space-x-2">
              <FileSearch className="h-5 w-5 text-sleuth-amber-400" />
              <span>Evidence Explorer</span>
            </h1>
            <p className="text-sm text-charcoal-300 mt-1">
              Traceable factual observations collected by diagnostic tools and AST static analysis.
            </p>
          </div>

          {/* Filter pills */}
          <div className="flex items-center space-x-2 text-xs font-mono overflow-x-auto pb-1">
            <Filter className="h-3.5 w-3.5 text-charcoal-400" />
            <button
              onClick={() => setFilterType('all')}
              className={`px-3 py-1 rounded-full border transition-colors ${
                filterType === 'all'
                  ? 'bg-sleuth-amber-500/20 text-sleuth-amber-300 border-sleuth-amber-500/40'
                  : 'bg-charcoal-900 text-charcoal-400 border-charcoal-750 hover:bg-charcoal-800'
              }`}
            >
              All ({evidenceList?.length || 0})
            </button>
            <button
              onClick={() => setFilterType('schema_diff')}
              className={`px-3 py-1 rounded-full border transition-colors ${
                filterType === 'schema_diff'
                  ? 'bg-sleuth-amber-500/20 text-sleuth-amber-300 border-sleuth-amber-500/40'
                  : 'bg-charcoal-900 text-charcoal-400 border-charcoal-750 hover:bg-charcoal-800'
              }`}
            >
              Schema Diffs
            </button>
            <button
              onClick={() => setFilterType('dataset_anomaly')}
              className={`px-3 py-1 rounded-full border transition-colors ${
                filterType === 'dataset_anomaly'
                  ? 'bg-sleuth-amber-500/20 text-sleuth-amber-300 border-sleuth-amber-500/40'
                  : 'bg-charcoal-900 text-charcoal-400 border-charcoal-750 hover:bg-charcoal-800'
              }`}
            >
              Dataset Anomalies
            </button>
            <button
              onClick={() => setFilterType('log_stacktrace')}
              className={`px-3 py-1 rounded-full border transition-colors ${
                filterType === 'log_stacktrace'
                  ? 'bg-sleuth-amber-500/20 text-sleuth-amber-300 border-sleuth-amber-500/40'
                  : 'bg-charcoal-900 text-charcoal-400 border-charcoal-750 hover:bg-charcoal-800'
              }`}
            >
              Stacktraces
            </button>
            <button
              onClick={() => setFilterType('ast_pattern')}
              className={`px-3 py-1 rounded-full border transition-colors ${
                filterType === 'ast_pattern'
                  ? 'bg-sleuth-amber-500/20 text-sleuth-amber-300 border-sleuth-amber-500/40'
                  : 'bg-charcoal-900 text-charcoal-400 border-charcoal-750 hover:bg-charcoal-800'
              }`}
            >
              AST Patterns
            </button>
          </div>
        </div>

        {/* Evidence Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered?.map((ev) => (
            <div
              key={ev.id}
              className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-5 space-y-3 hover:border-charcoal-700 transition-colors"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <span className="font-mono text-xs font-bold text-sleuth-amber-400 bg-charcoal-800 px-2 py-0.5 rounded border border-charcoal-700">
                    {ev.id}
                  </span>
                  <span className="text-xs font-mono text-charcoal-300 uppercase px-2 py-0.5 rounded bg-charcoal-850 border border-charcoal-800">
                    {ev.evidence_type.replace('_', ' ')}
                  </span>
                </div>
                <StatusBadge status={ev.verification_status} size="sm" />
              </div>

              <div className="space-y-1">
                <div className="text-xs font-mono text-charcoal-400 flex items-center space-x-2">
                  <FileCode className="h-3 w-3 text-sky-400" />
                  <span className="text-charcoal-200 font-medium">{ev.source_file}</span>
                  {ev.line_number && <span>Line: {ev.line_number}</span>}
                  {ev.column_name && <span>Col: {ev.column_name}</span>}
                </div>
                <p className="text-sm text-charcoal-100 font-medium leading-relaxed pt-1">
                  {ev.observation}
                </p>
              </div>

              {ev.associated_hypothesis_id && (
                <div className="pt-2 border-t border-charcoal-800 flex items-center justify-between text-xs font-mono">
                  <span className="text-charcoal-400">Associated Hypothesis:</span>
                  <span className="text-sleuth-amber-400 font-bold">{ev.associated_hypothesis_id}</span>
                </div>
              )}
            </div>
          ))}

          {filtered?.length === 0 && (
            <div className="col-span-2 p-12 text-center text-charcoal-400 bg-charcoal-900 rounded-xl border border-charcoal-800 text-sm">
              No evidence matching the selected filter.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
