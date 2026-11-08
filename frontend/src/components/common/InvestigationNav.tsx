import React from 'react';
import { NavLink } from 'react-router-dom';
import { 
  Activity, 
  FileSearch, 
  GitPullRequest, 
  CheckCircle2, 
  FileText,
  Clock
} from 'lucide-react';
import { StatusBadge } from './StatusBadge';

interface InvestigationNavProps {
  investigationId: string;
  status: string;
  objective: string;
  stage: string;
  remainingBudget: number;
}

export const InvestigationNav: React.FC<InvestigationNavProps> = ({
  investigationId,
  status,
  objective,
  stage,
  remainingBudget,
}) => {
  const navItems = [
    { label: 'Workspace', path: `/investigations/${investigationId}`, icon: Activity, exact: true },
    { label: 'Evidence Explorer', path: `/investigations/${investigationId}/evidence`, icon: FileSearch },
    { label: 'Patch Review', path: `/investigations/${investigationId}/patches`, icon: GitPullRequest },
    { label: 'Validation Runs', path: `/investigations/${investigationId}/validation`, icon: CheckCircle2 },
    { label: 'Final Report', path: `/investigations/${investigationId}/report`, icon: FileText },
  ];

  return (
    <div className="bg-charcoal-900 border-b border-charcoal-750">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3.5">
        {/* Top header line */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 mb-3">
          <div className="space-y-1">
            <div className="flex items-center space-x-2">
              <span className="font-mono text-xs text-charcoal-400">ID: {investigationId.substring(0, 8)}</span>
              <StatusBadge status={status} size="sm" />
              <span className="text-xs font-mono text-sleuth-amber-400/90 bg-sleuth-amber-500/10 px-2 py-0.5 rounded border border-sleuth-amber-500/20">
                Stage: {stage.replace(/_/g, ' ')}
              </span>
            </div>
            <h1 className="text-base sm:text-lg font-semibold text-charcoal-100 line-clamp-1">
              {objective}
            </h1>
          </div>

          <div className="flex items-center space-x-3 text-xs font-mono">
            <div className="bg-charcoal-800 px-3 py-1.5 rounded border border-charcoal-700 flex items-center space-x-2 text-charcoal-300">
              <Clock className="h-3.5 w-3.5 text-charcoal-400" />
              <span>Budget:</span>
              <span className="text-charcoal-100 font-bold">{remainingBudget} tool calls remaining</span>
            </div>
          </div>
        </div>

        {/* Tab Links */}
        <div className="flex space-x-1 border-t border-charcoal-800 pt-2 overflow-x-auto">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.exact}
                className={({ isActive }) =>
                  `flex items-center space-x-2 px-3.5 py-1.5 rounded-md text-xs font-medium whitespace-nowrap transition-colors ${
                    isActive
                      ? 'bg-charcoal-800 text-sleuth-amber-400 border border-charcoal-700'
                      : 'text-charcoal-400 hover:text-charcoal-200 hover:bg-charcoal-850'
                  }`
                }
              >
                <Icon className="h-3.5 w-3.5" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </div>
      </div>
    </div>
  );
};

