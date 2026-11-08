import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchSettings, updateSettings, testConnectivity } from '../services/api';
import { 
  Settings, 
  Cpu, 
  HardDrive, 
  CheckCircle2, 
  AlertTriangle, 
  Loader2, 
  Key, 
  Terminal, 
  ShieldCheck, 
  Save,
  HelpCircle,
  ExternalLink
} from 'lucide-react';

export const SettingsPage: React.FC = () => {
  const queryClient = useQueryClient();

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  });

  const [provider, setProvider] = useState<string>('ollama');
  const [ollamaUrl, setOllamaUrl] = useState<string>('http://127.0.0.1:11434');
  const [ollamaModel, setOllamaModel] = useState<string>('llama3.1:8b');
  const [openaiUrl, setOpenaiUrl] = useState<string>('https://api.openai.com/v1');
  const [openaiModel, setOpenaiModel] = useState<string>('gpt-4o');
  const [openaiKey, setOpenaiKey] = useState<string>('');
  const [maxTools, setMaxTools] = useState<number>(12);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Sync state when settings load
  React.useEffect(() => {
    if (settings) {
      setProvider(settings.ai_provider);
      setOllamaUrl(settings.ollama_base_url);
      setOllamaModel(settings.ollama_model);
      setOpenaiUrl(settings.openai_base_url);
      setOpenaiModel(settings.openai_model);
      setMaxTools(settings.max_tool_calls);
    }
  }, [settings]);

  const updateMutation = useMutation({
    mutationFn: updateSettings,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    },
  });

  const testConnMutation = useMutation({
    mutationFn: testConnectivity,
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    const payload: any = {
      ai_provider: provider,
      ollama_base_url: ollamaUrl,
      ollama_model: ollamaModel,
      openai_base_url: openaiUrl,
      openai_model: openaiModel,
      max_tool_calls: maxTools,
    };
    if (openaiKey) {
      payload.openai_api_key = openaiKey;
    }
    updateMutation.mutate(payload);
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-charcoal-950 text-charcoal-400 font-mono text-xs">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        Loading system configuration...
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-charcoal-100 flex items-center space-x-2">
          <Settings className="h-5 w-5 text-sleuth-amber-400" />
          <span>System & Model Configuration</span>
        </h1>
        <p className="text-sm text-charcoal-300 mt-1">
          Configure local Ollama or OpenAI-compatible model endpoints, execution sandbox boundaries, and investigation limits.
        </p>
      </div>

      {saveSuccess && (
        <div className="p-3 bg-emerald-950/60 border border-emerald-800 text-emerald-300 rounded-lg text-xs font-mono flex items-center space-x-2">
          <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
          <span>Settings saved successfully.</span>
        </div>
      )}

      {/* Docker Sandbox Status Banner */}
      <div className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <HardDrive className="h-5 w-5 text-charcoal-300" />
            <h2 className="text-sm font-semibold text-charcoal-100">Execution Sandbox Isolation</h2>
          </div>
          {settings?.docker_available ? (
            <span className="px-2.5 py-1 rounded-full bg-emerald-950 border border-emerald-800 text-emerald-400 text-xs font-mono font-medium flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></span>
              <span>DOCKER AVAILABLE</span>
            </span>
          ) : (
            <span className="px-2.5 py-1 rounded-full bg-amber-950 border border-amber-800 text-amber-400 text-xs font-mono font-medium flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-amber-400"></span>
              <span>DOCKER UNAVAILABLE</span>
            </span>
          )}
        </div>

        <p className="text-xs text-charcoal-300 leading-relaxed">
          {settings?.docker_available ? (
            'Isolated candidate container execution is enabled with non-root user permissions, CPU/memory quotas, and network disabled.'
          ) : (
            'Docker is not running on this host. For safety, PipelineSleuth strictly blocks arbitrary Python execution on the host. In-sandbox test execution and candidate patch validation require Docker Desktop to be installed and active.'
          )}
        </p>

        {!settings?.docker_available && (
          <div className="bg-charcoal-850 p-3 rounded-lg border border-charcoal-800 font-mono text-xs text-charcoal-300 space-y-1">
            <div className="text-sleuth-amber-400 font-semibold">Setup instructions:</div>
            <div>1. Install Docker Desktop for macOS / Linux.</div>
            <div>2. Ensure Docker daemon is running (`docker info`).</div>
            <div>3. Refresh this page to enable containerized validation.</div>
          </div>
        )}
      </div>

      {/* Model Provider Form */}
      <form onSubmit={handleSave} className="bg-charcoal-900 rounded-xl border border-charcoal-750 p-6 space-y-6">
        <div className="space-y-4">
          <h2 className="text-sm font-mono font-semibold uppercase tracking-wider text-charcoal-300 flex items-center space-x-2">
            <Cpu className="h-4 w-4 text-sleuth-amber-400" />
            <span>AI Model Provider</span>
          </h2>

          <div className="grid grid-cols-2 gap-3">
            <label
              className={`flex items-center space-x-3 p-3.5 rounded-lg border cursor-pointer transition-colors ${
                provider === 'ollama'
                  ? 'bg-sleuth-amber-500/10 border-sleuth-amber-500/50 text-charcoal-100'
                  : 'bg-charcoal-850 border-charcoal-750 text-charcoal-400 hover:bg-charcoal-800'
              }`}
            >
              <input
                type="radio"
                name="provider"
                value="ollama"
                checked={provider === 'ollama'}
                onChange={() => setProvider('ollama')}
                className="text-sleuth-amber-500 focus:ring-0"
              />
              <div>
                <div className="font-semibold text-xs text-charcoal-200">Local Ollama</div>
                <div className="text-[11px] text-charcoal-400">100% offline local model inference</div>
              </div>
            </label>

            <label
              className={`flex items-center space-x-3 p-3.5 rounded-lg border cursor-pointer transition-colors ${
                provider === 'openai'
                  ? 'bg-sleuth-amber-500/10 border-sleuth-amber-500/50 text-charcoal-100'
                  : 'bg-charcoal-850 border-charcoal-750 text-charcoal-400 hover:bg-charcoal-800'
              }`}
            >
              <input
                type="radio"
                name="provider"
                value="openai"
                checked={provider === 'openai'}
                onChange={() => setProvider('openai')}
                className="text-sleuth-amber-500 focus:ring-0"
              />
              <div>
                <div className="font-semibold text-xs text-charcoal-200">OpenAI-Compatible</div>
                <div className="text-[11px] text-charcoal-400">Remote API endpoint (GPT-4o, vLLM, etc.)</div>
              </div>
            </label>
          </div>

          {/* Ollama options */}
          {provider === 'ollama' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
              <div className="space-y-1">
                <label className="text-xs font-medium text-charcoal-300">Ollama Base URL</label>
                <input
                  type="text"
                  value={ollamaUrl}
                  onChange={(e) => setOllamaUrl(e.target.value)}
                  className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-xs font-mono text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-charcoal-300">Model Name</label>
                <input
                  type="text"
                  value={ollamaModel}
                  onChange={(e) => setOllamaModel(e.target.value)}
                  placeholder="e.g. llama3.1:8b, mistral:7b, qwen2.5:7b"
                  className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-xs font-mono text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                />
              </div>
            </div>
          )}

          {/* OpenAI options */}
          {provider === 'openai' && (
            <div className="space-y-4 pt-2">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-charcoal-300">OpenAI Base URL</label>
                  <input
                    type="text"
                    value={openaiUrl}
                    onChange={(e) => setOpenaiUrl(e.target.value)}
                    className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-xs font-mono text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-charcoal-300">Model Name</label>
                  <input
                    type="text"
                    value={openaiModel}
                    onChange={(e) => setOpenaiModel(e.target.value)}
                    placeholder="e.g. gpt-4o, gpt-4-turbo"
                    className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-xs font-mono text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-charcoal-300">
                  API Key {settings?.has_openai_key && <span className="text-emerald-400 font-mono">(Key currently configured)</span>}
                </label>
                <input
                  type="password"
                  value={openaiKey}
                  onChange={(e) => setOpenaiKey(e.target.value)}
                  placeholder={settings?.has_openai_key ? '••••••••••••••••••••' : 'sk-...'}
                  className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-xs font-mono text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                />
              </div>
            </div>
          )}

          {/* Connectivity Test */}
          <div className="pt-2 flex items-center justify-between">
            <button
              type="button"
              onClick={() => testConnMutation.mutate()}
              disabled={testConnMutation.isPending}
              className="px-3.5 py-1.5 bg-charcoal-800 hover:bg-charcoal-750 text-charcoal-200 border border-charcoal-700 rounded-lg text-xs font-mono flex items-center space-x-1.5 transition-colors disabled:opacity-50"
            >
              {testConnMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Cpu className="h-3.5 w-3.5 text-sleuth-amber-400" />
              )}
              <span>Test Connectivity</span>
            </button>

            {testConnMutation.data && (
              <div className={`text-xs font-mono flex items-center space-x-1.5 ${testConnMutation.data.success ? 'text-emerald-400' : 'text-amber-400'}`}>
                {testConnMutation.data.success ? (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                )}
                <span>
                  {testConnMutation.data.message} ({testConnMutation.data.latency_ms} ms)
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Investigation Engine Boundaries */}
        <div className="space-y-4 pt-4 border-t border-charcoal-800">
          <h2 className="text-sm font-mono font-semibold uppercase tracking-wider text-charcoal-300">
            Investigation Engine Boundaries
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-charcoal-300">Max Tool Calls per Session</label>
              <input
                type="number"
                min={3}
                max={30}
                value={maxTools}
                onChange={(e) => setMaxTools(parseInt(e.target.value) || 12)}
                className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-xs font-mono text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
              />
              <span className="text-[11px] text-charcoal-400">Bounded loop limit to prevent runaway inference</span>
            </div>
          </div>
        </div>

        <div className="pt-4 border-t border-charcoal-800 flex justify-end">
          <button
            type="submit"
            disabled={updateMutation.isPending}
            className="inline-flex items-center space-x-2 bg-sleuth-amber-500 hover:bg-sleuth-amber-600 text-charcoal-950 font-bold px-5 py-2 rounded-lg text-xs shadow-sm shadow-sleuth-amber-500/20 transition-all disabled:opacity-50"
          >
            {updateMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            <span>Save Configuration</span>
          </button>
        </div>
      </form>
    </div>
  );
};
