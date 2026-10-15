"use client";

import React from "react";
import { Search, FileCode2, UserCheck, Cpu, BookmarkCheck, Terminal, ShieldCheck } from "lucide-react";

export type NavTab = "discovery" | "tracker" | "workbench" | "profile" | "models";

interface SidebarProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  jobCount?: number;
}

export function Sidebar({ activeTab, onTabChange, jobCount = 0 }: SidebarProps) {
  const navItems = [
    { id: "discovery" as NavTab, label: "Job Discovery", icon: Search, badge: jobCount > 0 ? `${jobCount}` : undefined },
    { id: "tracker" as NavTab, label: "Application Tracker", icon: BookmarkCheck },
    { id: "workbench" as NavTab, label: "LaTeX Workbench", icon: FileCode2, badge: "v2.0" },
    { id: "profile" as NavTab, label: "Candidate Profile", icon: UserCheck },
    { id: "models" as NavTab, label: "AI Models & Quotas", icon: Cpu },
  ];

  return (
    <aside className="hidden md:flex w-64 border-r border-zinc-800 bg-zinc-950 flex-col justify-between shrink-0 h-screen select-none">
      <div>
        {/* Workspace Brand Header */}
        <div className="h-14 px-4 border-b border-zinc-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-zinc-400" />
            <span className="font-mono text-xs tracking-wider uppercase text-zinc-200 font-semibold">
              JOB_BUCKET // L7
            </span>
          </div>
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800/60">
            ONLINE
          </span>
        </div>

        {/* Navigation Section */}
        <div className="p-3 space-y-1">
          <div className="px-2 py-1 text-[11px] font-mono uppercase tracking-wider text-zinc-400 font-medium">
            Navigation
          </div>
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onTabChange(item.id)}
                className={`w-full flex items-center justify-between px-2.5 py-2 rounded text-xs transition-colors ${
                  isActive
                    ? "bg-zinc-800/80 text-zinc-100 font-medium border border-zinc-700/60"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900 border border-transparent"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Icon className={`w-3.5 h-3.5 ${isActive ? "text-zinc-100" : "text-zinc-400"}`} />
                  <span>{item.label}</span>
                </div>
                {item.badge && (
                  <span
                    className={`font-mono text-[10px] px-1.5 py-0.5 rounded ${
                      isActive ? "bg-zinc-700 text-zinc-200" : "bg-zinc-900 text-zinc-400 border border-zinc-800"
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Footer System Diagnostics */}
      <div className="p-3 border-t border-zinc-800 bg-zinc-950/80 text-[11px] font-mono text-zinc-400 space-y-2">
        <div className="flex items-center justify-between text-zinc-400">
          <span>Engine</span>
          <span className="text-zinc-300">FastAPI 2.0</span>
        </div>
        <div className="flex items-center justify-between text-zinc-400">
          <span>Grounding</span>
          <span className="text-emerald-400 flex items-center gap-1">
            <ShieldCheck className="w-3 h-3" /> Active
          </span>
        </div>
        <div className="pt-1 border-t border-zinc-900 flex items-center justify-between text-[10px] text-zinc-400">
          <span>Target: Abhishek Dwivedi</span>
          <span className="text-zinc-400">Java / K8s</span>
        </div>
      </div>
    </aside>
  );
}
