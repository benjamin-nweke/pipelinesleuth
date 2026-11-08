import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Search, PlusCircle, Settings, HardDrive, Cpu, Terminal, ShieldAlert } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { fetchSettings } from '../../services/api';

export const Navbar: React.FC = () => {
  const location = useLocation();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
    refetchInterval: 30000,
  });

  const isActive = (path: string) => {
    if (path === '/' && location.pathname === '/') return true;
    if (path !== '/' && location.pathname.startsWith(path)) return true;
    return false;
  };

  return (
    <header className="border-b border-charcoal-750 bg-charcoal-900/95 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center space-x-6">
          <Link to="/" className="flex items-center space-x-2.5 group">
            <div className="h-8 w-8 rounded bg-gradient-to-br from-sleuth-amber-500 to-sleuth-orange-600 flex items-center justify-center text-charcoal-950 shadow-sm shadow-sleuth-amber-500/20 group-hover:scale-105 transition-transform">
              <Search className="h-4.5 w-4.5 stroke-[2.5]" />
            </div>
            <div>
              <div className="flex items-center space-x-1.5">
                <span className="font-bold text-base tracking-tight text-charcoal-100 font-mono">PipelineSleuth</span>
                <span className="px-1.5 py-0.2 bg-sleuth-amber-500/10 text-sleuth-amber-400 text-[10px] font-mono rounded border border-sleuth-amber-500/20">v1.0</span>
              </div>
            </div>
          </Link>

          {/* Nav links */}
          <nav className="hidden md:flex items-center space-x-1">
            <Link
              to="/"
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                isActive('/') && !location.pathname.startsWith('/investigations') && !location.pathname.startsWith('/settings') && !location.pathname.startsWith('/new')
                  ? 'bg-charcoal-800 text-sleuth-amber-400 border border-charcoal-700'
                  : 'text-charcoal-300 hover:text-charcoal-100 hover:bg-charcoal-800/50'
              }`}
            >
              Dashboard
            </Link>
            <Link
              to="/new"
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center space-x-1.5 ${
                isActive('/new')
                  ? 'bg-charcoal-800 text-sleuth-amber-400 border border-charcoal-700'
                  : 'text-charcoal-300 hover:text-charcoal-100 hover:bg-charcoal-800/50'
              }`}
            >
              <PlusCircle className="h-3.5 w-3.5" />
              <span>New Investigation</span>
            </Link>
          </nav>
        </div>

        {/* System Status Indicators */}
        <div className="flex items-center space-x-3 text-xs font-mono">
          {/* Docker Status Pill */}
          <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-charcoal-800 border border-charcoal-700 text-charcoal-300" title="Docker Container Sandbox Status">
            <HardDrive className="h-3.5 w-3.5 text-charcoal-400" />
            <span className="hidden sm:inline">Sandbox:</span>
            {settings?.docker_available ? (
              <span className="text-emerald-400 font-medium flex items-center">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 mr-1 animate-pulse"></span>
                Docker Active
              </span>
            ) : (
              <span className="text-amber-400 font-medium flex items-center" title="Docker unavailable: isolated container runs disabled">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400 mr-1"></span>
                Host Only
              </span>
            )}
          </div>

          {/* AI Model Indicator */}
          <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded bg-charcoal-800 border border-charcoal-700 text-charcoal-300">
            <Cpu className="h-3.5 w-3.5 text-sleuth-amber-400" />
            <span className="hidden sm:inline">{settings?.ai_provider === 'ollama' ? 'Ollama:' : 'OpenAI:'}</span>
            <span className="text-charcoal-200 font-medium truncate max-w-[120px]">
              {settings?.ai_provider === 'ollama' ? settings?.ollama_model : settings?.openai_model}
            </span>
          </div>

          {/* Settings link */}
          <Link
            to="/settings"
            className={`p-1.5 rounded text-charcoal-400 hover:text-charcoal-100 hover:bg-charcoal-800 transition-colors ${
              isActive('/settings') ? 'text-sleuth-amber-400 bg-charcoal-800' : ''
            }`}
            title="Settings & Model Configuration"
          >
            <Settings className="h-4 w-4" />
          </Link>
        </div>
      </div>
    </header>
  );
};
