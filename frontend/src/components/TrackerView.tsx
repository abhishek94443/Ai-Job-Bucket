"use client";

import React, { useState } from "react";
import { BookmarkCheck, ArrowRight, ExternalLink } from "lucide-react";
import { JobItem } from "./JobDiscoveryView";

interface TrackerViewProps {
  jobs: JobItem[];
  onSelectJobForTailoring: (job: JobItem) => void;
  onUpdateStatus: (jobId: number, status: string) => void;
}

const STAGES = ["Saved", "Applied", "Interviewing", "Offer", "Rejected"];

export function TrackerView({ jobs, onSelectJobForTailoring, onUpdateStatus }: TrackerViewProps) {
  // Group jobs by stage or display tracked jobs
  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Header */}
      <header className="h-14 px-6 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90">
        <div className="flex items-center gap-3">
          <BookmarkCheck className="w-4 h-4 text-zinc-400" />
          <span className="font-semibold text-sm text-zinc-100">Application Lifecycle CRM Tracker</span>
          <span className="text-[11px] font-mono text-zinc-500">Pipeline Velocity &amp; Stages</span>
        </div>
        <div className="font-mono text-xs text-zinc-400">
          Total Indexed: {jobs.length}
        </div>
      </header>

      {/* Kanban / Stage Columns */}
      <div className="flex-1 overflow-x-auto p-6 flex gap-4">
        {STAGES.map((stage) => {
          // Filter jobs for this stage (or placeholder demo if none tracked yet)
          const stageJobs = jobs.filter((j: any) => (j.application_status || "Saved") === stage);

          return (
            <div key={stage} className="w-80 flex flex-col shrink-0 bg-zinc-900/40 border border-zinc-800 rounded">
              <div className="p-3 border-b border-zinc-800 flex items-center justify-between text-xs font-mono font-medium">
                <span className="text-zinc-200">{stage}</span>
                <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">
                  {stageJobs.length}
                </span>
              </div>

              <div className="flex-1 overflow-y-auto p-2.5 space-y-2.5">
                {stageJobs.length === 0 ? (
                  <div className="h-24 flex items-center justify-center text-zinc-600 font-mono text-[11px]">
                    No applications
                  </div>
                ) : (
                  stageJobs.map((job) => (
                    <div
                      key={job.id}
                      onClick={() => onSelectJobForTailoring(job)}
                      className="p-3 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded transition-all cursor-pointer space-y-2 group"
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="text-xs font-semibold text-zinc-100 group-hover:text-white">
                            {job.title}
                          </div>
                          <div className="text-[11px] text-zinc-400">{job.company}</div>
                        </div>
                        <span className="font-mono text-[10px] text-emerald-400 font-medium">
                          {job.relevance_score || 85}%
                        </span>
                      </div>

                      <div className="pt-2 border-t border-zinc-800/60 flex items-center justify-between text-[10px] font-mono text-zinc-500">
                        <span>Move:</span>
                        <div className="flex items-center gap-1">
                          {STAGES.filter((s) => s !== stage).slice(0, 2).map((nextStage) => (
                            <button
                              key={nextStage}
                              onClick={(e) => {
                                e.stopPropagation();
                                onUpdateStatus(job.id, nextStage);
                              }}
                              className="px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
                            >
                              {nextStage}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
