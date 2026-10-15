"use client";

import React from "react";
import { Search, UserCheck, Cpu, BookmarkCheck, Terminal, ShieldCheck, Zap } from "lucide-react";
import { NavTab } from "./Sidebar";

interface MobileNavProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  jobCount?: number;
}

export function MobileNav({ activeTab, onTabChange, jobCount = 0 }: MobileNavProps) {
  const navItems = [
    { id: "discovery" as NavTab, label: "Jobs", icon: Search, badge: jobCount > 0 ? `${jobCount}` : undefined },
    { id: "tracker" as NavTab, label: "Apps", icon: BookmarkCheck },
    { id: "profile" as NavTab, label: "Profile", icon: UserCheck },
    { id: "automation" as NavTab, label: "Automation", icon: Zap },
    { id: "models" as NavTab, label: "AI & Models", icon: Cpu },
  ];

  return (
    <>
      {/* Mobile Top Header */}
      <header className="md:hidden h-12 px-4 bg-zinc-950/95 backdrop-blur border-b border-zinc-800/80 flex items-center justify-between shrink-0 z-30 select-none">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-emerald-400" />
          <span className="font-mono text-xs font-semibold tracking-wider text-zinc-100">
            JOB BUCKET
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800/60">
            <ShieldCheck className="w-2.5 h-2.5" />
            ONLINE
          </span>
          {jobCount > 0 && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-zinc-900 text-zinc-400 border border-zinc-800">
              {jobCount}
            </span>
          )}
        </div>
      </header>

      {/* Mobile Bottom Navigation Rail */}
      <nav
        aria-label="Mobile Navigation"
        className="md:hidden fixed bottom-0 inset-x-0 h-16 bg-zinc-950/95 backdrop-blur border-t border-zinc-800/90 flex items-center justify-around px-2 z-40 select-none"
      >
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id)}
              className={`flex-1 flex flex-col items-center justify-center py-1.5 rounded-lg transition-colors relative ${
                isActive
                  ? "text-emerald-400"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              <div className="relative">
                <Icon className={`w-5 h-5 ${isActive ? "text-emerald-400 stroke-[2.2]" : "text-zinc-400"}`} />
                {item.badge && (
                  <span className="absolute -top-1 -right-2 font-mono text-[9px] px-1 rounded-full bg-emerald-900 text-emerald-200 border border-emerald-700">
                    {item.badge}
                  </span>
                )}
              </div>
              <span className={`text-[10px] font-medium tracking-tight mt-0.5 ${isActive ? "text-emerald-300 font-semibold" : "text-zinc-400"}`}>
                {item.label}
              </span>
            </button>
          );
        })}
      </nav>
    </>
  );
}
