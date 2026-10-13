"use client";

import React, { useState, useEffect } from "react";
import { Cpu, RefreshCw, Check, AlertCircle, Shield, Plus, Trash2, Key, ChevronUp, ChevronDown, X } from "lucide-react";

interface AIModel {
  id: number;
  provider: string;
  model_id: string;
  api_key_alias: string;
  rpm_limit: number;
  rpd_limit: number;
  calls_today: number;
  priority: number;
  is_active: boolean;
  status_note?: string;
  last_called_at?: string;
}

interface KeyStatus {
  alias: string;
  is_configured: boolean;
  masked: string;
  length: number;
}

export function ModelManagementView() {
  const [models, setModels] = useState<AIModel[]>([]);
  const [keys, setKeys] = useState<Record<string, KeyStatus>>({});
  const [isLoading, setIsLoading] = useState(true);

  // Modal states
  const [showAddModelModal, setShowAddModelModal] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [selectedKeyAlias, setSelectedKeyAlias] = useState<string>("GEMINI_API_KEY_1");
  const [newKeyValue, setNewKeyValue] = useState<string>("");
  const [isSavingKey, setIsSavingKey] = useState(false);

  // New model form state
  const [newModelProvider, setNewModelProvider] = useState("google");
  const [newModelId, setNewModelId] = useState("");
  const [newModelKeyAlias, setNewModelKeyAlias] = useState("GEMINI_API_KEY");
  const [newModelPriority, setNewModelPriority] = useState<number>(6);
  const [newModelRpm, setNewModelRpm] = useState<number>(15);
  const [newModelRpd, setNewModelRpd] = useState<number>(500);
  const [isAddingModel, setIsAddingModel] = useState(false);

  // Notification status
  const [statusMsg, setStatusMsg] = useState<{ text: string; type: "success" | "error" } | null>(null);

  const showStatus = (text: string, type: "success" | "error" = "success") => {
    setStatusMsg({ text, type });
    setTimeout(() => setStatusMsg(null), 3500);
  };

  const loadModels = async () => {
    try {
      const res = await fetch("/api/models");
      if (res.ok) {
        const data = await res.json();
        setModels(data.models || []);
      }
    } catch (err) {
      console.error("Failed to load models", err);
    }
  };

  const loadKeys = async () => {
    try {
      const res = await fetch("/api/keys");
      if (res.ok) {
        const data = await res.json();
        setKeys(data.keys || {});
      }
    } catch (err) {
      console.error("Failed to load keys", err);
    }
  };

  const refreshAll = async () => {
    setIsLoading(true);
    await Promise.all([loadModels(), loadKeys()]);
    setIsLoading(false);
  };

  useEffect(() => {
    refreshAll();
  }, []);

  // Helper to filter only CONFIGURED keys belonging to the selected provider
  const getConfiguredKeysForProvider = (provider: string) => {
    return Object.entries(keys).filter(([alias, k]) => {
      if (!k.is_configured) return false;
      if (provider === "google") return alias.startsWith("GEMINI_API_KEY");
      if (provider === "openai") return alias.startsWith("OPENAI_API_KEY");
      if (provider === "anthropic") return alias.startsWith("ANTHROPIC_API_KEY");
      return true;
    });
  };

  // Keep newModelKeyAlias in sync when provider or key configurations change
  useEffect(() => {
    const avail = getConfiguredKeysForProvider(newModelProvider);
    if (avail.length > 0) {
      if (!avail.some(([alias]) => alias === newModelKeyAlias)) {
        setNewModelKeyAlias(avail[0][0]);
      }
    } else {
      setNewModelKeyAlias("");
    }
  }, [newModelProvider, keys]);

  // Update model active state
  const handleToggleActive = async (model: AIModel) => {
    try {
      const res = await fetch(`/api/models/${model.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !model.is_active }),
      });
      if (res.ok) {
        setModels((prev) =>
          prev.map((m) => (m.id === model.id ? { ...m, is_active: !m.is_active } : m))
        );
        showStatus(`Model ${model.model_id} ${!model.is_active ? "enabled" : "disabled"}.`);
      }
    } catch (err) {
      console.error(err);
      showStatus("Failed to toggle model active state.", "error");
    }
  };

  // Change priority via atomic swap and reorder
  const handleChangePriority = async (model: AIModel, delta: number) => {
    const currentIdx = models.findIndex((m) => m.id === model.id);
    if (currentIdx === -1) return;

    const targetIdx = currentIdx + delta;
    if (targetIdx < 0 || targetIdx >= models.length) return;

    // Create a swapped copy
    const newModels = [...models];
    const temp = newModels[currentIdx];
    newModels[currentIdx] = newModels[targetIdx];
    newModels[targetIdx] = temp;

    // Optimistically update local priority numbering
    const reordered = newModels.map((m, idx) => ({ ...m, priority: idx + 1 }));
    setModels(reordered);

    try {
      const res = await fetch("/api/models/reorder", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_ids: reordered.map((m) => m.id) }),
      });
      if (res.ok) {
        showStatus(`Reordered waterfall priority: ${model.model_id} is now Priority ${targetIdx + 1}.`);
      } else {
        await loadModels();
        showStatus("Failed to persist model reordering.", "error");
      }
    } catch (err) {
      console.error(err);
      await loadModels();
      showStatus("Network error updating priority.", "error");
    }
  };

  // Delete model
  const handleDeleteModel = async (model: AIModel) => {
    if (!window.confirm(`Remove ${model.model_id} (Priority ${model.priority}) from AI Gateway registry?`)) {
      return;
    }

    try {
      const res = await fetch(`/api/models/${model.id}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setModels((prev) => prev.filter((m) => m.id !== model.id));
        showStatus(`Removed ${model.model_id} from registry.`);
      } else {
        showStatus("Failed to remove model.", "error");
      }
    } catch (err) {
      console.error(err);
      showStatus("Error communicating with server.", "error");
    }
  };

  // Submit new model
  const handleAddNewModel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newModelId.trim()) {
      showStatus("Model ID is required.", "error");
      return;
    }

    if (!newModelKeyAlias) {
      showStatus("A configured API key must be selected.", "error");
      return;
    }

    setIsAddingModel(true);
    try {
      const res = await fetch("/api/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: newModelProvider,
          model_id: newModelId.trim(),
          api_key_alias: newModelKeyAlias,
          priority: Number(newModelPriority) || 1,
          rpm_limit: Number(newModelRpm) || 15,
          rpd_limit: Number(newModelRpd) || 500,
        }),
      });

      if (res.ok) {
        showStatus(`Added ${newModelId} to registry under ${newModelKeyAlias}.`);
        setShowAddModelModal(false);
        setNewModelId("");
        await loadModels();
      } else {
        const errData = await res.json().catch(() => ({ detail: "Failed to add model" }));
        showStatus(errData.detail || "Failed to add model.", "error");
      }
    } catch (err) {
      console.error(err);
      showStatus("Network error adding model.", "error");
    } finally {
      setIsAddingModel(false);
    }
  };

  // Save API key
  const handleSaveKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKeyValue.trim()) {
      showStatus("API Key value cannot be empty.", "error");
      return;
    }

    setIsSavingKey(true);
    try {
      const res = await fetch("/api/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alias: selectedKeyAlias,
          key_value: newKeyValue.trim(),
        }),
      });

      if (res.ok) {
        showStatus(`Successfully saved key for ${selectedKeyAlias}.`);
        setShowKeyModal(false);
        setNewKeyValue("");
        await loadKeys();
      } else {
        showStatus("Failed to update API key.", "error");
      }
    } catch (err) {
      console.error(err);
      showStatus("Network error updating key.", "error");
    } finally {
      setIsSavingKey(false);
    }
  };

  const configuredKeysForCurrentProvider = getConfiguredKeysForProvider(newModelProvider);

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Header */}
      <header className="h-14 px-6 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90">
        <div className="flex items-center gap-3">
          <Cpu className="w-4 h-4 text-zinc-400" />
          <span className="font-semibold text-sm text-zinc-100">AI Model Registry &amp; Multi-Key Quotas</span>
          <span className="text-[11px] font-mono text-zinc-500">Waterfall Priority &amp; Quota Governance</span>
        </div>

        <div className="flex items-center gap-2">
          {statusMsg && (
            <div
              className={`px-2.5 py-1 rounded text-xs font-mono border transition-opacity ${
                statusMsg.type === "success"
                  ? "bg-emerald-950 text-emerald-300 border-emerald-800"
                  : "bg-red-950 text-red-300 border-red-800"
              }`}
            >
              {statusMsg.text}
            </div>
          )}

          <button
            onClick={() => {
              setNewModelPriority(models.length + 1);
              setShowAddModelModal(true);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold bg-zinc-100 hover:bg-white text-zinc-950 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Add Model</span>
          </button>

          <button
            onClick={refreshAll}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        </div>
      </header>

      {/* Main Scrollable View */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Architecture Note */}
        <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded text-xs text-zinc-300 leading-relaxed font-sans">
          The AI Gateway routes tailoring generation across active models in ascending priority order. If an endpoint encounters a 429 rate limit or 503 capacity spike, the non-blocking circuit breaker trips a brief cooldown and cascades to the next priority tier. Quotas reset daily at UTC midnight boundary.
        </div>

        {/* Section 1: Active Model Waterfall Table */}
        <div className="space-y-3">
          <div className="flex items-center justify-between text-xs font-mono text-zinc-400">
            <span className="font-semibold text-zinc-200">Waterfall Priority Order ({models.length} Registered)</span>
            <span className="text-[11px] text-zinc-500">Use arrows to swap priority ranks atomically</span>
          </div>

          <div className="border border-zinc-800 rounded overflow-hidden">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-zinc-900/80 text-zinc-400 border-b border-zinc-800 uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="px-4 py-3 w-28">Priority</th>
                  <th className="px-4 py-3">Model ID</th>
                  <th className="px-4 py-3">Provider</th>
                  <th className="px-4 py-3">Key Alias</th>
                  <th className="px-4 py-3">Calls Today / RPD</th>
                  <th className="px-4 py-3">RPM Limit</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/80 bg-zinc-950">
                {models.map((m, idx) => {
                  const percent = m.rpd_limit > 0 ? Math.round((m.calls_today / m.rpd_limit) * 100) : 0;
                  return (
                    <tr key={m.id} className="hover:bg-zinc-900/40 transition-colors">
                      {/* Priority with Up/Down Controls */}
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <span className="w-6 px-1.5 py-0.5 rounded text-center text-[11px] font-semibold bg-zinc-900 border border-zinc-700 text-zinc-200">
                            {m.priority}
                          </span>
                          <div className="flex flex-col">
                            <button
                              onClick={() => handleChangePriority(m, -1)}
                              disabled={idx === 0}
                              title="Move up in priority (swap with previous)"
                              className="p-0.5 hover:text-zinc-100 text-zinc-500 disabled:opacity-20 disabled:hover:text-zinc-500"
                            >
                              <ChevronUp className="w-3 h-3" />
                            </button>
                            <button
                              onClick={() => handleChangePriority(m, 1)}
                              disabled={idx === models.length - 1}
                              title="Move down in priority (swap with next)"
                              className="p-0.5 hover:text-zinc-100 text-zinc-500 disabled:opacity-20 disabled:hover:text-zinc-500"
                            >
                              <ChevronDown className="w-3 h-3" />
                            </button>
                          </div>
                        </div>
                      </td>

                      {/* Model ID */}
                      <td className="px-4 py-3 text-zinc-100 font-medium">{m.model_id}</td>

                      {/* Provider */}
                      <td className="px-4 py-3 text-zinc-400 uppercase text-[11px]">{m.provider}</td>

                      {/* Key Alias */}
                      <td className="px-4 py-3">
                        <span className="px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-[10px] text-zinc-300">
                          {m.api_key_alias}
                        </span>
                      </td>

                      {/* Calls Today / RPD */}
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="text-zinc-200">
                            {m.calls_today} / {m.rpd_limit}
                          </span>
                          <span className="text-[10px] text-zinc-500">({percent}%)</span>
                        </div>
                      </td>

                      {/* RPM Limit */}
                      <td className="px-4 py-3 text-zinc-300">{m.rpm_limit}/min</td>

                      {/* Active Status */}
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] ${
                            m.is_active
                              ? "bg-emerald-950 text-emerald-400 border border-emerald-800/80"
                              : "bg-zinc-900 text-zinc-500 border border-zinc-800"
                          }`}
                        >
                          {m.is_active ? "ACTIVE" : "DISABLED"}
                        </span>
                      </td>

                      {/* Actions */}
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            onClick={() => handleToggleActive(m)}
                            className="px-2 py-0.5 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 text-[11px] transition-colors"
                          >
                            {m.is_active ? "Disable" : "Enable"}
                          </button>
                          <button
                            onClick={() => handleDeleteModel(m)}
                            title="Delete model from registry"
                            className="p-1 rounded bg-zinc-900 hover:bg-red-950 text-zinc-500 hover:text-red-400 border border-zinc-800 hover:border-red-800 transition-colors"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Section 2: API Keys & Provider Secrets */}
        <div className="space-y-3 pt-2">
          <div className="flex items-center justify-between text-xs font-mono text-zinc-400">
            <div className="flex items-center gap-2">
              <Key className="w-3.5 h-3.5 text-zinc-300" />
              <span className="font-semibold text-zinc-200">API Key Vault &amp; Provider Slots</span>
            </div>
            <span className="text-[11px] text-zinc-500">Encrypted token persistence in local environment</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {Object.entries(keys).map(([alias, k]) => (
              <div
                key={alias}
                className="p-3.5 bg-zinc-900/60 border border-zinc-800 rounded flex flex-col justify-between gap-3 text-xs font-mono"
              >
                <div>
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <span className="font-semibold text-zinc-200 text-[11px]">{alias}</span>
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded border ${
                        k.is_configured
                          ? "bg-emerald-950/80 text-emerald-400 border-emerald-800"
                          : "bg-zinc-900 text-zinc-500 border-zinc-800"
                      }`}
                    >
                      {k.is_configured ? "CONFIGURED" : "NOT SET"}
                    </span>
                  </div>
                  <div className="text-[11px] text-zinc-400 font-mono">
                    {k.is_configured ? "Secret verified in .env" : "No secret token present"}
                  </div>
                </div>

                <button
                  onClick={() => {
                    setSelectedKeyAlias(alias);
                    setNewKeyValue("");
                    setShowKeyModal(true);
                  }}
                  className="w-full py-1 px-2 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 text-[11px] text-center transition-colors"
                >
                  {k.is_configured ? "Update Key" : "Set Key"}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Modal: Add New Model */}
      {showAddModelModal && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-md bg-zinc-950 border border-zinc-800 rounded-lg shadow-2xl p-5 text-xs font-mono animate-in fade-in duration-150">
            <div className="flex items-center justify-between pb-3 border-b border-zinc-800 mb-4">
              <span className="font-semibold text-sm text-zinc-100">Add Model to AI Gateway</span>
              <button
                onClick={() => setShowAddModelModal(false)}
                className="text-zinc-500 hover:text-zinc-200 p-1"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleAddNewModel} className="space-y-3.5">
              <div>
                <label className="block text-zinc-400 mb-1">Provider Adapter</label>
                <select
                  value={newModelProvider}
                  onChange={(e) => {
                    const prov = e.target.value;
                    setNewModelProvider(prov);
                  }}
                  className="w-full px-2.5 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-100 focus:outline-none focus:border-zinc-700"
                >
                  <option value="google">Google (Gemini)</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-zinc-400">Model Identifier</label>
                  <span className="text-[10px] text-zinc-500">e.g. gemini-2.5-flash</span>
                </div>
                <input
                  type="text"
                  placeholder="e.g. gemini-2.5-flash or gpt-4o-mini"
                  value={newModelId}
                  onChange={(e) => setNewModelId(e.target.value)}
                  className="w-full px-2.5 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700"
                  required
                />
                {/* Preset quick pills */}
                <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                  {["gemini-2.5-flash", "gemini-2.5-pro", "gpt-4o-mini", "claude-3-5-haiku"].map((pill) => (
                    <button
                      type="button"
                      key={pill}
                      onClick={() => {
                        setNewModelId(pill);
                        if (pill.startsWith("gemini")) setNewModelProvider("google");
                        else if (pill.startsWith("gpt")) setNewModelProvider("openai");
                        else if (pill.startsWith("claude")) setNewModelProvider("anthropic");
                      }}
                      className="px-1.5 py-0.5 rounded bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-[10px] text-zinc-400"
                    >
                      {pill}
                    </button>
                  ))}
                </div>
              </div>

              {/* API Key Slot: strictly restricted to verified configured keys for this provider */}
              <div>
                <label className="block text-zinc-400 mb-1">API Key Slot</label>
                {configuredKeysForCurrentProvider.length > 0 ? (
                  <select
                    value={newModelKeyAlias}
                    onChange={(e) => setNewModelKeyAlias(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-100 focus:outline-none focus:border-zinc-700"
                  >
                    {configuredKeysForCurrentProvider.map(([alias]) => (
                      <option key={alias} value={alias}>
                        {alias} (Verified &amp; Active)
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="p-2.5 rounded bg-zinc-900/80 border border-amber-900/40 text-zinc-400 text-[11px] leading-relaxed">
                    <span className="text-amber-400 font-semibold block mb-0.5">
                      No Active Key Configured for {newModelProvider.toUpperCase()}
                    </span>
                    Please set and save an API key for {newModelProvider} in the API Key Vault below before registering models for this provider.
                  </div>
                )}
              </div>

              <div className="grid grid-cols-3 gap-2.5">
                <div>
                  <label className="block text-zinc-400 mb-1">Priority</label>
                  <input
                    type="number"
                    min="1"
                    max="99"
                    value={newModelPriority}
                    onChange={(e) => setNewModelPriority(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-100 focus:outline-none focus:border-zinc-700"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">RPM Limit</label>
                  <input
                    type="number"
                    min="1"
                    value={newModelRpm}
                    onChange={(e) => setNewModelRpm(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-100 focus:outline-none focus:border-zinc-700"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">RPD Limit</label>
                  <input
                    type="number"
                    min="1"
                    value={newModelRpd}
                    onChange={(e) => setNewModelRpd(Number(e.target.value))}
                    className="w-full px-2 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-100 focus:outline-none focus:border-zinc-700"
                  />
                </div>
              </div>

              <div className="pt-3 flex items-center justify-end gap-2 border-t border-zinc-800 mt-4">
                <button
                  type="button"
                  onClick={() => setShowAddModelModal(false)}
                  className="px-3 py-1.5 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isAddingModel || configuredKeysForCurrentProvider.length === 0}
                  className="px-3.5 py-1.5 rounded bg-zinc-100 hover:bg-white text-zinc-950 font-semibold transition-colors disabled:opacity-40"
                >
                  {isAddingModel ? "Registering..." : "Add to Registry"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Set / Update API Key */}
      {showKeyModal && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-md bg-zinc-950 border border-zinc-800 rounded-lg shadow-2xl p-5 text-xs font-mono animate-in fade-in duration-150">
            <div className="flex items-center justify-between pb-3 border-b border-zinc-800 mb-4">
              <div>
                <span className="font-semibold text-sm text-zinc-100">Set API Key</span>
                <div className="text-[11px] text-zinc-400 font-mono mt-0.5">{selectedKeyAlias}</div>
              </div>
              <button
                onClick={() => setShowKeyModal(false)}
                className="text-zinc-500 hover:text-zinc-200 p-1"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveKey} className="space-y-4">
              <div>
                <label className="block text-zinc-400 mb-1">Secret Key Value</label>
                <input
                  type="password"
                  placeholder="Paste secret token (e.g. AIzaSy...)"
                  value={newKeyValue}
                  onChange={(e) => setNewKeyValue(e.target.value)}
                  className="w-full px-2.5 py-2 rounded bg-zinc-900 border border-zinc-800 text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-700 font-mono"
                  required
                  autoFocus
                />
                <div className="text-[10px] text-zinc-500 mt-1">
                  Key is saved atomically to local .env and updated in process memory immediately.
                </div>
              </div>

              <div className="pt-2 flex items-center justify-end gap-2 border-t border-zinc-800">
                <button
                  type="button"
                  onClick={() => setShowKeyModal(false)}
                  className="px-3 py-1.5 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSavingKey}
                  className="px-3.5 py-1.5 rounded bg-zinc-100 hover:bg-white text-zinc-950 font-semibold transition-colors disabled:opacity-50"
                >
                  {isSavingKey ? "Saving..." : "Save Key"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
