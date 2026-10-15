"use client";

import React from "react";
import { X, SlidersHorizontal, RotateCcw, Calendar, Check, Clock, Layers, Sparkles } from "lucide-react";

export interface FilterState {
  minScore: number | null;
  platforms: string[]; // empty means all
  postedWithin: string | null;
  fetchedWithin: string | null;
  status: string | null;
  sortBy: string;
}

interface FilterModalProps {
  isOpen: boolean;
  onClose: () => void;
  filters: FilterState;
  onApply: (newFilters: FilterState) => void;
  onReset: () => void;
  totalMatching: number;
}

export function FilterModal({
  isOpen,
  onClose,
  filters,
  onApply,
  onReset,
  totalMatching,
}: FilterModalProps) {
  const [draft, setDraft] = React.useState<FilterState>(filters);

  // Sync draft when opened or external filters change
  React.useEffect(() => {
    setDraft(filters);
  }, [filters, isOpen]);

  if (!isOpen) return null;

  const togglePlatform = (p: string) => {
    setDraft((prev) => {
      const exists = prev.platforms.includes(p);
      const updated = exists ? prev.platforms.filter((x) => x !== p) : [...prev.platforms, p];
      return { ...prev, platforms: updated };
    });
  };

  const handleApply = () => {
    onApply(draft);
    onClose();
  };

  const handleResetDraft = () => {
    const empty: FilterState = {
      minScore: null,
      platforms: [],
      postedWithin: null,
      fetchedWithin: null,
      status: null,
      sortBy: "ai_score",
    };
    setDraft(empty);
    onReset();
  };

  const postedOptions = [
    { label: "Anytime", value: null },
    { label: "Past 24 Hours", value: "last 24 hours" },
    { label: "Past 3 Days", value: "last 3 days" },
    { label: "Past 7 Days", value: "last 7 days" },
    { label: "Past 14 Days", value: "last 14 days" },
    { label: "Past 30 Days", value: "last 30 days" },
  ];

  const fetchedOptions = [
    { label: "Anytime", value: null },
    { label: "Today", value: "today" },
    { label: "Past 3 Days", value: "last 3 days" },
    { label: "Past 7 Days", value: "last 7 days" },
    { label: "Past 30 Days", value: "last 30 days" },
  ];

  const sortOptions = [
    { label: "Best Match (AI Score)", value: "ai_score" },
    { label: "Date Posted (Newest)", value: "newest" },
    { label: "Date Fetched (Recent)", value: "fetched" },
    { label: "Company (A-Z)", value: "company" },
  ];

  const activeCount = [
    draft.minScore !== null,
    draft.platforms.length > 0,
    draft.postedWithin !== null,
    draft.fetchedWithin !== null,
    draft.status !== null,
    draft.sortBy !== "ai_score",
  ].filter(Boolean).length;

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end md:justify-center items-center bg-black/70 backdrop-blur-sm animate-in fade-in duration-150 p-0 md:p-4">
      {/* Background overlay click-to-dismiss */}
      <div className="fixed inset-0" onClick={onClose} />

      {/* Modal / Bottom Sheet Box */}
      <div
        role="dialog"
        aria-modal="true"
        className="relative w-full max-w-xl bg-zinc-950 border border-zinc-800 rounded-t-2xl md:rounded-2xl shadow-2xl flex flex-col max-h-[88vh] md:max-h-[82vh] overflow-hidden z-10 animate-in slide-in-from-bottom duration-200"
      >
        {/* Top Drag Handle for Mobile */}
        <div className="md:hidden flex justify-center pt-2.5 pb-1">
          <div className="w-10 h-1 bg-zinc-700 rounded-full" />
        </div>

        {/* Header */}
        <div className="h-14 px-5 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90">
          <div className="flex items-center gap-2.5">
            <SlidersHorizontal className="w-4 h-4 text-emerald-400" />
            <h2 className="text-sm font-semibold text-zinc-100">Filter Opportunities</h2>
            {activeCount > 0 && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-800">
                {activeCount} active
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleResetDraft}
              className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors flex items-center gap-1"
            >
              <RotateCcw className="w-3 h-3" />
              <span>Reset all</span>
            </button>
            <button
              onClick={onClose}
              className="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Scrollable Facet Body */}
        <div className="p-5 overflow-y-auto space-y-6 text-xs text-zinc-300">
          {/* 1. Relevance Match Score */}
          <div>
            <div className="flex items-center gap-2 mb-2.5">
              <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
              <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px] font-mono">
                Match Relevance Score
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {[
                { label: "All Scores", val: null },
                { label: "Match >= 80%", val: 80 },
                { label: "High Affinity >= 90%", val: 90 },
              ].map((opt) => {
                const active = draft.minScore === opt.val;
                return (
                  <button
                    key={opt.label}
                    onClick={() => setDraft((p) => ({ ...p, minScore: opt.val }))}
                    className={`py-2 px-3 rounded text-xs font-mono transition-colors text-center border ${
                      active
                        ? "bg-emerald-950 text-emerald-200 border-emerald-700 font-semibold"
                        : "bg-zinc-900/80 text-zinc-400 border-zinc-800 hover:bg-zinc-800 hover:text-zinc-200"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 2. Platform Sources (Multi-Select) */}
          <div>
            <div className="flex items-center gap-2 mb-2.5">
              <Layers className="w-3.5 h-3.5 text-zinc-400" />
              <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px] font-mono">
                Job Board Sources
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {[
                { id: "linkedin", label: "LinkedIn" },
                { id: "indeed", label: "Indeed" },
                { id: "freehire", label: "Freehire" },
              ].map((p) => {
                const isSelected = draft.platforms.includes(p.id);
                return (
                  <button
                    key={p.id}
                    onClick={() => togglePlatform(p.id)}
                    className={`px-3 py-1.5 rounded-full text-xs font-mono transition-colors flex items-center gap-1.5 border ${
                      isSelected
                        ? "bg-emerald-950 text-emerald-300 border-emerald-700 font-medium"
                        : "bg-zinc-900 text-zinc-400 border-zinc-800 hover:bg-zinc-800 hover:text-zinc-200"
                    }`}
                  >
                    {isSelected && <Check className="w-3 h-3 text-emerald-400" />}
                    <span>{p.label}</span>
                  </button>
                );
              })}
              {draft.platforms.length > 0 && (
                <button
                  onClick={() => setDraft((p) => ({ ...p, platforms: [] }))}
                  className="px-2.5 py-1.5 text-[11px] text-zinc-400 hover:text-zinc-300 underline"
                >
                  Clear platform selection
                </button>
              )}
            </div>
          </div>

          {/* 3. Date Posted (by Employer) */}
          <div>
            <div className="flex items-center gap-2 mb-2.5">
              <Calendar className="w-3.5 h-3.5 text-zinc-400" />
              <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px] font-mono">
                Date Posted (Employer Posting Date)
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {postedOptions.map((opt) => {
                const active = draft.postedWithin === opt.value;
                return (
                  <button
                    key={opt.label}
                    onClick={() => setDraft((p) => ({ ...p, postedWithin: opt.value }))}
                    className={`py-1.5 px-2.5 rounded text-xs transition-colors text-left border ${
                      active
                        ? "bg-emerald-950 text-emerald-200 border-emerald-700 font-semibold"
                        : "bg-zinc-900/80 text-zinc-400 border-zinc-800 hover:bg-zinc-800 hover:text-zinc-200"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 4. Date Fetched / Discovered (by Pipeline) */}
          <div>
            <div className="flex items-center gap-2 mb-2.5">
              <Clock className="w-3.5 h-3.5 text-zinc-400" />
              <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px] font-mono">
                Date Fetched (Ingested by System)
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {fetchedOptions.map((opt) => {
                const active = draft.fetchedWithin === opt.value;
                return (
                  <button
                    key={opt.label}
                    onClick={() => setDraft((p) => ({ ...p, fetchedWithin: opt.value }))}
                    className={`py-1.5 px-2.5 rounded text-xs transition-colors text-left border ${
                      active
                        ? "bg-emerald-950 text-emerald-200 border-emerald-700 font-semibold"
                        : "bg-zinc-900/80 text-zinc-400 border-zinc-800 hover:bg-zinc-800 hover:text-zinc-200"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 5. Sort Order */}
          <div>
            <div className="flex items-center gap-2 mb-2.5">
              <SlidersHorizontal className="w-3.5 h-3.5 text-zinc-400" />
              <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px] font-mono">
                Sort Order
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {sortOptions.map((opt) => {
                const active = draft.sortBy === opt.value;
                return (
                  <button
                    key={opt.label}
                    onClick={() => setDraft((p) => ({ ...p, sortBy: opt.value }))}
                    className={`py-1.5 px-2.5 rounded text-xs transition-colors text-left border ${
                      active
                        ? "bg-emerald-950 text-emerald-200 border-emerald-700 font-semibold"
                        : "bg-zinc-900/80 text-zinc-400 border-zinc-800 hover:bg-zinc-800 hover:text-zinc-200"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Sticky Footer CTA */}
        <div className="p-4 border-t border-zinc-800 bg-zinc-950/95 flex items-center justify-between gap-3 shrink-0">
          <button
            onClick={handleResetDraft}
            className="px-4 py-2 rounded text-xs font-medium text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            Clear All
          </button>
          <button
            onClick={handleApply}
            className="flex-1 max-w-sm py-2.5 px-4 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold text-xs tracking-tight transition-colors shadow-lg shadow-emerald-950/40 flex items-center justify-center gap-1.5"
          >
            <span>Apply Filters</span>
            {activeCount > 0 && (
              <span className="px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-emerald-950 text-emerald-300">
                ({activeCount})
              </span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
