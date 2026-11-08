import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { 
  fetchProjects, 
  createProject, 
  uploadProjectFiles, 
  createInvestigation, 
  fetchDemos, 
  loadDemoScenario 
} from '../services/api';
import { 
  Upload, 
  FileCode, 
  Database, 
  FileText, 
  Sparkles, 
  AlertCircle, 
  Check, 
  Loader2,
  FolderPlus,
  Play
} from 'lucide-react';

export const NewInvestigationPage: React.FC = () => {
  const navigate = useNavigate();

  // Mode: "upload" (custom files) or "demo" (pre-built scenario)
  const [activeTab, setActiveTab] = useState<'upload' | 'demo'>('upload');

  // Form states
  const [projectName, setProjectName] = useState('');
  const [description, setDescription] = useState('');
  const [pipelineType, setPipelineType] = useState('python_batch');
  const [entryPoint, setEntryPoint] = useState('');
  const [objective, setObjective] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const { data: existingProjects } = useQuery({
    queryKey: ['projects'],
    queryFn: fetchProjects,
  });

  const { data: demos } = useQuery({
    queryKey: ['demos'],
    queryFn: fetchDemos,
  });

  const [selectedProjectId, setSelectedProjectId] = useState<string>('');

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const filesArray = Array.from(e.target.files);
      // Validate extensions
      const allowed = ['.py', '.csv', '.parquet', '.json', '.log', '.txt', '.yaml', '.yml'];
      const invalid = filesArray.filter(f => !allowed.some(ext => f.name.toLowerCase().endsWith(ext)));
      if (invalid.length > 0) {
        setErrorMsg(`Unsupported files: ${invalid.map(f => f.name).join(', ')}. Allowed: ${allowed.join(', ')}`);
        return;
      }
      setErrorMsg(null);
      setSelectedFiles(filesArray);

      // Auto-detect entrypoint
      const pyMain = filesArray.find(f => f.name.includes('main') || f.name.includes('pipeline') || f.name.includes('run'));
      if (pyMain && !entryPoint) {
        setEntryPoint(pyMain.name);
      }
    }
  };

  const startCustomInvestigationMutation = useMutation({
    mutationFn: async () => {
      let pId = selectedProjectId;

      if (!pId) {
        if (!projectName.trim()) throw new Error('Please enter a project name or select an existing project');
        const formData = new FormData();
        formData.append('name', projectName);
        if (description) formData.append('description', description);
        formData.append('pipeline_type', pipelineType);
        if (entryPoint) formData.append('entry_point', entryPoint);

        const project = await createProject(formData);
        pId = project.id;

        if (selectedFiles.length > 0) {
          await uploadProjectFiles(pId, selectedFiles);
        }
      } else if (selectedFiles.length > 0) {
        await uploadProjectFiles(pId, selectedFiles);
      }

      if (!objective.trim()) {
        throw new Error('Please enter an investigation objective');
      }

      const inv = await createInvestigation(pId, objective);
      return inv;
    },
    onSuccess: (inv) => {
      navigate(`/investigations/${inv.id}`);
    },
    onError: (err: any) => {
      setErrorMsg(err.message || 'Failed to start investigation');
    },
  });

  const launchDemoMutation = useMutation({
    mutationFn: async (demoKey: string) => {
      const demo = demos?.find(d => d.key === demoKey);
      if (!demo) throw new Error('Demo not found');
      const project = await loadDemoScenario(demoKey);
      const inv = await createInvestigation(project.id, demo.default_objective);
      return inv;
    },
    onSuccess: (inv) => {
      navigate(`/investigations/${inv.id}`);
    },
    onError: (err: any) => {
      setErrorMsg(err.message || 'Failed to launch demo');
    },
  });

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-charcoal-100">Launch New Investigation</h1>
        <p className="text-sm text-charcoal-300 mt-1">
          Upload project artifacts and instruct the AI engine to isolate and validate the pipeline failure root cause.
        </p>
      </div>

      {errorMsg && (
        <div className="p-4 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-300 text-sm flex items-center space-x-2">
          <AlertCircle className="h-4 w-4 shrink-0 text-rose-400" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Mode selection tabs */}
      <div className="flex border-b border-charcoal-750">
        <button
          onClick={() => { setActiveTab('upload'); setErrorMsg(null); }}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px flex items-center space-x-2 transition-colors ${
            activeTab === 'upload'
              ? 'border-sleuth-amber-500 text-sleuth-amber-400'
              : 'border-transparent text-charcoal-400 hover:text-charcoal-200'
          }`}
        >
          <FolderPlus className="h-4 w-4" />
          <span>Upload Custom Pipeline</span>
        </button>
        <button
          onClick={() => { setActiveTab('demo'); setErrorMsg(null); }}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px flex items-center space-x-2 transition-colors ${
            activeTab === 'demo'
              ? 'border-sleuth-amber-500 text-sleuth-amber-400'
              : 'border-transparent text-charcoal-400 hover:text-charcoal-200'
          }`}
        >
          <Sparkles className="h-4 w-4" />
          <span>Load Demonstration Scenario</span>
        </button>
      </div>

      {/* Tab: Upload Custom Pipeline */}
      {activeTab === 'upload' && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            startCustomInvestigationMutation.mutate();
          }}
          className="space-y-6 bg-charcoal-900 border border-charcoal-750 rounded-xl p-6"
        >
          {/* Project setup */}
          <div className="space-y-4">
            <h2 className="text-sm font-mono font-semibold uppercase tracking-wider text-charcoal-300">
              1. Project Configuration
            </h2>

            {existingProjects && existingProjects.length > 0 && (
              <div className="space-y-1">
                <label className="text-xs font-medium text-charcoal-300">Use Existing Project (Optional)</label>
                <select
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                  className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-sm text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                >
                  <option value="">-- Create a new project --</option>
                  {existingProjects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.files.length} files)
                    </option>
                  ))}
                </select>
              </div>
            )}

            {!selectedProjectId && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-charcoal-300">Project Name *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Transaction Batch ETL"
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                    className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-sm text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-charcoal-300">Pipeline Type</label>
                  <input
                    type="text"
                    placeholder="e.g. python_batch, streaming, ml_feature"
                    value={pipelineType}
                    onChange={(e) => setPipelineType(e.target.value)}
                    className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-sm text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                  />
                </div>

                <div className="md:col-span-2 space-y-1">
                  <label className="text-xs font-medium text-charcoal-300">Description</label>
                  <input
                    type="text"
                    placeholder="Brief description of pipeline business context"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-sm text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Artifact uploads */}
          <div className="space-y-4 pt-4 border-t border-charcoal-800">
            <h2 className="text-sm font-mono font-semibold uppercase tracking-wider text-charcoal-300">
              2. Pipeline Artifacts
            </h2>

            <div className="border-2 border-dashed border-charcoal-700 hover:border-sleuth-amber-500/60 rounded-xl p-6 text-center cursor-pointer transition-colors bg-charcoal-850/50">
              <input
                type="file"
                multiple
                id="file-upload"
                onChange={handleFileChange}
                className="hidden"
              />
              <label htmlFor="file-upload" className="cursor-pointer space-y-2 block">
                <Upload className="h-8 w-8 mx-auto text-sleuth-amber-400 stroke-1" />
                <div className="text-sm font-medium text-charcoal-200">
                  Click to select pipeline source files, logs, and datasets
                </div>
                <div className="text-xs text-charcoal-400">
                  Supported formats: .py, .csv, .parquet, .json, .log, .txt, .yaml (Max 50MB per file)
                </div>
              </label>
            </div>

            {selectedFiles.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-mono text-charcoal-400">Selected Files ({selectedFiles.length}):</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {selectedFiles.map((file, idx) => (
                    <div
                      key={idx}
                      className="flex items-center space-x-2 bg-charcoal-800 px-3 py-2 rounded border border-charcoal-700 text-xs font-mono"
                    >
                      {file.name.endsWith('.py') && <FileCode className="h-3.5 w-3.5 text-sky-400" />}
                      {(file.name.endsWith('.csv') || file.name.endsWith('.parquet')) && <Database className="h-3.5 w-3.5 text-emerald-400" />}
                      {file.name.endsWith('.log') && <FileText className="h-3.5 w-3.5 text-rose-400" />}
                      <span className="truncate text-charcoal-200">{file.name}</span>
                      <span className="text-[10px] text-charcoal-400 ml-auto">{(file.size / 1024).toFixed(1)} KB</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-1">
              <label className="text-xs font-medium text-charcoal-300">Pipeline Entry Point (Optional)</label>
              <input
                type="text"
                placeholder="e.g. pipeline.py or main.py"
                value={entryPoint}
                onChange={(e) => setEntryPoint(e.target.value)}
                className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg px-3 py-2 text-sm text-charcoal-100 font-mono focus:outline-none focus:border-sleuth-amber-500"
              />
            </div>
          </div>

          {/* Investigation Objective */}
          <div className="space-y-4 pt-4 border-t border-charcoal-800">
            <h2 className="text-sm font-mono font-semibold uppercase tracking-wider text-charcoal-300">
              3. Investigation Objective
            </h2>

            <div className="space-y-1">
              <label className="text-xs font-medium text-charcoal-300">Objective *</label>
              <textarea
                required
                rows={3}
                placeholder="Describe the failure symptom or goal, e.g.: Investigate why the customer transaction pipeline terminated with a KeyError during the aggregation stage, identify root cause, and propose fix."
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                className="w-full bg-charcoal-800 border border-charcoal-700 rounded-lg p-3 text-sm text-charcoal-100 focus:outline-none focus:border-sleuth-amber-500 leading-relaxed"
              />
            </div>
          </div>

          <div className="pt-2 flex justify-end">
            <button
              type="submit"
              disabled={startCustomInvestigationMutation.isPending}
              className="inline-flex items-center space-x-2 bg-sleuth-amber-500 hover:bg-sleuth-amber-600 text-charcoal-950 font-semibold px-6 py-2.5 rounded-lg text-sm shadow-md shadow-sleuth-amber-500/20 transition-all disabled:opacity-50"
            >
              {startCustomInvestigationMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Starting Agent...</span>
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  <span>Start Autonomous Investigation</span>
                </>
              )}
            </button>
          </div>
        </form>
      )}

      {/* Tab: Load Demonstration Scenario */}
      {activeTab === 'demo' && (
        <div className="space-y-4">
          <p className="text-sm text-charcoal-300">
            Select one of the 3 pre-built scenarios to load source code, reproducible test datasets, and execution logs:
          </p>

          <div className="grid grid-cols-1 gap-4">
            {demos?.map((d) => (
              <div
                key={d.key}
                className="bg-charcoal-900 border border-charcoal-750 rounded-xl p-5 hover:border-sleuth-amber-500/40 transition-colors space-y-3"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-sleuth-amber-400 font-bold uppercase">
                    {d.key.replace('_', ' ')}
                  </span>
                  <span className="text-xs font-mono text-charcoal-400 bg-charcoal-800 px-2 py-0.5 rounded border border-charcoal-700">
                    Type: {d.pipeline_type}
                  </span>
                </div>

                <div>
                  <h3 className="text-base font-semibold text-charcoal-100">{d.name}</h3>
                  <p className="text-sm text-charcoal-300 mt-1">{d.description}</p>
                </div>

                <div className="bg-charcoal-850 p-3 rounded border border-charcoal-800 text-xs font-mono text-charcoal-300">
                  <span className="text-charcoal-500">Objective:</span> {d.default_objective}
                </div>

                <div className="pt-2 flex justify-end">
                  <button
                    onClick={() => launchDemoMutation.mutate(d.key)}
                    disabled={launchDemoMutation.isPending}
                    className="inline-flex items-center space-x-2 bg-sleuth-amber-500 hover:bg-sleuth-amber-600 text-charcoal-950 font-semibold px-4 py-2 rounded-lg text-xs transition-colors disabled:opacity-50"
                  >
                    {launchDemoMutation.isPending ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        <span>Loading Scenario...</span>
                      </>
                    ) : (
                      <>
                        <Play className="h-3.5 w-3.5 fill-current" />
                        <span>Load & Launch Investigation</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

