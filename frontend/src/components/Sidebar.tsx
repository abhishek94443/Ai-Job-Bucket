"use client";

import React from "react";
import { Search, UserCheck, Cpu, BookmarkCheck, Terminal, ShieldCheck, Zap } from "lucide-react";

export type NavTab = "discovery" | "tracker" | "profile" | "automation" | "models";
export type SystemStatus = "online" | "connecting" | "offline";

interface SidebarProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  jobCount?: number;
  systemStatus?: SystemStatus;
}

export function Sidebar({ activeTab, onTabChange, jobCount = 0, systemStatus = "connecting" }: SidebarProps) {
  const navItems = [
    { id: "discovery" as NavTab, label: "Discover Jobs", icon: Search, badge: jobCount > 0 ? `${jobCount}` : undefined },
    { id: "tracker" as NavTab, label: "Applications", icon: BookmarkCheck },
    { id: "profile" as NavTab, label: "My Profile", icon: UserCheck },
    { id: "automation" as NavTab, label: "Automation", icon: Zap },
    { id: "models" as NavTab, label: "AI & Models", icon: Cpu },
  ];

  return (
    <aside className="hidden md:flex w-64 border-r border-zinc-800 bg-zinc-950 flex-col justify-between shrink-0 h-screen select-none">
      <div>
        {/* Workspace Brand Header */}
        <div className="h-14 px-4 border-b border-zinc-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-emerald-400" />
            <span className="font-mono text-xs tracking-wider uppercase text-zinc-100 font-semibold">
              JOB BUCKET
            </span>
          </div>
          {systemStatus === "online" ? (
            <span
              title="Backend online and reachable"
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800/60"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              ONLINE
            </span>
          ) : systemStatus === "connecting" ? (
            <span
              title="Render container is waking up (cold boot in progress)"
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono bg-amber-950/80 text-amber-300 border border-amber-800/60"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping" />
              WAKING UP
            </span>
          ) : (
            <span
              title="System currently unreachable"
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono bg-rose-950/80 text-rose-300 border border-rose-800/60"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-rose-400" />
              OFFLINE
            </span>
          )}
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
          <span>Verification</span>
          <span
            className={
              systemStatus === "online"
                ? "text-emerald-400 flex items-center gap-1"
                : systemStatus === "connecting"
                ? "text-amber-400 flex items-center gap-1"
                : "text-rose-400 flex items-center gap-1"
            }
          >
            <ShieldCheck className="w-3 h-3" />
            {systemStatus === "online"
              ? "Active"
              : systemStatus === "connecting"
              ? "Connecting"
              : "Unreachable"}
          </span>
        </div>
        <div className="pt-1 border-t border-zinc-900 flex items-center justify-between text-[10px] text-zinc-400">
          <span>Profile: Abhishek Dwivedi</span>
          <span className="text-zinc-400">Java / K8s</span>
        </div>
      </div>
    </aside>
  );
}
