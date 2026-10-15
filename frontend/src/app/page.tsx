"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Sidebar, NavTab, SystemStatus } from "@/components/Sidebar";
import { MobileNav } from "@/components/MobileNav";
import { JobDiscoveryView, JobItem } from "@/components/JobDiscoveryView";
import { TailoringDrawer } from "@/components/TailoringDrawer";
import { AutomationView } from "@/components/AutomationView";
import { ProfileFactsView } from "@/components/ProfileFactsView";
import { ModelManagementView } from "@/components/ModelManagementView";
import { TrackerView } from "@/components/TrackerView";

export default function Home() {
  const [activeTab, setActiveTab] = useState<NavTab>("discovery");
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [isLoadingJobs, setIsLoadingJobs] = useState(true);
  const [selectedJobForTailoring, setSelectedJobForTailoring] = useState<JobItem | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus>("connecting");

  // Health check against FastAPI /api/health to track Render reachability
  const checkHealth = useCallback(async () => {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 6000);
      const res = await fetch("/api/health", {
        signal: controller.signal,
        cache: "no-store",
      });
      clearTimeout(timeoutId);
      if (res.ok) {
        const data = await res.json();
        if (data.status === "healthy") {
          setSystemStatus("online");
          return;
        }
      }
      setSystemStatus("offline");
    } catch {
      setSystemStatus("offline");
    }
  }, []);

  useEffect(() => {
    checkHealth();
    // Poll every 25 seconds: keeps status fresh and maintains active session
    const interval = setInterval(checkHealth, 25000);
    return () => clearInterval(interval);
  }, [checkHealth]);

  // Fetch jobs from FastAPI backend
  const fetchJobs = async () => {
    setIsLoadingJobs(true);
    try {
      const res = await fetch("/api/jobs?limit=500");
      if (res.ok) {
        const data = await res.json();
        setJobs(data.jobs || []);
      }
    } catch (err) {
      console.error("Failed to fetch jobs:", err);
    } finally {
      setIsLoadingJobs(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, []);

  const handleTrackJob = async (jobId: number, status: string) => {
    try {
      await fetch(`/api/jobs/${jobId}/track`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      // update local job state
      setJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, application_status: status } : j))
      );
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="flex flex-col md:flex-row h-screen bg-zinc-950 text-zinc-100 overflow-hidden font-sans">
      {/* Mobile Top Header & Bottom Thumb Nav Rail */}
      <MobileNav
        activeTab={activeTab}
        onTabChange={setActiveTab}
        jobCount={jobs.length}
        systemStatus={systemStatus}
      />

      {/* Desktop Fixed Left Navigation Rail */}
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        jobCount={jobs.length}
        systemStatus={systemStatus}
      />

      {/* Main View Area */}
      <main className="flex-1 flex overflow-hidden pb-16 md:pb-0">
        {activeTab === "discovery" && (
          <JobDiscoveryView
            jobs={jobs}
            isLoading={isLoadingJobs}
            onRefresh={fetchJobs}
            onSelectJobForTailoring={setSelectedJobForTailoring}
            onTrackJob={handleTrackJob}
          />
        )}

        {activeTab === "tracker" && (
          <TrackerView
            jobs={jobs}
            onSelectJobForTailoring={setSelectedJobForTailoring}
            onUpdateStatus={handleTrackJob}
          />
        )}

        {activeTab === "profile" && <ProfileFactsView />}

        {activeTab === "automation" && <AutomationView onJobsUpdated={fetchJobs} />}

        {activeTab === "models" && <ModelManagementView />}
      </main>

      {/* Tailoring Drawer */}
      <TailoringDrawer
        key={selectedJobForTailoring?.id ?? "closed"}
        job={selectedJobForTailoring}
        onClose={() => setSelectedJobForTailoring(null)}
      />
    </div>
  );
}
