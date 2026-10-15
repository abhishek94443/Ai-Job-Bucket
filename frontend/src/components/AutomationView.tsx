"use client";

import React, { useState, useEffect } from "react";
import { Zap, RefreshCw, Sparkles, CheckCircle2, Clock, AlertTriangle, Layers, Play, Check } from "lucide-react";

interface PipelineStatus {
  staged_count: number;
  enriched_count: number;
  quota_held_count: number;
  total_jobs: number;
}

interface AutomationViewProps {
  onJobsUpdated?: () => void;
}

export function AutomationView({ onJobsUpdated }: AutomationViewProps) {
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [isLoadingStatus, setIsLoadingStatus] = useState(false);
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [isEnriching, setIsEnriching] = useState(false);
  const [jobsPerCall, setJobsPerCall] = useState<number>(2);
  const [concurrency, setConcurrency] = useState<number>(2);
  const [activityLog, setActivityLog] = useState<{ id: string; time: string; text: string; type: "info" | "success" | "error" }[]>([]);

  const addLog = (text: string, type: "info" | "success" | "error" = "info") => {
    const time = new Date().toLocaleTimeString();
    setActivityLog((prev) => [{ id: Math.random().toString(), time, text, type }, ...prev.slice(0, 19)]);
  };

  const fetchStatus = async () => {
    setIsLoadingStatus(true);
    try {
      const res = await fetch("/api/pipeline/status");
      if (res.ok) {
        const data = await res.json();
        setPipelineStatus(data);
      }
    } catch (err) {
      console.error("Failed to fetch pipeline status:", err);
    } finally {
      setIsLoadingStatus(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    addLog("Automation engine initialized. Connected to pipeline API.", "info");
  }, []);

  const handleTriggerDiscovery = async () => {
    if (isDiscovering) return;
    setIsDiscovering(true);
    addLog("Starting discovery pipeline across Freehire, LinkedIn, and Indeed...", "info");

    try {
      const res = await fetch("/api/pipeline/discover", { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        addLog(data.message || "Discovery crawler launched in background.", "success");
        fetchStatus();
        if (onJobsUpdated) onJobsUpdated();
      } else {
        addLog(data.detail || "Discovery pipeline failed to trigger.", "error");
      }
    } catch (err: any) {
      addLog(err.message || "Network error while triggering discovery.", "error");
    } finally {
      setIsDiscovering(false);
    }
  };

  const handleEnrichBatch = async () => {
    if (isEnriching) return;
    setIsEnriching(true);
    addLog(`Running batch enrichment (${jobsPerCall} jobs/call, ${concurrency} parallel workers)...`, "info");

    try {
      const res = await fetch("/api/pipeline/enrich-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jobs_per_call: jobsPerCall, concurrency }),
      });
      const data = await res.json();
      if (res.ok) {
        addLog(data.message || `Batch enriched ${data.claimed_count || 0} jobs successfully.`, "success");
        fetchStatus();
        if (onJobsUpdated) onJobsUpdated();
      } else {
        addLog(data.detail || "Enrichment request returned an error.", "error");
      }
    } catch (err: any) {
      addLog(err.message || "Network error while running batch enrichment.", "error");
    } finally {
      setIsEnriching(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Header */}
      <header className="h-14 px-6 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90">
        <div className="flex items-center gap-3">
          <Zap className="w-4 h-4 text-emerald-400" />
          <span className="font-semibold text-sm text-zinc-100">Automation</span>
          <span className="text-[11px] font-mono text-zinc-500 hidden sm:inline">
            Job Crawlers &amp; Batch AI Enrichment
          </span>
        </div>

        <button
          onClick={fetchStatus}
          disabled={isLoadingStatus}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoadingStatus ? "animate-spin" : ""}`} />
          <span>Refresh Queue</span>
        </button>
      </header>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 max-w-6xl mx-auto w-full">
        {/* Pipeline Telemetry Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="p-4 rounded-lg bg-zinc-900/60 border border-zinc-800">
            <div className="flex items-center justify-between text-zinc-500 mb-2">
              <span className="text-[11px] font-mono uppercase tracking-wider">Total Discovered</span>
              <Layers className="w-3.5 h-3.5 text-zinc-400" />
            </div>
            <div className="text-2xl font-bold font-mono text-zinc-100">
              {pipelineStatus?.total_jobs ?? "-"}
            </div>
            <p className="text-[11px] text-zinc-500 mt-1">Jobs collected in database</p>
          </div>

          <div className="p-4 rounded-lg bg-zinc-900/60 border border-zinc-800">
            <div className="flex items-center justify-between text-zinc-500 mb-2">
              <span className="text-[11px] font-mono uppercase tracking-wider">Staged Queue</span>
              <Clock className="w-3.5 h-3.5 text-amber-400" />
            </div>
            <div className="text-2xl font-bold font-mono text-amber-300">
              {pipelineStatus?.staged_count ?? "-"}
            </div>
            <p className="text-[11px] text-zinc-500 mt-1">Awaiting AI scoring &amp; extraction</p>
          </div>

          <div className="p-4 rounded-lg bg-zinc-900/60 border border-zinc-800">
            <div className="flex items-center justify-between text-zinc-500 mb-2">
              <span className="text-[11px] font-mono uppercase tracking-wider">Enriched &amp; Ready</span>
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            </div>
            <div className="text-2xl font-bold font-mono text-emerald-300">
              {pipelineStatus?.enriched_count ?? "-"}
            </div>
            <p className="text-[11px] text-zinc-500 mt-1">Fully matched and ready to tailor</p>
          </div>

          <div className="p-4 rounded-lg bg-zinc-900/60 border border-zinc-800">
            <div className="flex items-center justify-between text-zinc-500 mb-2">
              <span className="text-[11px] font-mono uppercase tracking-wider">Quota Held</span>
              <AlertTriangle className="w-3.5 h-3.5 text-zinc-500" />
            </div>
            <div className="text-2xl font-bold font-mono text-zinc-400">
              {pipelineStatus?.quota_held_count ?? "-"}
            </div>
            <p className="text-[11px] text-zinc-500 mt-1">Paused due to rate limits</p>
          </div>
        </div>

        {/* Pipeline Controls Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Stage 1: Discovery Crawler */}
          <div className="p-5 rounded-lg bg-zinc-900/40 border border-zinc-800 flex flex-col justify-between space-y-4">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-emerald-400 uppercase tracking-wider font-semibold">
                  Stage 1
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-zinc-800 text-zinc-300">
                  Automated Scraper
                </span>
              </div>
              <h3 className="text-base font-semibold text-zinc-100">Job Board Discovery</h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                Executes background crawlers across Freehire, LinkedIn, Indeed, and direct engineering career pages to discover active openings matching your profile keywords.
              </p>
            </div>

            <div className="pt-2 border-t border-zinc-800/80 flex items-center justify-between">
              <span className="text-[11px] font-mono text-zinc-500">
                {isDiscovering ? "Crawler active..." : "Ready to run"}
              </span>
              <button
                onClick={handleTriggerDiscovery}
                disabled={isDiscovering}
                className="flex items-center gap-2 px-4 py-2 rounded bg-zinc-100 hover:bg-white text-zinc-950 text-xs font-semibold transition-colors disabled:opacity-50"
              >
                {isDiscovering ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Discovering...</span>
                  </>
                ) : (
                  <>
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>Run Discovery</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Stage 2: Batch AI Enrichment */}
          <div className="p-5 rounded-lg bg-zinc-900/40 border border-zinc-800 flex flex-col justify-between space-y-4">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-emerald-400 uppercase tracking-wider font-semibold">
                  Stage 2
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-zinc-800 text-zinc-300">
                  AI Matching &amp; Extraction
                </span>
              </div>
              <h3 className="text-base font-semibold text-zinc-100">Batch AI Enrichment</h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                Processes staged job descriptions through AI to extract verified requirements, compute match scores against your profile facts, and identify skill gaps.
              </p>
            </div>

            {/* Batch Parameters */}
            <div className="p-3 bg-zinc-950 rounded border border-zinc-800/80 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-zinc-400">Jobs Per API Call:</span>
                <select
                  value={jobsPerCall}
                  onChange={(e) => setJobsPerCall(Number(e.target.value))}
                  disabled={isEnriching}
                  className="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs font-mono text-zinc-200 focus:outline-none"
                >
                  <option value={1}>1 Job / Call</option>
                  <option value={2}>2 Jobs / Call</option>
                </select>
              </div>

              <div className="flex items-center justify-between text-xs">
                <span className="text-zinc-400">Parallel Workers:</span>
                <select
                  value={concurrency}
                  onChange={(e) => setConcurrency(Number(e.target.value))}
                  disabled={isEnriching}
                  className="bg-zinc-900 border border-zinc-700 rounded px-2 py-0.5 text-xs font-mono text-zinc-200 focus:outline-none"
                >
                  <option value={1}>1 Worker</option>
                  <option value={2}>2 Workers</option>
                </select>
              </div>

              <div className="pt-1 border-t border-zinc-800 flex items-center justify-between text-[11px] font-mono text-zinc-400">
                <span>Wave Output:</span>
                <span className="text-emerald-400 font-semibold">{jobsPerCall * concurrency} jobs / batch</span>
              </div>
            </div>

            <div className="pt-2 border-t border-zinc-800/80 flex items-center justify-between">
              <span className="text-[11px] font-mono text-zinc-500">
                {isEnriching ? "Enriching batch..." : `${pipelineStatus?.staged_count || 0} jobs queued`}
              </span>
              <button
                onClick={handleEnrichBatch}
                disabled={isEnriching || (pipelineStatus?.staged_count === 0 && pipelineStatus?.quota_held_count === 0)}
                className="flex items-center gap-2 px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {isEnriching ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Enriching...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Run Batch Enrichment</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Live Automation Activity Log */}
        <div className="rounded-lg bg-zinc-900/40 border border-zinc-800 overflow-hidden">
          <div className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between bg-zinc-900/60">
            <span className="text-xs font-semibold text-zinc-200">Automation Activity Stream</span>
            <span className="text-[11px] font-mono text-zinc-500">Real-time pipeline events</span>
          </div>
          <div className="p-4 font-mono text-xs space-y-2 max-h-52 overflow-y-auto">
            {activityLog.length === 0 ? (
              <div className="text-zinc-600">No events logged yet.</div>
            ) : (
              activityLog.map((log) => (
                <div key={log.id} className="flex items-start gap-2.5 leading-relaxed">
                  <span className="text-zinc-500 text-[11px] shrink-0">{log.time}</span>
                  <span
                    className={
                      log.type === "success"
                        ? "text-emerald-400"
                        : log.type === "error"
                        ? "text-rose-400"
                        : "text-zinc-300"
                    }
                  >
                    {log.text}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
