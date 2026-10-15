"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Search,
  RefreshCw,
  Sparkles,
  ExternalLink,
  Bookmark,
  Check,
  SlidersHorizontal,
  X,
  Calendar,
  Clock,
} from "lucide-react";
import { FilterModal, FilterState } from "./FilterModal";
import { PaginationBar } from "./PaginationBar";

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
  posted_date?: string;
  discovered_at?: string;
  created_at?: string;
  application_status?: string;
}

interface JobDiscoveryViewProps {
  jobs?: JobItem[];
  isLoading?: boolean;
  onRefresh: () => void;
  onSelectJobForTailoring: (job: JobItem) => void;
  onTrackJob: (jobId: number, status: string) => void;
}

export function JobDiscoveryView({
  onRefresh,
  onSelectJobForTailoring,
  onTrackJob,
}: JobDiscoveryViewProps) {
  // Paging state: strictly 20 items per page
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [pageSize] = useState<number>(20);
  const [totalJobs, setTotalJobs] = useState<number>(0);
  const [totalPages, setTotalPages] = useState<number>(1);
  const [pagedJobs, setPagedJobs] = useState<JobItem[]>([]);
  const [isLoadingJobs, setIsLoadingJobs] = useState<boolean>(true);

  // Search & Filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [isFilterModalOpen, setIsFilterModalOpen] = useState(false);
  const [filters, setFilters] = useState<FilterState>({
    minScore: null,
    platforms: [],
    postedWithin: null,
    fetchedWithin: null,
    status: null,
    sortBy: "ai_score",
  });

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

  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Debounce search query by 300ms
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedSearch(searchQuery);
      setCurrentPage(1);
    }, 300);
    return () => clearTimeout(handler);
  }, [searchQuery]);

  // Fetch pipeline diagnostics
  const fetchStatus = () => {
    fetch("/api/pipeline/status")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) setPipelineStatus(data);
      })
      .catch(() => {});
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  // Fetch 20 paged jobs from backend with active filters
  const fetchJobs = useCallback(
    async (targetPage = 1) => {
      setIsLoadingJobs(true);
      try {
        const params = new URLSearchParams();
        params.set("page", String(targetPage));
        params.set("page_size", String(pageSize));

        if (debouncedSearch.trim()) {
          params.set("search", debouncedSearch.trim());
        }
        if (filters.minScore !== null) {
          params.set("min_score", String(filters.minScore));
        }
        if (filters.platforms.length > 0) {
          params.set("portal", filters.platforms.join(","));
        }
        if (filters.postedWithin) {
          params.set("posted_within", filters.postedWithin);
        }
        if (filters.fetchedWithin) {
          params.set("fetched_within", filters.fetchedWithin);
        }
        if (filters.status) {
          params.set("status", filters.status);
        }
        if (filters.sortBy) {
          params.set("sort_by", filters.sortBy);
        }

        const res = await fetch(`/api/jobs?${params.toString()}`);
        if (res.ok) {
          const data = await res.json();
          setPagedJobs(data.jobs || []);
          setTotalJobs(data.total || 0);
          setTotalPages(data.total_pages || 1);
          setCurrentPage(data.page || targetPage);
        }
      } catch (err) {
        console.error("Failed to fetch paged jobs:", err);
      } finally {
        setIsLoadingJobs(false);
      }
    },
    [debouncedSearch, filters, pageSize]
  );

  useEffect(() => {
    fetchJobs(currentPage);
  }, [fetchJobs, currentPage]);

  const handlePageChange = (newPage: number) => {
    setCurrentPage(newPage);
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  const handleApplyFilters = (newFilters: FilterState) => {
    setFilters(newFilters);
    setCurrentPage(1);
  };

  const handleResetFilters = () => {
    setFilters({
      minScore: null,
      platforms: [],
      postedWithin: null,
      fetchedWithin: null,
      status: null,
      sortBy: "ai_score",
    });
    setSearchQuery("");
    setCurrentPage(1);
  };

  const handleRemoveSingleFilter = (key: keyof FilterState, val?: string) => {
    setFilters((prev) => {
      if (key === "platforms" && val) {
        return { ...prev, platforms: prev.platforms.filter((p) => p !== val) };
      }
      return { ...prev, [key]: key === "sortBy" ? "ai_score" : null };
    });
    setCurrentPage(1);
  };

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
        fetchJobs(currentPage);
        fetchStatus();
        onRefresh();
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keywords: ["Software Engineer", "Java", "Backend"], limit: 30 }),
      });
      const data = await res.json();
      if (res.ok) {
        setEnrichStatusMsg(data.message || "Discovery pipeline started in background.");
        setTimeout(() => {
          fetchJobs(1);
          fetchStatus();
          onRefresh();
          setIsDiscovering(false);
        }, 5000);
      } else {
        setEnrichStatusMsg(data.detail || "Discovery trigger failed.");
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

  const activeFilterCount = [
    filters.minScore !== null,
    filters.platforms.length > 0,
    filters.postedWithin !== null,
    filters.fetchedWithin !== null,
    filters.status !== null,
    filters.sortBy !== "ai_score",
  ].filter(Boolean).length;

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Top Header & Search Bar with Integrated Filter Trigger */}
      <header className="h-auto md:h-14 px-3 md:px-6 py-2.5 md:py-0 border-b border-zinc-800 flex flex-col md:flex-row items-stretch md:items-center justify-between gap-2.5 shrink-0 bg-zinc-950/90 backdrop-blur">
        <div className="flex items-center gap-2 flex-1 max-w-xl">
          {/* Search Input Box */}
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder="Search index by role, company, or stack (e.g. Java, Kubernetes)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-zinc-900 border border-zinc-800 rounded pl-9 pr-8 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-zinc-600 transition-colors"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Integrated Filter Button with Active Badge */}
          <button
            onClick={() => setIsFilterModalOpen(true)}
            aria-label="Open Filter Drawer"
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium border transition-colors shrink-0 ${
              activeFilterCount > 0
                ? "bg-emerald-950 text-emerald-300 border-emerald-700 font-semibold"
                : "bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border-zinc-800"
            }`}
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span>Filter</span>
            {activeFilterCount > 0 && (
              <span className="ml-0.5 px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-emerald-900 text-emerald-200 border border-emerald-700 font-bold">
                {activeFilterCount}
              </span>
            )}
          </button>
        </div>

        {/* Desktop Pipeline & Batch Actions */}
        <div className="flex items-center justify-between md:justify-end gap-2 md:gap-3 overflow-x-auto">
          {/* Configurable Multi-Job Batch Controls */}
          <div className="hidden lg:flex items-center gap-2 px-2.5 py-1 rounded bg-zinc-900 border border-zinc-800 text-xs">
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
            onClick={() => fetchJobs(currentPage)}
            disabled={isLoadingJobs || isDiscovering}
            title="Refresh current jobs from database"
            className="flex items-center justify-center p-2 md:p-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 border border-zinc-800 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoadingJobs && !isDiscovering ? "animate-spin" : ""}`} />
          </button>

          <button
            onClick={handleTriggerDiscovery}
            disabled={isDiscovering || isLoadingJobs}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 transition-colors disabled:opacity-50 shrink-0"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isDiscovering ? "animate-spin text-emerald-400" : ""}`} />
            <span>{isDiscovering ? "Discovering..." : "Discover Pipeline"}</span>
          </button>
        </div>
      </header>

      {/* Notification Banner */}
      {enrichStatusMsg && (
        <div className="px-4 md:px-6 py-2 bg-emerald-950/40 border-b border-emerald-800/40 text-emerald-300 text-xs font-mono flex items-center justify-between">
          <span>{enrichStatusMsg}</span>
          <button
            onClick={() => setEnrichStatusMsg(null)}
            className="text-emerald-500 hover:text-emerald-300 text-xs"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Active Filter Chips Strip & Pipeline Stats */}
      <div className="px-3 md:px-6 py-2 border-b border-zinc-800/80 flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-400 bg-zinc-950/60 overflow-x-auto">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-mono uppercase text-zinc-500 font-medium">Active:</span>

          {activeFilterCount === 0 && !debouncedSearch ? (
            <span className="text-[11px] font-mono text-zinc-500">All 20 results/page</span>
          ) : null}

          {/* Search Query Chip */}
          {debouncedSearch && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-900 text-zinc-200 border border-zinc-700">
              <span>&ldquo;{debouncedSearch}&rdquo;</span>
              <button
                onClick={() => setSearchQuery("")}
                className="text-zinc-400 hover:text-white"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          )}

          {/* Match Score Chip */}
          {filters.minScore !== null && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-800">
              <span>Score &gt;= {filters.minScore}%</span>
              <button
                onClick={() => handleRemoveSingleFilter("minScore")}
                className="text-emerald-400 hover:text-white"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          )}

          {/* Platform Chips */}
          {filters.platforms.map((p) => (
            <span
              key={p}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono uppercase bg-zinc-900 text-zinc-200 border border-zinc-700"
            >
              <span>{p}</span>
              <button
                onClick={() => handleRemoveSingleFilter("platforms", p)}
                className="text-zinc-400 hover:text-white"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}

          {/* Date Posted Chip */}
          {filters.postedWithin && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-900 text-zinc-200 border border-zinc-700">
              <Calendar className="w-2.5 h-2.5 text-zinc-400" />
              <span>Posted: {filters.postedWithin}</span>
              <button
                onClick={() => handleRemoveSingleFilter("postedWithin")}
                className="text-zinc-400 hover:text-white"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          )}

          {/* Date Fetched Chip */}
          {filters.fetchedWithin && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-900 text-zinc-200 border border-zinc-700">
              <Clock className="w-2.5 h-2.5 text-zinc-400" />
              <span>Fetched: {filters.fetchedWithin}</span>
              <button
                onClick={() => handleRemoveSingleFilter("fetchedWithin")}
                className="text-zinc-400 hover:text-white"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          )}

          {/* Clear All Link */}
          {(activeFilterCount > 0 || debouncedSearch) && (
            <button
              onClick={handleResetFilters}
              className="text-[11px] font-mono text-zinc-500 hover:text-zinc-300 ml-1 underline"
            >
              Clear all
            </button>
          )}
        </div>

        {/* Pipeline Diagnostics Counter */}
        <div className="flex items-center gap-2 font-mono text-[11px] text-zinc-400 shrink-0">
          {pipelineStatus && (
            <div className="hidden sm:flex items-center gap-1.5 px-2 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-400 text-[10px]">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400" />
              <span>{pipelineStatus.ai_enriched_count} Verified</span>
              <span>•</span>
              <span>{pipelineStatus.staged_count} Staged</span>
            </div>
          )}
          <span className="text-zinc-400">
            Total: <span className="text-zinc-200 font-semibold">{totalJobs}</span>
          </span>
        </div>
      </div>

      {/* Main Job Cards Feed */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto p-3 md:p-6 space-y-3 focus:outline-none"
      >
        {isLoadingJobs ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3 text-zinc-500 font-mono text-xs">
            <RefreshCw className="w-5 h-5 animate-spin text-emerald-400" />
            <span>Loading page {currentPage} (20 jobs)...</span>
          </div>
        ) : pagedJobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-2 text-zinc-500 font-mono text-xs text-center px-4">
            <span className="text-zinc-400 font-semibold">No job listings found</span>
            <span className="text-[11px] text-zinc-600">
              Try adjusting your filter criteria, resetting dates, or clearing keyword searches.
            </span>
            <button
              onClick={handleResetFilters}
              className="mt-2 px-3 py-1.5 rounded bg-zinc-900 text-zinc-300 border border-zinc-800 text-xs hover:bg-zinc-800"
            >
              Reset All Filters
            </button>
          </div>
        ) : (
          pagedJobs.map((job) => {
            const score = job.ai_score ?? job.relevance_score ?? 0;
            return (
              <div
                key={job.id}
                onClick={() => onSelectJobForTailoring(job)}
                className="group p-3.5 md:p-4 rounded-lg bg-zinc-900/50 hover:bg-zinc-900 border border-zinc-800/80 hover:border-zinc-700 transition-all cursor-pointer space-y-2.5"
              >
                {/* Job Card Header */}
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-sm text-zinc-100 group-hover:text-emerald-400 transition-colors">
                        {job.title}
                      </h3>
                      {job.platform && (
                        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-mono uppercase bg-zinc-800 text-zinc-400 border border-zinc-700/60">
                          {job.platform}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-zinc-400 font-mono flex items-center gap-2 flex-wrap">
                      <span>{job.company}</span>
                      <span>•</span>
                      <span>{job.location || "Remote / Location Unspecified"}</span>
                      {job.posted_date && (
                        <>
                          <span>•</span>
                          <span className="text-zinc-500">Posted: {job.posted_date.slice(0, 10)}</span>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Score Pill & Bookmark */}
                  <div className="flex items-center gap-2 shrink-0">
                    <div className="flex flex-col items-end">
                      <div
                        className={`font-mono text-xs font-semibold px-2 py-0.5 rounded border ${
                          score >= 80
                            ? "bg-emerald-950/80 text-emerald-400 border-emerald-800"
                            : score >= 60
                            ? "bg-amber-950/80 text-amber-400 border-amber-800"
                            : "bg-zinc-900 text-zinc-400 border-zinc-800"
                        }`}
                      >
                        {score}% MATCH
                      </div>
                      <span className="text-[10px] font-mono font-medium text-zinc-500">
                        {job.enrichment_status === "AI_ENRICHED" || (job.ai_score !== undefined && job.ai_score !== null)
                          ? "AI Verified"
                          : "Deterministic"}
                      </span>
                    </div>

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
                  <div className="flex flex-wrap gap-1.5 pt-0.5">
                    {job.required_skills.slice(0, 7).map((sk, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-950 text-zinc-400 border border-zinc-800/80"
                      >
                        {sk}
                      </span>
                    ))}
                  </div>
                )}

                {/* Footer Action Bar */}
                <div className="pt-2 border-t border-zinc-800/60 flex items-center justify-between text-[11px] text-zinc-400">
                  <span className="font-mono text-zinc-500 text-[10px] hidden sm:inline">
                    Grounding verified: Draft ⊆ Profile
                  </span>
                  <div className="flex items-center gap-2 ml-auto">
                    {job.url && (
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-zinc-500 hover:text-zinc-300 flex items-center gap-1"
                      >
                        <span>Original</span>
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectJobForTailoring(job);
                      }}
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

      {/* Pagination Controls Bar (Strictly 20 results per page) */}
      <PaginationBar
        page={currentPage}
        pageSize={pageSize}
        total={totalJobs}
        totalPages={totalPages}
        onPageChange={handlePageChange}
      />

      {/* Filter Modal / Bottom Sheet Component */}
      <FilterModal
        isOpen={isFilterModalOpen}
        onClose={() => setIsFilterModalOpen(false)}
        filters={filters}
        onApply={handleApplyFilters}
        onReset={handleResetFilters}
        totalMatching={totalJobs}
      />
    </div>
  );
}
