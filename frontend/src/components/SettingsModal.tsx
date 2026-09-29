import React, { useState, useEffect } from 'react';
import {
  X,
  Settings,
  Sparkles,
  RotateCcw,
  Save,
  Trash2,
  Copy,
  Plus,
  Eye,
  EyeOff,
  Check,
  AlertTriangle,
  Loader2,
  FileCode,
  Globe2,
  Activity,
  Zap,
  CheckCircle2,
  WifiOff,
} from 'lucide-react';
import type { PromptProfile, ProfilesData } from '../types';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProfileUpdated: (activeProfile: PromptProfile) => void;
  showToast: (msg: string) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  onProfileUpdated,
  showToast,
}) => {
  const [profilesData, setProfilesData] = useState<ProfilesData | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string>('default');

  // Form edit state (active edits for the selected profile)
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editModelEn, setEditModelEn] = useState('minimax-m2.5');
  const [editModelTl, setEditModelTl] = useState('minimax-m2.5');
  const [editInstructions, setEditInstructions] = useState('');

  // UI state
  const [showPreview, setShowPreview] = useState(false);
  const [previewContent, setPreviewContent] = useState('');
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [confirmFactoryReset, setConfirmFactoryReset] = useState(false);
  const [isActivating, setIsActivating] = useState(false);

  // Backend & LLM Connection Diagnostics
  const [backendStatus, setBackendStatus] = useState<'checking' | 'reachable' | 'unreachable'>('checking');
  const [backendLatency, setBackendLatency] = useState<number | null>(null);
  const [isTestingConnection, setIsTestingConnection] = useState(false);
  const [testResult, setTestResult] = useState<any | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // Check backend health
  const checkBackendHealth = async () => {
    setBackendStatus('checking');
    const t0 = performance.now();
    try {
      const res = await fetch('/api/health');
      if (res.ok) {
        const lat = Math.round(performance.now() - t0);
        setBackendStatus('reachable');
        setBackendLatency(lat);
      } else {
        setBackendStatus('unreachable');
        setBackendLatency(null);
      }
    } catch {
      setBackendStatus('unreachable');
      setBackendLatency(null);
    }
  };

  // Test LLM Connection handler
  const handleTestConnection = async () => {
    setIsTestingConnection(true);
    setTestError(null);
    try {
      const res = await fetch('/api/remarks/test-connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_english: editModelEn,
          model_tagalog: editModelTl,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 100)}`);
      }
      const data = await res.json();
      setTestResult(data);
      if (data.backend?.status === 'reachable') {
        setBackendStatus('reachable');
        if (data.backend.latency_ms) {
          setBackendLatency(Math.round(data.backend.latency_ms));
        }
      }
      showToast('LLM connection test complete.');
    } catch (err: any) {
      setTestError(err.message || 'Connection test failed');
      setBackendStatus('unreachable');
      showToast('Connection test failed.');
    } finally {
      setIsTestingConnection(false);
    }
  };

  // Load profiles, models, and backend health on modal open
  useEffect(() => {
    if (!isOpen) return;

    checkBackendHealth();
    setTestResult(null);
    setTestError(null);

    // Fetch models list
    fetch('/api/remarks/models')
      .then((res) => res.json())
      .then((data) => {
        if (data.models && Array.isArray(data.models)) {
          setAvailableModels(data.models);
        }
      })
      .catch((err) => console.error('Failed to load models:', err));

    // Fetch prompt profiles
    loadProfiles();
  }, [isOpen]);

  const loadProfiles = (targetIdToSelect?: string) => {
    fetch('/api/remarks/profiles')
      .then((res) => res.json())
      .then((data: ProfilesData) => {
        setProfilesData(data);
        const activeId = targetIdToSelect || data.active_profile_id || 'default';
        setSelectedProfileId(activeId);
        populateForm(data.profiles, activeId);
      })
      .catch((err) => console.error('Failed to load profiles:', err));
  };

  const populateForm = (profiles: PromptProfile[], id: string) => {
    const prof = profiles.find((p) => p.id === id) || profiles[0];
    if (prof) {
      setEditName(prof.name);
      setEditDesc(prof.description || '');
      setEditModelEn(prof.model_english || 'glm-5');
      setEditModelTl(prof.model_tagalog || 'minimax-m2.5');
      setEditInstructions(prof.system_instructions || '');
    }
  };

  const handleSelectProfile = (id: string) => {
    setSelectedProfileId(id);
    if (profilesData) {
      populateForm(profilesData.profiles, id);
    }
  };

  // Directly activate the selected profile
  const handleActivateProfile = async () => {
    setIsActivating(true);
    try {
      const res = await fetch('/api/remarks/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'activate', profile_id: selectedProfileId }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Failed to activate profile');
      }
      const data = await res.json();
      setProfilesData(data.profiles_data);
      onProfileUpdated(data.active_profile);
      showToast(`Profile "${data.active_profile.name}" is now active!`);
    } catch (err: any) {
      alert(`Activation failed: ${err.message}`);
    } finally {
      setIsActivating(false);
    }
  };

  // Revert / Discard uncommitted changes back to current saved profile
  const handleRevertToCurrent = () => {
    if (profilesData) {
      populateForm(profilesData.profiles, selectedProfileId);
      showToast('Settings reverted to current saved profile.');
    }
  };

  // Create a new blank profile
  const handleCreateNewProfile = () => {
    const newId = `profile_${Date.now().toString(36)}`;
    const newProf: PromptProfile = {
      id: newId,
      name: `Custom Profile (${new Date().toLocaleDateString()})`,
      description: 'Custom model routes and specialized directives',
      model_english: editModelEn || 'minimax-m2.5',
      model_tagalog: editModelTl || 'minimax-m2.5',
      system_instructions: editInstructions,
      is_default: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    if (profilesData) {
      const updatedList = [...profilesData.profiles, newProf];
      setProfilesData({
        ...profilesData,
        profiles: updatedList,
      });
      setSelectedProfileId(newId);
      populateForm(updatedList, newId);
      showToast('Created new profile draft. Click "Save & Apply" to persist.');
    }
  };

  // Duplicate the current profile
  const handleDuplicateProfile = () => {
    const current = profilesData?.profiles.find((p) => p.id === selectedProfileId);
    const newId = `profile_${Date.now().toString(36)}`;
    const cloned: PromptProfile = {
      id: newId,
      name: `${current ? current.name : 'Profile'} (Copy)`,
      description: current?.description || '',
      model_english: editModelEn,
      model_tagalog: editModelTl,
      system_instructions: editInstructions,
      is_default: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    if (profilesData) {
      const updatedList = [...profilesData.profiles, cloned];
      setProfilesData({
        ...profilesData,
        profiles: updatedList,
      });
      setSelectedProfileId(newId);
      populateForm(updatedList, newId);
      showToast('Profile duplicated. You can now tweak directives and models.');
    }
  };

  // Delete custom profile
  const handleDeleteProfile = async () => {
    if (selectedProfileId === 'default') {
      alert('The System Default profile cannot be deleted.');
      return;
    }

    if (!confirm(`Are you sure you want to delete profile "${editName}"?`)) {
      return;
    }

    try {
      const res = await fetch(`/api/remarks/profiles/${selectedProfileId}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Failed to delete profile');
      }
      const data = await res.json();
      setProfilesData(data.profiles_data);
      const nextId = data.profiles_data.active_profile_id || 'default';
      setSelectedProfileId(nextId);
      populateForm(data.profiles_data.profiles, nextId);
      showToast('Profile deleted successfully.');
    } catch (err: any) {
      alert(err.message);
    }
  };

  // Save and Apply Profile
  const handleSaveAndApply = async () => {
    if (!editName.trim()) {
      alert('Please provide a profile name.');
      return;
    }

    setIsSaving(true);
    try {
      const isDefault = selectedProfileId === 'default';
      const payload = {
        id: selectedProfileId,
        name: editName.trim(),
        description: editDesc.trim(),
        model_english: editModelEn,
        model_tagalog: editModelTl,
        system_instructions: editInstructions,
        is_default: isDefault,
      };

      const res = await fetch('/api/remarks/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile: payload, set_active: true }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Failed to save profile');
      }

      const resData = await res.json();
      setProfilesData(resData.profiles_data);
      setSelectedProfileId(resData.saved_profile.id);
      populateForm(resData.profiles_data.profiles, resData.saved_profile.id);
      onProfileUpdated(resData.saved_profile);
      showToast(`Profile "${resData.saved_profile.name}" saved and activated!`);
      onClose();
    } catch (err: any) {
      alert(`Save failed: ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  // Restore System Factory Defaults
  const handleRestoreFactoryDefaults = async () => {
    setIsResetting(true);
    try {
      const res = await fetch('/api/remarks/profiles/reset', {
        method: 'POST',
      });
      if (!res.ok) {
        throw new Error('Failed to reset profiles');
      }
      const data = await res.json();
      setProfilesData(data.profiles_data);
      setSelectedProfileId('default');
      populateForm(data.profiles_data.profiles, 'default');
      const defaultProf = data.profiles_data.profiles.find((p: any) => p.id === 'default');
      if (defaultProf) {
        onProfileUpdated(defaultProf);
      }
      setConfirmFactoryReset(false);
      showToast('All profiles restored to factory system defaults.');
    } catch (err: any) {
      alert(`Reset failed: ${err.message}`);
    } finally {
      setIsResetting(false);
    }
  };

  // Render Full Assembled Prompt Preview
  const handleTogglePreview = async () => {
    if (!showPreview) {
      setIsLoadingPreview(true);
      try {
        const res = await fetch('/api/remarks/prompt-preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ system_instructions: editInstructions }),
        });
        if (res.ok) {
          const data = await res.json();
          setPreviewContent(data.rendered_prompt || '');
        }
      } catch (err) {
        console.error('Failed to preview prompt:', err);
      } finally {
        setIsLoadingPreview(false);
      }
    }
    setShowPreview(!showPreview);
  };

  if (!isOpen) return null;

  const currentProfile = profilesData?.profiles.find((p) => p.id === selectedProfileId);
  const isActiveProfile = profilesData?.active_profile_id === selectedProfileId;

  // Fallback model list if endpoint hasn't resolved
  const models = availableModels.length > 0
    ? availableModels
    : [
        'minimax-m2.5',
        'claude-haiku-4-5',
        'nova-2-lite',
        'nova-pro',
        'glm-4.7-flash',
        'qwen3-32b',
        'nova-micro',
        'gemma-4-e2b',
        'nemotron-3-nano',
        'glm-5',
      ];

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-2xl max-w-4xl w-full max-h-[92vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Modal Header */}
        <div className="px-6 py-4 bg-slate-900 text-white flex items-center justify-between border-b border-slate-800">
          <div className="flex items-center space-x-2.5">
            <div className="p-2 rounded-lg bg-indigo-600/30 border border-indigo-500/40 text-indigo-400">
              <Settings className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="font-bold text-base tracking-tight text-white">AI Engine &amp; Prompt Configuration</h3>
                {isActiveProfile ? (
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-mono">
                    <Check className="w-3 h-3 mr-0.5" /> ACTIVE PROFILE
                  </span>
                ) : (
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-400 border border-slate-700 font-mono">
                    INACTIVE
                  </span>
                )}
                {/* Backend Reachability Indicator */}
                {backendStatus === 'reachable' ? (
                  <span
                    className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-mono"
                    title={`Backend API is connected and responding on port 8080 (latency: ${backendLatency ?? 0}ms)`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                    <span>Backend Online {backendLatency !== null ? `(${backendLatency}ms)` : ''}</span>
                  </span>
                ) : backendStatus === 'unreachable' ? (
                  <span
                    className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-500/20 text-red-300 border border-red-500/40 font-mono"
                    title="Backend API server is not responding or unreachable at http://127.0.0.1:8080"
                  >
                    <WifiOff className="w-2.5 h-2.5 text-red-400" />
                    <span>Backend Offline</span>
                  </span>
                ) : (
                  <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-400 border border-slate-700 font-mono">
                    <Loader2 className="w-2.5 h-2.5 animate-spin text-slate-400" />
                    <span>Checking Backend...</span>
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Configure 2-Tier Language routing models, prompt directives, and operational rules.
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            title="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Profile Selector & Actions Bar */}
          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 flex flex-col md:flex-row md:items-center justify-between gap-3">
            <div className="flex items-center space-x-3 flex-1">
              <label className="text-xs font-bold text-slate-700 uppercase tracking-wider whitespace-nowrap">
                Select Profile:
              </label>
              <select
                value={selectedProfileId}
                onChange={(e) => handleSelectProfile(e.target.value)}
                className="w-full md:w-64 text-xs font-semibold bg-white border border-slate-300 rounded-lg px-3 py-2 text-slate-800 shadow-xs focus:ring-2 focus:ring-indigo-500 focus:outline-hidden"
              >
                {profilesData?.profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} {p.is_default ? '(Default)' : ''} {p.id === profilesData?.active_profile_id ? '✓ [Active]' : ''}
                  </option>
                ))}
              </select>

              {!isActiveProfile && (
                <button
                  type="button"
                  onClick={handleActivateProfile}
                  disabled={isActivating}
                  className="inline-flex items-center space-x-1.5 px-3 py-2 rounded-lg text-xs font-bold bg-emerald-600 hover:bg-emerald-700 text-white shadow-xs transition-colors shrink-0"
                  title="Make this profile the active profile"
                >
                  <Check className="w-3.5 h-3.5" />
                  <span>{isActivating ? 'Activating...' : 'Activate'}</span>
                </button>
              )}
            </div>

            <div className="flex items-center space-x-2">
              <button
                type="button"
                onClick={handleCreateNewProfile}
                className="inline-flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 shadow-xs transition-colors"
                title="Create New Profile"
              >
                <Plus className="w-3.5 h-3.5 text-indigo-600" />
                <span>New</span>
              </button>

              <button
                type="button"
                onClick={handleDuplicateProfile}
                className="inline-flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 shadow-xs transition-colors"
                title="Duplicate Current Profile"
              >
                <Copy className="w-3.5 h-3.5 text-slate-600" />
                <span>Duplicate</span>
              </button>

              {selectedProfileId !== 'default' && (
                <button
                  type="button"
                  onClick={handleDeleteProfile}
                  className="inline-flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-red-50 border border-red-200 hover:bg-red-100 text-red-700 shadow-xs transition-colors"
                  title="Delete Profile"
                >
                  <Trash2 className="w-3.5 h-3.5 text-red-600" />
                  <span>Delete</span>
                </button>
              )}
            </div>
          </div>

          {/* Profile Name & Description */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1">
                Profile Name
              </label>
              <input
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                disabled={currentProfile?.is_default}
                placeholder="e.g. Aggressive Skip Trace Rules"
                className={`w-full text-xs font-medium px-3 py-2 rounded-lg border border-slate-300 focus:ring-2 focus:ring-indigo-500 focus:outline-hidden ${
                  currentProfile?.is_default ? 'bg-slate-100 text-slate-500 cursor-not-allowed' : 'bg-white'
                }`}
              />
              {currentProfile?.is_default && (
                <span className="text-[10px] text-slate-400 mt-1 block">Default system profile name is protected</span>
              )}
            </div>

            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1">
                Description / Purpose
              </label>
              <input
                type="text"
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                placeholder="Brief summary of profile modifications..."
                className="w-full text-xs font-medium px-3 py-2 rounded-lg border border-slate-300 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-hidden"
              />
            </div>
          </div>

          {/* 2-Tier Language Routed Model Selection */}
          <div className="border border-slate-200 rounded-xl p-4 bg-slate-50/50 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Globe2 className="w-4 h-4 text-indigo-600" />
                <h4 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                  2-Tier Language Routed Models
                </h4>
              </div>
              <span className="text-[11px] text-slate-500">
                Routed via SPM LiteLLM Proxy
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
              {/* English Model Dropdown */}
              <div className="bg-white p-3 rounded-lg border border-slate-200">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-bold text-slate-800 flex items-center space-x-1.5">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-blue-100 text-blue-800 font-bold">
                      EN
                    </span>
                    <span>English Model Route</span>
                  </label>
                  <span className="text-[10px] text-slate-400 font-mono">Ultra-fast tier</span>
                </div>
                <select
                  value={editModelEn}
                  onChange={(e) => setEditModelEn(e.target.value)}
                  className="w-full text-xs font-mono font-medium bg-slate-50 border border-slate-300 rounded-md px-2.5 py-2 text-slate-800 focus:ring-2 focus:ring-indigo-500 focus:outline-hidden"
                >
                  {models.map((m) => (
                    <option key={`en-${m}`} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
                <p className="text-[10px] text-slate-500 mt-1">
                  Processes purely English remarks with low latency.
                </p>
              </div>

              {/* Tagalog Model Dropdown */}
              <div className="bg-white p-3 rounded-lg border border-slate-200">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-bold text-slate-800 flex items-center space-x-1.5">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-100 text-emerald-800 font-bold">
                      TL
                    </span>
                    <span>Tagalog / Taglish Model Route</span>
                  </label>
                  <span className="text-[10px] text-slate-400 font-mono">Multilingual tier</span>
                </div>
                <select
                  value={editModelTl}
                  onChange={(e) => setEditModelTl(e.target.value)}
                  className="w-full text-xs font-mono font-medium bg-slate-50 border border-slate-300 rounded-md px-2.5 py-2 text-slate-800 focus:ring-2 focus:ring-indigo-500 focus:outline-hidden"
                >
                  {models.map((m) => (
                    <option key={`tl-${m}`} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
                <p className="text-[10px] text-slate-500 mt-1">
                  Processes colloquial Tagalog, nuances, and code-switched phrases.
                </p>
              </div>
            </div>

            {/* Test LLM Connection Section */}
            <div className="mt-3 pt-3 border-t border-slate-200/80">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div>
                  <h5 className="text-xs font-bold text-slate-800 flex items-center gap-1.5">
                    <Activity className="w-3.5 h-3.5 text-indigo-600" />
                    <span>LLM &amp; Proxy Connectivity Test</span>
                  </h5>
                  <p className="text-[11px] text-slate-500">
                    Verify LiteLLM proxy and check if your selected models ({editModelEn} &amp; {editModelTl}) are online and responding.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleTestConnection}
                  disabled={isTestingConnection}
                  className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-700 text-white shadow-xs transition-colors shrink-0 disabled:opacity-50 cursor-pointer"
                  title="Send live probe to LiteLLM proxy and selected models"
                >
                  {isTestingConnection ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Testing Models...</span>
                    </>
                  ) : (
                    <>
                      <Zap className="w-3.5 h-3.5 text-amber-300" />
                      <span>Test LLM Connection</span>
                    </>
                  )}
                </button>
              </div>

              {/* Diagnostic Results Card */}
              {testResult && (
                <div className="mt-3 bg-white p-3 rounded-xl border border-slate-200 shadow-2xs space-y-2.5 animate-in fade-in duration-150">
                  <div className="flex items-center justify-between text-xs border-b border-slate-100 pb-2">
                    <span className="font-semibold text-slate-700 flex items-center gap-1.5">
                      <span className="text-[10px] uppercase tracking-wider text-slate-400">Diagnostic Status:</span>
                      {testResult.proxy?.reachable && testResult.models?.english?.status === 'online' && testResult.models?.tagalog?.status === 'online' ? (
                        <span className="text-emerald-700 font-bold flex items-center gap-1">
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" /> All Systems Operational
                        </span>
                      ) : (
                        <span className="text-amber-700 font-bold flex items-center gap-1">
                          <AlertTriangle className="w-3.5 h-3.5 text-amber-600" /> Connection Issues Detected
                        </span>
                      )}
                    </span>
                    <span className="text-[11px] font-mono text-slate-500">
                      Backend API: <span className="text-emerald-700 font-bold">Reachable</span> ({testResult.backend?.latency_ms ? `${testResult.backend.latency_ms}ms` : 'online'})
                    </span>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-xs">
                    {/* Proxy Card */}
                    <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200 space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-700">LiteLLM Proxy</span>
                        {testResult.proxy?.reachable ? (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800">
                            ONLINE ({testResult.proxy.latency_ms}ms)
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-800">
                            UNREACHABLE
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] font-mono text-slate-500 truncate" title={testResult.proxy?.base_url}>
                        {testResult.proxy?.base_url}
                      </div>
                      {testResult.proxy?.error && (
                        <p className="text-[10px] text-red-600 font-mono mt-1 break-words">{testResult.proxy.error}</p>
                      )}
                    </div>

                    {/* English Model Card */}
                    <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200 space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-700 truncate pr-1">EN: {testResult.models?.english?.model}</span>
                        {testResult.models?.english?.status === 'online' ? (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800 shrink-0">
                            ONLINE ({testResult.models.english.latency_ms}ms)
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-800 shrink-0">
                            FAILED
                          </span>
                        )}
                      </div>
                      {testResult.models?.english?.status === 'online' ? (
                        <p className="text-[10px] text-emerald-700 font-medium">Ready for English remarks</p>
                      ) : (
                        <p className="text-[10px] text-red-600 font-mono break-words">
                          {testResult.models?.english?.error || 'No response / timed out'}
                        </p>
                      )}
                    </div>

                    {/* Tagalog Model Card */}
                    <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200 space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-700 truncate pr-1">TL: {testResult.models?.tagalog?.model}</span>
                        {testResult.models?.tagalog?.status === 'online' ? (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800 shrink-0">
                            ONLINE ({testResult.models.tagalog.latency_ms}ms)
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-800 shrink-0">
                            FAILED
                          </span>
                        )}
                      </div>
                      {testResult.models?.tagalog?.status === 'online' ? (
                        <p className="text-[10px] text-emerald-700 font-medium">Ready for Tagalog remarks</p>
                      ) : (
                        <p className="text-[10px] text-red-600 font-mono break-words">
                          {testResult.models?.tagalog?.error || 'No response / timed out'}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {testError && !testResult && (
                <div className="mt-2.5 p-2.5 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs flex items-center space-x-2">
                  <AlertTriangle className="w-4 h-4 shrink-0 text-red-600" />
                  <span className="font-mono text-[11px]">{testError}</span>
                </div>
              )}
            </div>
          </div>

          {/* System Prompt & Operational Directives Editor */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div>
                <label className="text-xs font-bold text-slate-800 uppercase tracking-wider flex items-center gap-1.5">
                  <FileCode className="w-4 h-4 text-indigo-600" />
                  System Operational Directives &amp; Classification Rules
                </label>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  Directives defining priority hierarchies, informant distinction, and exception logic for the LLM.
                </p>
              </div>
              <span className="text-[11px] font-mono text-slate-400">
                {editInstructions.length} chars
              </span>
            </div>

            <textarea
              rows={13}
              value={editInstructions}
              onChange={(e) => setEditInstructions(e.target.value)}
              className="w-full p-3 font-mono text-xs leading-relaxed bg-slate-900 text-slate-100 rounded-xl border border-slate-800 shadow-inner focus:ring-2 focus:ring-indigo-500 focus:outline-hidden resize-y"
              placeholder="Paste or edit operational directives and rules..."
              spellCheck={false}
            />
          </div>

          {/* Expandable Full Prompt Preview */}
          <div className="border border-slate-200 rounded-xl overflow-hidden">
            <button
              type="button"
              onClick={handleTogglePreview}
              className="w-full px-4 py-3 bg-slate-50 hover:bg-slate-100 flex items-center justify-between text-xs font-semibold text-slate-700 transition-colors"
            >
              <span className="flex items-center space-x-2">
                <Sparkles className="w-4 h-4 text-indigo-600" />
                <span>View Full Assembled Prompt Preview (Taxonomies + Schema + Directives)</span>
              </span>
              <span className="flex items-center space-x-1 text-slate-500">
                {isLoadingPreview ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : showPreview ? (
                  <>
                    <EyeOff className="w-3.5 h-3.5" />
                    <span>Hide</span>
                  </>
                ) : (
                  <>
                    <Eye className="w-3.5 h-3.5" />
                    <span>Expand</span>
                  </>
                )}
              </span>
            </button>

            {showPreview && (
              <div className="p-4 bg-slate-950 text-slate-300 font-mono text-[11px] leading-relaxed max-h-72 overflow-y-auto border-t border-slate-200 whitespace-pre-wrap select-text">
                {isLoadingPreview ? (
                  <div className="flex items-center justify-center py-6 text-slate-400">
                    <Loader2 className="w-5 h-5 animate-spin mr-2" />
                    Rendering full prompt preview...
                  </div>
                ) : (
                  previewContent || 'Click expand to render preview.'
                )}
              </div>
            )}
          </div>
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-4 bg-slate-50 border-t border-slate-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          {/* Left Actions: Factory Defaults */}
          <div>
            {!confirmFactoryReset ? (
              <button
                type="button"
                onClick={() => setConfirmFactoryReset(true)}
                disabled={isResetting || isSaving}
                className="text-xs text-red-600 hover:text-red-700 font-semibold flex items-center space-x-1 px-2.5 py-1.5 rounded-lg hover:bg-red-50 transition-colors"
              >
                <AlertTriangle className="w-3.5 h-3.5" />
                <span>Restore Factory Defaults</span>
              </button>
            ) : (
              <div className="flex items-center space-x-2 bg-red-50 p-1.5 rounded-lg border border-red-200">
                <span className="text-[11px] text-red-800 font-medium">Reset all profiles?</span>
                <button
                  type="button"
                  onClick={handleRestoreFactoryDefaults}
                  disabled={isResetting}
                  className="px-2.5 py-1 rounded bg-red-600 text-white text-[11px] font-bold hover:bg-red-700 transition-colors"
                >
                  {isResetting ? 'Resetting...' : 'Yes, Reset All'}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmFactoryReset(false)}
                  className="px-2 py-1 rounded bg-slate-200 text-slate-700 text-[11px] font-medium hover:bg-slate-300"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>

          {/* Right Actions: Revert & Save */}
          <div className="flex items-center space-x-2.5 justify-end">
            <button
              type="button"
              onClick={handleRevertToCurrent}
              disabled={isSaving}
              className="inline-flex items-center space-x-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold text-slate-700 bg-white border border-slate-300 hover:bg-slate-100 shadow-xs transition-colors"
              title="Reverts unsaved modal edits to currently saved settings"
            >
              <RotateCcw className="w-3.5 h-3.5 text-slate-500" />
              <span>Reset Settings</span>
            </button>

            <button
              type="button"
              onClick={handleSaveAndApply}
              disabled={isSaving}
              className="inline-flex items-center space-x-1.5 px-4 py-2 rounded-lg text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 shadow-xs transition-colors"
            >
              {isSaving ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Saving &amp; Applying...</span>
                </>
              ) : (
                <>
                  <Save className="w-3.5 h-3.5" />
                  <span>Save &amp; Apply Profile</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
