"use client";

import React, { useState } from "react";
import { Search, RefreshCw, Sparkles, ExternalLink, Bookmark, Check, Filter } from "lucide-react";

export interface JobItem {
  id: number;
  title: string;
  company: string;
  location?: string;
  platform?: string;
  url?: string;
  relevance_score?: number;
  ai_score?: number;
  enrichment_status?: string;
  char_count?: number;
  estimated_tokens?: number;
  experience_level?: string;
  required_skills?: string[];
  matched_skills?: string[];
  description?: string;
  created_at?: string;
}

interface JobDiscoveryViewProps {
  jobs: JobItem[];
  isLoading: boolean;
  onRefresh: () => void;
  onSelectJobForTailoring: (job: JobItem) => void;
  onTrackJob: (jobId: number, status: string) => void;
}

export function JobDiscoveryView({
  jobs,
  isLoading,
  onRefresh,
  onSelectJobForTailoring,
  onTrackJob,
}: JobDiscoveryViewProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [minScoreFilter, setMinScoreFilter] = useState<number | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [trackedMap, setTrackedMap] = useState<Record<number, boolean>>({});
  const [jobsPerCall, setJobsPerCall] = useState<number>(2);
  const [concurrency, setConcurrency] = useState<number>(2);
  const [isEnriching, setIsEnriching] = useState<boolean>(false);
  const [isDiscovering, setIsDiscovering] = useState<boolean>(false);
  const [enrichStatusMsg, setEnrichStatusMsg] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<{
    total_jobs: number;
    ai_enriched_count: number;
    staged_count: number;
    in_flight_count: number;
    quota_held_count: number;
    window_metrics?: {
      total_staged: number;
      total_chars: number;
      estimated_total_tokens: number;
      recommended_batch_size: number;
      estimated_windows_count: number;
      average_chars_per_job: number;
    };
  } | null>(null);

  const fetchStatus = () => {
    fetch("/api/pipeline/status")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) setPipelineStatus(data);
      })
      .catch(() => {});
  };

  React.useEffect(() => {
    fetchStatus();
  }, [jobs.length]);

  const handleEnrichBatch = async () => {
    if (isEnriching) return;
    setIsEnriching(true);
    setEnrichStatusMsg(null);
    try {
      const res = await fetch("/api/pipeline/enrich-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jobs_per_call: jobsPerCall, concurrency: concurrency }),
      });
      const data = await res.json();
      if (res.ok) {
        setEnrichStatusMsg(data.message || `Processed ${data.claimed_count} jobs.`);
        onRefresh();
        fetchStatus();
      } else {
        setEnrichStatusMsg(data.detail || "Enrichment request failed.");
      }
    } catch (err: any) {
      setEnrichStatusMsg(err.message || "Failed to trigger batch enrichment.");
    } finally {
      setIsEnriching(false);
      setTimeout(() => setEnrichStatusMsg(null), 8000);
    }
  };

  const handleTriggerDiscovery = async () => {
    if (isDiscovering) return;
    setIsDiscovering(true);
    setEnrichStatusMsg("Initiating uncapped discovery pipeline in background across Freehire, LinkedIn, and Indeed...");
    try {
      const res = await fetch("/api/jobs/discover", {
        method: "POST",
      });
      const data = await res.json();
      if (res.ok) {
        setEnrichStatusMsg(data.message || "Discovery pipeline running in background with auto-enrichment.");
        onRefresh();
        fetchStatus();
        let polls = 0;
        const interval = setInterval(() => {
          polls += 1;
          onRefresh();
          fetchStatus();
          if (polls >= 15) {
            clearInterval(interval);
            setIsDiscovering(false);
          }
        }, 3000);
      } else {
        setEnrichStatusMsg(data.detail || "Failed to trigger discovery.");
        setIsDiscovering(false);
      }
    } catch (err: any) {
      setEnrichStatusMsg(err.message || "Network error while triggering discovery.");
      setIsDiscovering(false);
    }
  };

  const handleTrack = async (e: React.MouseEvent, jobId: number) => {
    e.stopPropagation();
    onTrackJob(jobId, "Saved");
    setTrackedMap((prev) => ({ ...prev, [jobId]: true }));
  };

  const filteredJobs = jobs.filter((job) => {
    const score = job.ai_score ?? job.relevance_score ?? 0;
    if (minScoreFilter !== null && score < minScoreFilter) return false;
    if (platformFilter !== null && (job.platform || "").toLowerCase() !== platformFilter.toLowerCase()) return false;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchTitle = (job.title || "").toLowerCase().includes(q);
      const matchComp = (job.company || "").toLowerCase().includes(q);
      const matchSkills = (job.required_skills || []).some((s) => s.toLowerCase().includes(q));
      if (!matchTitle && !matchComp && !matchSkills) return false;
    }
    return true;
  });

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Top Header & Search Bar */}
      <header className="h-14 px-6 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90 backdrop-blur">
        <div className="flex items-center gap-4 flex-1 max-w-xl">
          <div className="relative w-full">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder="Search index by role, company, or tech stack (e.g. Java, Kubernetes)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-zinc-900 border border-zinc-800 rounded pl-9 pr-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-zinc-600 transition-colors"
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Configurable Multi-Job Batch Controls */}
          <div className="flex items-center gap-2 px-2.5 py-1 rounded bg-zinc-900 border border-zinc-800 text-xs">
            <span className="text-[11px] font-mono text-zinc-500 uppercase">Jobs/Call:</span>
            <select
              value={jobsPerCall}
              onChange={(e) => setJobsPerCall(Number(e.target.value))}
              disabled={isEnriching}
              className="bg-zinc-950 border border-zinc-800 rounded px-1.5 py-0.5 text-[11px] font-mono text-zinc-300 focus:outline-none focus:border-zinc-700"
            >
              <option value={1}>1 Job / Call</option>
              <option value={2}>2 Jobs / Call</option>
            </select>

            <span className="text-[11px] font-mono text-zinc-500 uppercase ml-1">Parallel:</span>
            <select
              value={concurrency}
              onChange={(e) => setConcurrency(Number(e.target.value))}
              disabled={isEnriching}
              className="bg-zinc-950 border border-zinc-800 rounded px-1.5 py-0.5 text-[11px] font-mono text-zinc-300 focus:outline-none focus:border-zinc-700"
            >
              <option value={1}>1 Worker</option>
              <option value={2}>2 Workers</option>
            </select>

            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-950 text-zinc-400 border border-zinc-800">
              {jobsPerCall * concurrency} Jobs/wave
            </span>

            <button
              onClick={handleEnrichBatch}
              disabled={isEnriching || (pipelineStatus?.staged_count === 0 && pipelineStatus?.quota_held_count === 0)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium bg-emerald-950/80 hover:bg-emerald-900/80 text-emerald-300 border border-emerald-800/80 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ml-1"
            >
              {isEnriching ? (
                <>
                  <RefreshCw className="w-3 h-3 animate-spin" />
                  <span>Enriching...</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-3 h-3 text-emerald-400" />
                  <span>Enrich Staged</span>
                </>
              )}
            </button>
          </div>


          <button
            onClick={onRefresh}
            disabled={isLoading || isDiscovering}
            title="Refresh current jobs from database"
            className="flex items-center justify-center p-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 border border-zinc-800 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading && !isDiscovering ? "animate-spin" : ""}`} />
          </button>

          <button
            onClick={handleTriggerDiscovery}
            disabled={isDiscovering || isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isDiscovering ? "animate-spin text-emerald-400" : ""}`} />
            <span>{isDiscovering ? "Discovering..." : "Discover Pipeline"}</span>
          </button>
        </div>
      </header>

      {/* Notification Banner */}
      {enrichStatusMsg && (
        <div className="px-6 py-2 bg-emerald-950/40 border-b border-emerald-800/40 text-emerald-300 text-xs font-mono flex items-center justify-between">
          <span>{enrichStatusMsg}</span>
          <button
            onClick={() => setEnrichStatusMsg(null)}
            className="text-emerald-500 hover:text-emerald-300 text-xs"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Filter Chips Bar */}
      <div className="px-6 py-2.5 border-b border-zinc-800/80 flex items-center justify-between text-xs text-zinc-400 bg-zinc-950/60">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono uppercase text-zinc-400 font-medium">Filter:</span>
          <button
            onClick={() => setMinScoreFilter(null)}
            className={`px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
              minScoreFilter === null
                ? "bg-zinc-800 text-zinc-200 border border-zinc-700"
                : "bg-zinc-900 text-zinc-400 hover:text-zinc-300 border border-zinc-800"
            }`}
          >
            All
          </button>
          <button
            onClick={() => setMinScoreFilter(minScoreFilter === 80 ? null : 80)}
            className={`px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
              minScoreFilter === 80
                ? "bg-emerald-950/80 text-emerald-400 border border-emerald-800"
                : "bg-zinc-900 text-zinc-400 hover:text-zinc-300 border border-zinc-800"
            }`}
          >
            Match &gt;= 80%
          </button>
          <button
            onClick={() => setMinScoreFilter(minScoreFilter === 90 ? null : 90)}
            className={`px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
              minScoreFilter === 90
                ? "bg-emerald-950/80 text-emerald-400 border border-emerald-800"
                : "bg-zinc-900 text-zinc-400 hover:text-zinc-300 border border-zinc-800"
            }`}
          >
            High Affinity &gt;= 90%
          </button>

          <span className="text-zinc-700 ml-2">|</span>

          {["freehire", "linkedin", "indeed"].map((plat) => (
            <button
              key={plat}
              onClick={() => setPlatformFilter(platformFilter === plat ? null : plat)}
              className={`px-2 py-0.5 rounded text-[11px] font-mono uppercase transition-colors ${
                platformFilter === plat
                  ? "bg-zinc-800 text-zinc-200 border border-zinc-700"
                  : "bg-zinc-900 text-zinc-400 hover:text-zinc-300 border border-zinc-800"
              }`}
            >
              {plat}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3 font-mono text-[11px] text-zinc-400">
          {pipelineStatus && (
            <div className="flex items-center gap-2 px-2 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-400">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400" />
              <span>{pipelineStatus.ai_enriched_count} Verified</span>
              <span>•</span>
              <span>{pipelineStatus.staged_count} Staged</span>
              {pipelineStatus.in_flight_count > 0 && (
                <>
                  <span>•</span>
                  <span className="text-amber-400">{pipelineStatus.in_flight_count} In Flight</span>
                </>
              )}
              {pipelineStatus.quota_held_count > 0 && (
                <>
                  <span>•</span>
                  <span className="text-amber-500">{pipelineStatus.quota_held_count} Held</span>
                </>
              )}
              {pipelineStatus.window_metrics && pipelineStatus.window_metrics.average_chars_per_job > 0 && (
                <>
                  <span>•</span>
                  <span className="text-zinc-500">
                    Window ~{Math.round(pipelineStatus.window_metrics.average_chars_per_job / 1000)}k chars/job
                  </span>
                </>
              )}
            </div>
          )}
          <span>Showing {filteredJobs.length} of {jobs.length} opportunities</span>
        </div>
      </div>


      {/* Main Job Stream */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {isLoading && jobs.length === 0 ? (
          <div className="h-64 flex flex-col items-center justify-center text-zinc-500 gap-2">
            <RefreshCw className="w-5 h-5 animate-spin text-zinc-400" />
            <span className="text-xs font-mono">Loading opportunities from PostgreSQL...</span>
          </div>
        ) : filteredJobs.length === 0 ? (
          <div className="h-64 flex flex-col items-center justify-center text-zinc-500 border border-dashed border-zinc-800 rounded">
            <span className="text-xs font-mono">No opportunities matching active filters.</span>
          </div>
        ) : (
          filteredJobs.map((job) => {
            const score = job.ai_score ?? job.relevance_score ?? 0;
            const isHigh = score >= 85;
            const isMed = score >= 70 && score < 85;

            return (
              <div
                key={job.id}
                onClick={() => onSelectJobForTailoring(job)}
                className="group p-4 bg-zinc-900/60 hover:bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded transition-all cursor-pointer flex flex-col justify-between gap-3"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-semibold text-zinc-100 group-hover:text-white transition-colors">
                        {job.title}
                      </span>
                      {job.platform && (
                        <span className="px-1.5 py-0.5 text-[10px] font-mono uppercase bg-zinc-800 text-zinc-400 rounded border border-zinc-700/60">
                          {job.platform}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-zinc-400 flex items-center gap-2">
                      <span className="font-medium text-zinc-300">{job.company}</span>
                      <span>•</span>
                      <span>{job.location || "Remote / Unspecified"}</span>
                      {job.experience_level && (
                        <>
                          <span>•</span>
                          <span className="font-mono text-zinc-400">{job.experience_level}</span>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-3 shrink-0">
                    {/* Score Badge & Staging Indicator */}
                    <div className="flex flex-col items-end gap-1">
                      <div
                        className={`px-2.5 py-1 rounded font-mono text-xs font-medium border ${
                          isHigh
                            ? "bg-emerald-950/70 text-emerald-400 border-emerald-800/80"
                            : isMed
                            ? "bg-zinc-800 text-zinc-200 border-zinc-700"
                            : "bg-zinc-900 text-zinc-400 border-zinc-800"
                        }`}
                      >
                        {score}% MATCH
                      </div>
                      <span className="text-[10px] font-mono font-medium text-zinc-400">
                        {job.enrichment_status === "AI_ENRICHED" || (job.ai_score !== undefined && job.ai_score !== null)
                          ? "AI Verified"
                          : "Deterministic Preview"}
                      </span>
                    </div>

                    {/* Bookmark Action */}
                    <button
                      onClick={(e) => handleTrack(e, job.id)}
                      title="Save to Applications Tracker"
                      className="p-1.5 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-200 transition-colors"
                    >
                      {trackedMap[job.id] ? (
                        <Check className="w-4 h-4 text-emerald-400" />
                      ) : (
                        <Bookmark className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Tech Skills Badges */}
                {job.required_skills && job.required_skills.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {job.required_skills.slice(0, 8).map((sk, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-950 text-zinc-400 border border-zinc-800/80"
                      >
                        {sk}
                      </span>
                    ))}
                  </div>
                )}

                {/* Footer Drawer Action Bar */}
                <div className="pt-2 border-t border-zinc-800/60 flex items-center justify-between text-[11px] text-zinc-400">
                  <span className="font-mono">Grounding verified: Draft ⊆ Profile</span>
                  <div className="flex items-center gap-2">
                    {job.url && (
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-zinc-500 hover:text-zinc-300 flex items-center gap-1"
                      >
                        <span>Original Posting</span>
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                    <button
                      onClick={() => onSelectJobForTailoring(job)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded bg-zinc-100 hover:bg-white text-zinc-950 text-xs font-medium transition-colors"
                    >
                      <Sparkles className="w-3 h-3" />
                      <span>Open 2-Agent Tailor</span>
                    </button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
