import React from 'react';
import { 
  CheckCircle2, 
  AlertCircle, 
  Clock, 
  HelpCircle, 
  Loader2, 
  ShieldCheck, 
  GitPullRequest,
  Check,
  X
} from 'lucide-react';

interface StatusBadgeProps {
  status: string;
  size?: 'sm' | 'md';
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, size = 'md' }) => {
  const norm = status.toLowerCase();

  let bg = 'bg-charcoal-800 text-charcoal-300 border-charcoal-700';
  let icon = <HelpCircle className="h-3 w-3 mr-1" />;
  let label = status.replace(/_/g, ' ');

  if (norm === 'completed' || norm === 'confirmed' || norm === 'passed' || norm === 'validated') {
    bg = 'bg-emerald-950/60 text-emerald-400 border-emerald-800/60';
    icon = <CheckCircle2 className="h-3 w-3 mr-1 text-emerald-400" />;
  } else if (norm === 'investigating' || norm === 'testing' || norm === 'validating') {
    bg = 'bg-sleuth-amber-950/60 text-sleuth-amber-400 border-sleuth-amber-800/60';
    icon = <Loader2 className="h-3 w-3 mr-1 text-sleuth-amber-400 animate-spin" />;
  } else if (norm === 'awaiting_patch_approval' || norm === 'proposed') {
    bg = 'bg-orange-950/60 text-orange-400 border-orange-800/60';
    icon = <GitPullRequest className="h-3 w-3 mr-1 text-orange-400" />;
  } else if (norm === 'inconclusive') {
    bg = 'bg-yellow-950/60 text-yellow-400 border-yellow-800/60';
    icon = <HelpCircle className="h-3 w-3 mr-1 text-yellow-400" />;
  } else if (norm === 'failed' || norm === 'rejected' || norm === 'validation_failed' || norm === 'error') {
    bg = 'bg-rose-950/60 text-rose-400 border-rose-800/60';
    icon = <AlertCircle className="h-3 w-3 mr-1 text-rose-400" />;
  } else if (norm === 'formulated' || norm === 'created') {
    bg = 'bg-sky-950/60 text-sky-400 border-sky-800/60';
    icon = <Clock className="h-3 w-3 mr-1 text-sky-400" />;
  }

  const pxClass = size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs';

  return (
    <span className={`inline-flex items-center font-mono font-medium rounded-full border ${bg} ${pxClass} uppercase tracking-wider`}>
      {icon}
      <span>{label}</span>
    </span>
  );
};
