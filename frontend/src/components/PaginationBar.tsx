"use client";

import React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationBarProps {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  onPageChange: (newPage: number) => void;
}

export function PaginationBar({
  page,
  pageSize,
  total,
  totalPages,
  onPageChange,
}: PaginationBarProps) {
  if (total <= 0) return null;

  const startIdx = Math.min((page - 1) * pageSize + 1, total);
  const endIdx = Math.min(page * pageSize, total);

  // Generate page numbers with window
  const getPageNumbers = () => {
    const pages: (number | string)[] = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      if (page > 3) pages.push("...");

      const start = Math.max(2, page - 1);
      const end = Math.min(totalPages - 1, page + 1);
      for (let i = start; i <= end; i++) {
        pages.push(i);
      }

      if (page < totalPages - 2) pages.push("...");
      pages.push(totalPages);
    }
    return pages;
  };

  const pages = getPageNumbers();

  return (
    <div className="px-4 md:px-6 py-3 border-t border-zinc-800 bg-zinc-950/90 backdrop-blur flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-zinc-400 font-mono shrink-0 select-none">
      {/* Range summary */}
      <div className="text-[11px] text-zinc-400">
        Showing <span className="text-zinc-200 font-semibold">{startIdx}</span> -{" "}
        <span className="text-zinc-200 font-semibold">{endIdx}</span> of{" "}
        <span className="text-emerald-400 font-semibold">{total}</span> opportunities
      </div>

      {/* Navigation Buttons */}
      <div className="flex items-center gap-1.5">
        {/* Previous */}
        <button
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          aria-label="Previous Page"
          className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs bg-zinc-900 border border-zinc-800 text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Prev</span>
        </button>

        {/* Numbered Buttons for Desktop */}
        <div className="hidden md:flex items-center gap-1">
          {pages.map((p, idx) => {
            if (p === "...") {
              return (
                <span key={`dots-${idx}`} className="px-2 py-1 text-zinc-600">
                  ...
                </span>
              );
            }
            const isCurrent = p === page;
            return (
              <button
                key={`page-${p}`}
                onClick={() => onPageChange(p as number)}
                className={`w-8 h-8 rounded text-xs transition-colors flex items-center justify-center font-mono ${
                  isCurrent
                    ? "bg-emerald-950 text-emerald-300 border border-emerald-700 font-bold"
                    : "bg-zinc-900 text-zinc-400 border border-zinc-800 hover:text-zinc-200 hover:bg-zinc-800"
                }`}
              >
                {p}
              </button>
            );
          })}
        </div>

        {/* Mobile Page Indicator */}
        <span className="md:hidden px-2 text-xs text-zinc-400 font-mono">
          Page {page} of {totalPages}
        </span>

        {/* Next */}
        <button
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          aria-label="Next Page"
          className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs bg-zinc-900 border border-zinc-800 text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <span className="hidden sm:inline">Next</span>
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
