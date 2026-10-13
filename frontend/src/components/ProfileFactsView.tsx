"use client";

import React, { useState, useEffect } from "react";
import { User, ShieldCheck, Save, Eye, FileText, Check } from "lucide-react";

export function ProfileFactsView() {
  const [profile, setProfile] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<"edit" | "markdown">("edit");
  const [profileMd, setProfileMd] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    async function loadData() {
      try {
        const res = await fetch("/api/profile");
        if (res.ok) {
          const data = await res.json();
          setProfile(data);
        }

        const mdRes = await fetch("/api/profile/markdown");
        if (mdRes.ok) {
          const d = await mdRes.json();
          setProfileMd(d.markdown);
        }
      } catch (err) {
        console.error(err);
      }
    }
    loadData();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profile) return;
    setIsSaving(true);
    try {
      const res = await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      });
      if (res.ok) {
        setSaveSuccess(true);
        setTimeout(() => setSaveSuccess(false), 2500);
      }
    } catch (err) {
      console.error(err);
      alert("Failed to save profile.");
    } finally {
      setIsSaving(false);
    }
  };

  if (!profile) {
    return (
      <div className="flex-1 flex items-center justify-center bg-zinc-950 font-mono text-xs text-zinc-500">
        Loading candidate ground truth...
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Header */}
      <header className="h-14 px-6 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90">
        <div className="flex items-center gap-3">
          <User className="w-4 h-4 text-zinc-400" />
          <span className="font-semibold text-sm text-zinc-100">Candidate Profile &amp; Ground Truth</span>
          <span className="text-[11px] font-mono text-zinc-500">Single Source of Truth (01-candidate-profile.md)</span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveTab("edit")}
            className={`px-3 py-1.5 rounded text-xs font-mono transition-colors ${
              activeTab === "edit" ? "bg-zinc-800 text-zinc-100 border border-zinc-700" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Fact Editor
          </button>
          <button
            onClick={() => setActiveTab("markdown")}
            className={`px-3 py-1.5 rounded text-xs font-mono transition-colors ${
              activeTab === "markdown" ? "bg-zinc-800 text-zinc-100 border border-zinc-700" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            AI Grounding Preview
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === "edit" ? (
          <form onSubmit={handleSave} className="max-w-4xl space-y-6">
            {/* Ground Truth Banner */}
            <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded flex items-start gap-3">
              <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
              <div className="text-xs text-zinc-300 leading-relaxed">
                <span className="font-semibold text-white">Single Source of Truth:</span> These verified facts form your ground truth profile. They are supplied directly to the AI tailoring engine for strict factual grounding and resume generation.
              </div>
            </div>

            {/* Core Identity Fields */}
            <div className="space-y-4">
              <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-400 font-medium">1. Core Identity &amp; Contact Details</h3>
              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <label className="block text-zinc-400 mb-1">Candidate Full Name</label>
                  <input
                    type="text"
                    value={profile.candidate_name || ""}
                    onChange={(e) => setProfile({ ...profile, candidate_name: e.target.value })}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-zinc-200 focus:outline-none focus:border-zinc-600"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">Phone Number</label>
                  <input
                    type="text"
                    value={profile.phone || ""}
                    onChange={(e) => setProfile({ ...profile, phone: e.target.value })}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-zinc-200 focus:outline-none focus:border-zinc-600"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">Email Address</label>
                  <input
                    type="text"
                    value={profile.email || ""}
                    onChange={(e) => setProfile({ ...profile, email: e.target.value })}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-zinc-200 focus:outline-none focus:border-zinc-600"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">Location</label>
                  <input
                    type="text"
                    value={profile.location || ""}
                    onChange={(e) => setProfile({ ...profile, location: e.target.value })}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-zinc-200 focus:outline-none focus:border-zinc-600"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">LinkedIn URL</label>
                  <input
                    type="text"
                    value={profile.linkedin_url || ""}
                    onChange={(e) => setProfile({ ...profile, linkedin_url: e.target.value })}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-zinc-200 focus:outline-none focus:border-zinc-600"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">GitHub URL</label>
                  <input
                    type="text"
                    value={profile.github_url || ""}
                    onChange={(e) => setProfile({ ...profile, github_url: e.target.value })}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-zinc-200 focus:outline-none focus:border-zinc-600"
                  />
                </div>
              </div>
            </div>

            {/* Technical Skills JSON */}
            <div className="space-y-2">
              <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-400 font-medium">2. Technical Skills Matrix (JSON)</h3>
              <textarea
                rows={6}
                value={typeof profile.technical_skills === "object" ? JSON.stringify(profile.technical_skills, null, 2) : profile.technical_skills}
                onChange={(e) => {
                  try {
                    setProfile({ ...profile, technical_skills: JSON.parse(e.target.value) });
                  } catch {
                    // allow typing raw string
                  }
                }}
                className="w-full bg-zinc-900 border border-zinc-800 rounded p-3 font-mono text-xs text-zinc-300 focus:outline-none focus:border-zinc-600"
              />
            </div>

            {/* Work History JSON */}
            <div className="space-y-2">
              <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-400 font-medium">3. Professional Experience (JSON)</h3>
              <textarea
                rows={8}
                value={typeof profile.work_history === "object" ? JSON.stringify(profile.work_history, null, 2) : profile.work_history}
                onChange={(e) => {
                  try {
                    setProfile({ ...profile, work_history: JSON.parse(e.target.value) });
                  } catch {
                    // allow typing raw string
                  }
                }}
                className="w-full bg-zinc-900 border border-zinc-800 rounded p-3 font-mono text-xs text-zinc-300 focus:outline-none focus:border-zinc-600"
              />
            </div>

            {/* Save Button */}
            <div className="flex items-center gap-3 pt-2">
              <button
                type="submit"
                disabled={isSaving}
                className="flex items-center gap-1.5 px-4 py-2 rounded bg-zinc-100 hover:bg-white text-zinc-950 text-xs font-semibold transition-colors disabled:opacity-50"
              >
                {saveSuccess ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Save className="w-3.5 h-3.5" />}
                <span>{isSaving ? "Saving..." : saveSuccess ? "Saved to Neon PostgreSQL" : "Save Profile Facts"}</span>
              </button>
            </div>
          </form>
        ) : (
          <div className="max-w-4xl space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono text-zinc-400">Ground Truth Profile Preview (01-candidate-profile.md)</span>
            </div>

            <pre className="p-4 bg-zinc-900 border border-zinc-800 rounded text-xs font-mono text-zinc-300 overflow-x-auto leading-relaxed">
              {profileMd}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
