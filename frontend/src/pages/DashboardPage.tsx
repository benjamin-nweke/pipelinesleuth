import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { 
  fetchDashboardStats, 
  fetchDemos, 
  loadDemoScenario, 
  createInvestigation 
} from '../services/api';
import { StatusBadge } from '../components/common/StatusBadge';
import { 
  Activity, 
  CheckCircle2, 
  AlertTriangle, 
  ShieldCheck, 
  PlusCircle, 
  ArrowRight, 
  Play, 
  FileCode, 
  Calendar,
  Layers,
  Database
} from 'lucide-react';

export const DashboardPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['dashboardStats'],
    queryFn: fetchDashboardStats,
    refetchInterval: 5000,
  });

  const { data: demos } = useQuery({
    queryKey: ['demos'],
    queryFn: fetchDemos,
  });

  // One-click demo launcher mutation
  const demoLaunchMutation = useMutation({
    mutationFn: async (demoKey: string) => {
      const demo = demos?.find(d => d.key === demoKey);
      if (!demo) throw new Error('Demo not found');
      // 1. Load project
      const project = await loadDemoScenario(demoKey);
      // 2. Start investigation
      const inv = await createInvestigation(project.id, demo.default_objective);
      return inv;
    },
    onSuccess: (inv) => {
      queryClient.invalidateQueries({ queryKey: ['dashboardStats'] });
      navigate(`/investigations/${inv.id}`);
    },
  });

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
      {/* Hero Welcome banner */}
      <div className="bg-gradient-to-r from-charcoal-900 to-charcoal-850 rounded-xl p-6 border border-charcoal-750 flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div>
          <div className="flex items-center space-x-2 text-sleuth-amber-400 font-mono text-xs uppercase tracking-wider mb-1">
            <span className="h-2 w-2 rounded-full bg-sleuth-amber-400 animate-ping"></span>
            <span>Autonomous Failure Forensics</span>
          </div>
          <h1 className="text-2xl font-bold text-charcoal-100 tracking-tight">
            Pipeline Failure Intelligence Console
          </h1>
          <p className="text-sm text-charcoal-300 mt-1 max-w-2xl">
            PipelineSleuth autonomously discovers pipeline failures, isolates evidence-backed root causes,
            generates verifiable code corrections, and validates fixes in isolated sandboxes.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <Link
            to="/new"
            className="inline-flex items-center space-x-2 bg-sleuth-amber-500 hover:bg-sleuth-amber-600 text-charcoal-950 font-semibold px-4 py-2.5 rounded-lg text-sm shadow-sm shadow-sleuth-amber-500/20 transition-colors"
          >
            <PlusCircle className="h-4 w-4" />
            <span>New Investigation</span>
          </Link>
        </div>
      </div>

      {/* Primary Metrics Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-charcoal-900 rounded-lg p-5 border border-charcoal-750">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono uppercase text-charcoal-400">Total Investigations</span>
            <Activity className="h-4 w-4 text-charcoal-400" />
          </div>
          <div className="mt-3 flex items-baseline justify-between">
            <span className="text-3xl font-mono font-bold text-charcoal-100">
              {statsLoading ? '—' : stats?.metrics.total_investigations || 0}
            </span>
            <span className="text-xs text-charcoal-400">All sessions</span>
          </div>
        </div>

        <div className="bg-charcoal-900 rounded-lg p-5 border border-charcoal-750">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono uppercase text-charcoal-400">Active Loops</span>
            <span className="h-2.5 w-2.5 rounded-full bg-sleuth-amber-400 animate-pulse"></span>
          </div>
          <div className="mt-3 flex items-baseline justify-between">
            <span className="text-3xl font-mono font-bold text-sleuth-amber-400">
              {statsLoading ? '—' : stats?.metrics.active_investigations || 0}
            </span>
            <span className="text-xs text-sleuth-amber-400/80">Running now</span>
          </div>
        </div>

        <div className="bg-charcoal-900 rounded-lg p-5 border border-charcoal-750">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono uppercase text-charcoal-400">Confirmed Diagnoses</span>
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
          </div>
          <div className="mt-3 flex items-baseline justify-between">
            <span className="text-3xl font-mono font-bold text-emerald-400">
              {statsLoading ? '—' : stats?.metrics.confirmed_diagnoses || 0}
            </span>
            <span className="text-xs text-emerald-400/80 font-mono">Evidence-backed</span>
          </div>
        </div>

        <div className="bg-charcoal-900 rounded-lg p-5 border border-charcoal-750">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono uppercase text-charcoal-400">Validated Fixes</span>
            <ShieldCheck className="h-4 w-4 text-sky-400" />
          </div>
          <div className="mt-3 flex items-baseline justify-between">
            <span className="text-3xl font-mono font-bold text-sky-400">
              {statsLoading ? '—' : stats?.metrics.validated_fixes || 0}
            </span>
            <span className="text-xs text-sky-400/80 font-mono">Isolated tests passed</span>
          </div>
        </div>
      </div>

      {/* One-Click Demonstration Scenarios */}
      <div className="bg-charcoal-900 rounded-xl p-6 border border-charcoal-750 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-charcoal-100 flex items-center space-x-2">
              <Database className="h-4 w-4 text-sleuth-amber-400" />
              <span>Reproducible Test Scenarios</span>
            </h2>
            <p className="text-xs text-charcoal-400 mt-0.5">
              Launch preconfigured, reproducible failure scenarios to observe autonomous diagnosis in action.
            </p>
          </div>
          <span className="text-xs font-mono text-charcoal-400 hidden sm:inline">3 scenarios ready</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {demos?.map((d) => (
            <div
              key={d.key}
              className="bg-charcoal-850 hover:bg-charcoal-800/80 border border-charcoal-700/80 rounded-lg p-4 flex flex-col justify-between transition-colors group"
            >
              <div>
                <div className="flex items-center justify-between text-xs font-mono text-sleuth-amber-400 mb-1.5">
                  <span className="font-semibold">{d.key.replace('_', ' ').toUpperCase()}</span>
                  <span className="px-1.5 py-0.5 bg-charcoal-800 rounded border border-charcoal-700 text-[10px] text-charcoal-300">
                    {d.pipeline_type}
                  </span>
                </div>
                <h3 className="font-medium text-sm text-charcoal-100 line-clamp-1">{d.name}</h3>
                <p className="text-xs text-charcoal-400 mt-1 line-clamp-3 leading-relaxed">
                  {d.description}
                </p>
              </div>

              <div className="mt-4 pt-3 border-t border-charcoal-750/60 flex items-center justify-between">
                <span className="text-[11px] font-mono text-charcoal-400">Entry: {d.entry_point}</span>
                <button
                  onClick={() => demoLaunchMutation.mutate(d.key)}
                  disabled={demoLaunchMutation.isPending}
                  className="inline-flex items-center space-x-1.5 text-xs font-semibold text-sleuth-amber-400 hover:text-sleuth-amber-300 group-hover:translate-x-0.5 transition-transform disabled:opacity-50"
                >
                  <Play className="h-3 w-3 fill-current" />
                  <span>Investigate</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Two Column Layout: Recent Investigations & Recent Projects */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Recent Investigations (2 cols) */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-charcoal-100 flex items-center space-x-2">
              <Activity className="h-4 w-4 text-charcoal-400" />
              <span>Recent Investigations</span>
            </h2>
            <Link to="/new" className="text-xs text-sleuth-amber-400 hover:underline flex items-center space-x-1">
              <span>Start new</span>
              <ArrowRight className="h-3 w-3" />
            </Link>
          </div>

          <div className="bg-charcoal-900 rounded-lg border border-charcoal-750 divide-y divide-charcoal-800">
            {stats?.recent_investigations && stats.recent_investigations.length > 0 ? (
              stats.recent_investigations.map((inv) => (
                <Link
                  key={inv.id}
                  to={`/investigations/${inv.id}`}
                  className="p-4 flex items-center justify-between hover:bg-charcoal-850/60 transition-colors block"
                >
                  <div className="space-y-1 max-w-xl">
                    <div className="flex items-center space-x-2">
                      <span className="font-mono text-xs text-charcoal-400">#{inv.id.substring(0, 8)}</span>
                      <StatusBadge status={inv.status} size="sm" />
                      <span className="text-[11px] font-mono text-charcoal-400">
                        {inv.stage.replace(/_/g, ' ')}
                      </span>
                    </div>
                    <p className="text-sm font-medium text-charcoal-200 line-clamp-1">{inv.objective}</p>
                    <div className="text-xs text-charcoal-400 font-mono">
                      Started: {new Date(inv.created_at).toLocaleString()}
                    </div>
                  </div>

                  <div className="flex items-center space-x-4 text-right">
                    <div className="hidden sm:block">
                      <div className="text-xs text-charcoal-400 font-mono">Confidence</div>
                      <div className="text-sm font-mono font-bold text-sleuth-amber-400">
                        {Math.round(inv.confidence_score * 100)}%
                      </div>
                    </div>
                    <ArrowRight className="h-4 w-4 text-charcoal-500" />
                  </div>
                </Link>
              ))
            ) : (
              <div className="p-8 text-center text-charcoal-400 text-sm">
                No investigations recorded yet. Click above to launch one of the demo scenarios.
              </div>
            )}
          </div>
        </div>

        {/* Status Distribution & Recent Projects (1 col) */}
        <div className="space-y-6">
          {/* Status Breakdown card */}
          <div className="bg-charcoal-900 rounded-lg border border-charcoal-750 p-5 space-y-4">
            <h3 className="text-sm font-semibold text-charcoal-100 flex items-center space-x-2">
              <Layers className="h-4 w-4 text-charcoal-400" />
              <span>Status Distribution</span>
            </h3>

            <div className="space-y-2.5">
              {stats?.status_distribution && Object.keys(stats.status_distribution).length > 0 ? (
                Object.entries(stats.status_distribution).map(([status, count]) => {
                  const total = stats.metrics.total_investigations || 1;
                  const pct = Math.round((count / total) * 100);
                  return (
                    <div key={status} className="space-y-1">
                      <div className="flex justify-between text-xs font-mono">
                        <span className="capitalize text-charcoal-300">{status.replace(/_/g, ' ')}</span>
                        <span className="text-charcoal-400">{count} ({pct}%)</span>
                      </div>
                      <div className="w-full h-1.5 rounded-full bg-charcoal-800 overflow-hidden">
                        <div
                          className="h-full bg-sleuth-amber-500 rounded-full"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="text-xs text-charcoal-400 py-2">No data yet.</div>
              )}
            </div>
          </div>

          {/* Recent Projects Card */}
          <div className="bg-charcoal-900 rounded-lg border border-charcoal-750 p-5 space-y-4">
            <h3 className="text-sm font-semibold text-charcoal-100 flex items-center space-x-2">
              <FileCode className="h-4 w-4 text-charcoal-400" />
              <span>Recent Projects</span>
            </h3>

            <div className="divide-y divide-charcoal-800">
              {stats?.recent_projects && stats.recent_projects.length > 0 ? (
                stats.recent_projects.map((p) => (
                  <div key={p.id} className="py-2.5 first:pt-0 last:pb-0">
                    <div className="font-medium text-xs text-charcoal-200 line-clamp-1">{p.name}</div>
                    <div className="flex items-center justify-between text-[11px] font-mono text-charcoal-400 mt-0.5">
                      <span>{p.pipeline_type}</span>
                      <span>{p.entry_point || 'auto'}</span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-xs text-charcoal-400 py-2">No projects loaded.</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
