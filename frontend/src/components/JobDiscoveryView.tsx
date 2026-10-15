"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Search,
  Sparkles,
  ExternalLink,
  Bookmark,
  Check,
  SlidersHorizontal,
  X,
  ChevronRight,
  RefreshCw,
  Loader2,
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
  const [isLoadingMore, setIsLoadingMore] = useState<boolean>(false);
  const sentinelRef = useRef<HTMLDivElement>(null);

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
    async (targetPage = 1, append = false) => {
      if (append) {
        setIsLoadingMore(true);
      } else {
        setIsLoadingJobs(true);
      }
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
          const incoming: JobItem[] = data.jobs || [];
          if (append) {
            setPagedJobs((prev) => {
              const existingIds = new Set(prev.map((j) => j.id));
              const fresh = incoming.filter((j) => !existingIds.has(j.id));
              return [...prev, ...fresh];
            });
          } else {
            setPagedJobs(incoming);
          }
          setTotalJobs(data.total || 0);
          setTotalPages(data.total_pages || 1);
          setCurrentPage(data.page || targetPage);
        }
      } catch (err) {
        console.error("Failed to fetch paged jobs:", err);
      } finally {
        setIsLoadingJobs(false);
        setIsLoadingMore(false);
      }
    },
    [debouncedSearch, filters, pageSize]
  );

  // Initial fetch and on filter/search change
  useEffect(() => {
    fetchJobs(1, false);
  }, [fetchJobs]);

  const handlePageChange = (newPage: number) => {
    fetchJobs(newPage, false);
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  const handleLoadMore = useCallback(() => {
    if (isLoadingMore || isLoadingJobs) return;
    if (currentPage >= totalPages) return;
    fetchJobs(currentPage + 1, true);
  }, [fetchJobs, currentPage, totalPages, isLoadingMore, isLoadingJobs]);

  // Mobile / PWA IntersectionObserver for automatic infinite scroll
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !isLoadingMore && !isLoadingJobs) {
          handleLoadMore();
        }
      },
      {
        root: scrollContainerRef.current,
        rootMargin: "250px",
        threshold: 0.1,
      }
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [handleLoadMore, isLoadingMore, isLoadingJobs]);

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

          {/* Integrated Filter Button with Active Badge and Reset Cross */}
          <div className="flex items-center shrink-0">
            <button
              onClick={() => setIsFilterModalOpen(true)}
              aria-label="Open Filter Drawer"
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium border transition-colors ${
                activeFilterCount > 0
                  ? "bg-emerald-950 text-emerald-300 border-emerald-700 font-semibold rounded-r-none border-r-0"
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
            {activeFilterCount > 0 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleResetFilters();
                }}
                title="Clear all filters and restore defaults"
                aria-label="Clear all filters and restore defaults"
                className="flex items-center justify-center px-2 py-1.5 bg-emerald-950 hover:bg-emerald-900 text-emerald-300 hover:text-white border border-emerald-700 rounded-r border-l border-l-emerald-800/60 text-xs transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
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
            const isAiEnriched =
              job.enrichment_status === "AI_ENRICHED" ||
              (job.ai_score !== undefined && job.ai_score !== null);

            return (
              <div
                key={job.id}
                onClick={() => onSelectJobForTailoring(job)}
                className="group p-3.5 md:p-4 rounded-lg bg-zinc-900/50 hover:bg-zinc-900 border border-zinc-800/80 hover:border-zinc-700 transition-all cursor-pointer space-y-2.5"
              >
                {/* Job Card Header */}
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1 min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-sm text-zinc-100 group-hover:text-emerald-400 transition-colors truncate">
                        {job.title}
                      </h3>
                      {job.platform && (
                        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-mono uppercase bg-zinc-800 text-zinc-400 border border-zinc-700/60 shrink-0">
                          {job.platform}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-zinc-400 font-mono flex items-center gap-2 flex-wrap">
                      <span className="text-zinc-300 font-medium">{job.company}</span>
                      <span>•</span>
                      <span>{job.location || "Remote / Location Unspecified"}</span>
                      {job.posted_date && (
                        <>
                          <span>•</span>
                          <span className="text-zinc-500">Posted: {job.posted_date.slice(0, 10)}</span>
                        </>
                      )}
                      {/* Inline verification status for all screens */}
                      <span className="text-zinc-600">•</span>
                      <span
                        className={`font-medium ${
                          isAiEnriched ? "text-emerald-400" : "text-zinc-500"
                        }`}
                      >
                        {isAiEnriched ? "AI Verified" : "Keyword Match"}
                      </span>
                    </div>
                  </div>

                  {/* Desktop Right Side Actions (Same Height h-8, perfectly aligned) */}
                  <div className="hidden md:flex items-center gap-2 shrink-0">
                    <div
                      className={`h-8 flex items-center px-2.5 rounded-md border font-mono text-xs font-semibold mr-1.5 ${
                        score >= 80
                          ? "bg-emerald-950/80 text-emerald-400 border-emerald-800"
                          : score >= 60
                          ? "bg-amber-950/80 text-amber-400 border-amber-800"
                          : "bg-zinc-900 text-zinc-400 border-zinc-800"
                      }`}
                    >
                      {score}% MATCH
                    </div>

                    {job.url && (
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="h-8 flex items-center gap-1.5 px-3 rounded-md bg-zinc-800 hover:bg-zinc-700 text-zinc-200 hover:text-white text-xs font-medium border border-zinc-700/80 transition-colors"
                        title="Apply directly on company portal"
                      >
                        <span>Apply</span>
                        <ExternalLink className="w-3.5 h-3.5 text-zinc-400" />
                      </a>
                    )}

                    <button
                      onClick={(e) => handleTrack(e, job.id)}
                      title="Save to Applications Tracker"
                      className="h-8 w-8 flex items-center justify-center rounded-md bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-100 border border-zinc-800 transition-colors"
                    >
                      {trackedMap[job.id] ? (
                        <Check className="w-4 h-4 text-emerald-400" />
                      ) : (
                        <Bookmark className="w-4 h-4" />
                      )}
                    </button>
                  </div>

                  {/* Mobile / PWA Right Side (Clean Vertical Column) */}
                  <div className="flex md:hidden flex-col items-end gap-2 shrink-0">
                    <div className="flex items-center gap-1.5">
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
                      <button
                        onClick={(e) => handleTrack(e, job.id)}
                        title="Save to Applications Tracker"
                        className="p-1 rounded bg-zinc-900 border border-zinc-800 text-zinc-400 hover:text-zinc-100"
                      >
                        {trackedMap[job.id] ? (
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                        ) : (
                          <Bookmark className="w-3.5 h-3.5" />
                        )}
                      </button>
                    </div>

                    {job.url && (
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="flex items-center gap-1 px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium border border-zinc-700/80 transition-colors"
                      >
                        <span>Apply</span>
                        <ExternalLink className="w-3 h-3 text-zinc-400" />
                      </a>
                    )}
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
              </div>
            );
          })
        )}

        {/* Mobile / PWA Infinite Scroll & Append Feed Sentinel */}
        <div className="md:hidden pt-3 pb-8">
          {currentPage < totalPages ? (
            <div ref={sentinelRef} className="flex flex-col items-center justify-center gap-2 py-4">
              {isLoadingMore ? (
                <div className="flex items-center gap-2 text-xs font-mono text-emerald-400 py-2">
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>Loading more jobs...</span>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={handleLoadMore}
                  className="w-full py-2.5 px-4 rounded-lg bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-800 hover:border-zinc-700 font-mono text-xs font-medium flex items-center justify-center gap-2 transition-colors active:scale-[0.99]"
                >
                  <span>Load More Jobs</span>
                  <span className="text-zinc-500 text-[11px]">
                    ({pagedJobs.length} of {totalJobs})
                  </span>
                </button>
              )}
            </div>
          ) : (
            pagedJobs.length > 0 && (
              <div className="text-center py-4 text-xs font-mono text-zinc-500 border-t border-zinc-900 mt-2">
                All {totalJobs} jobs loaded
              </div>
            )
          )}
        </div>
      </div>

      {/* Pagination Controls Bar (Desktop only, strictly 20 results per page) */}
      <div className="hidden md:block">
        <PaginationBar
          page={currentPage}
          pageSize={pageSize}
          total={totalJobs}
          totalPages={totalPages}
          onPageChange={handlePageChange}
        />
      </div>

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
