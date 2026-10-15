"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  User,
  ShieldCheck,
  Save,
  Check,
  UploadCloud,
  Plus,
  Trash2,
  FolderGit2,
  Briefcase,
  GraduationCap,
  Code2,
  Sparkles,
  FileText,
  AlertCircle,
  ExternalLink,
  FileCode,
  Download,
  Copy,
} from "lucide-react";

interface ProjectItem {
  name: string;
  tech_stack: string;
  github_url?: string;
  bullets: string[];
}

interface WorkItem {
  company: string;
  role: string;
  period: string;
  location: string;
  client?: string;
  bullets: string[];
}

interface EducationItem {
  degree: string;
  period: string;
  institution: string;
  topics?: string;
}

export function ProfileFactsView() {
  const [profile, setProfile] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<"edit" | "resume" | "preview" | "raw">("edit");
  const [profileMd, setProfileMd] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isCopiedLatex, setIsCopiedLatex] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleCopyLatex = () => {
    if (!profile?.master_latex_source) return;
    navigator.clipboard.writeText(profile.master_latex_source);
    setIsCopiedLatex(true);
    setTimeout(() => setIsCopiedLatex(false), 2500);
  };

  const handleDownloadTex = () => {
    const source = profile?.master_latex_source || "";
    const blob = new Blob([source], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "Abhishek_Dwivedi_Master_CV.tex";
    a.click();
    URL.revokeObjectURL(url);
  };

  const loadData = async () => {
    try {
      const res = await fetch("/api/profile");
      if (res.ok) {
        const data = await res.json();
        // Normalize projects, work_history, technical_skills
        if (typeof data.projects === "string") {
          try {
            data.projects = JSON.parse(data.projects);
          } catch {
            data.projects = [];
          }
        }
        if (!Array.isArray(data.projects)) data.projects = [];

        if (typeof data.work_history === "string") {
          try {
            data.work_history = JSON.parse(data.work_history);
          } catch {
            data.work_history = [];
          }
        }
        if (!Array.isArray(data.work_history)) data.work_history = [];

        if (typeof data.education === "string") {
          try {
            data.education = JSON.parse(data.education);
          } catch {
            data.education = [];
          }
        }
        if (!Array.isArray(data.education)) data.education = [];

        if (typeof data.technical_skills === "string") {
          try {
            data.technical_skills = JSON.parse(data.technical_skills);
          } catch {
            data.technical_skills = {};
          }
        }
        if (!data.technical_skills || typeof data.technical_skills !== "object") {
          data.technical_skills = {
            languages: [],
            frameworks: [],
            databases: [],
            devops_cloud: [],
            tools: [],
          };
        }

        setProfile(data);
      }

      const mdRes = await fetch("/api/profile/markdown");
      if (mdRes.ok) {
        const d = await mdRes.json();
        setProfileMd(d.markdown);
      }
    } catch (err) {
      console.error("Failed to load profile data", err);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleSave = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
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
        // Refresh markdown preview
        const mdRes = await fetch("/api/profile/markdown");
        if (mdRes.ok) {
          const d = await mdRes.json();
          setProfileMd(d.markdown);
        }
      } else {
        alert("Failed to save profile.");
      }
    } catch (err) {
      console.error(err);
      alert("Failed to save profile.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleFileProcess = async (file: File) => {
    setIsUploading(true);
    setUploadMessage(null);
    setUploadError(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/profile/upload-resume", {
        method: "POST",
        body: formData,
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to parse resume.");
      }

      setUploadMessage(data.message || "Resume parsed and profile updated successfully.");
      await loadData();
    } catch (err: any) {
      setUploadError(err.message || "Error processing uploaded resume.");
    } finally {
      setIsUploading(false);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFileProcess(e.target.files[0]);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileProcess(e.dataTransfer.files[0]);
    }
  };

  // Project Vault Helpers
  const addProject = () => {
    const updated = { ...profile };
    updated.projects = [
      {
        name: "New High-Impact Project",
        tech_stack: "Python, FastAPI, PostgreSQL",
        github_url: "",
        bullets: ["Engineered core backend service with automated CI/CD pipeline."],
      },
      ...updated.projects,
    ];
    setProfile(updated);
  };

  const removeProject = (index: number) => {
    const updated = { ...profile };
    updated.projects = updated.projects.filter((_: any, i: number) => i !== index);
    setProfile(updated);
  };

  const updateProjectField = (index: number, field: string, value: any) => {
    const updated = { ...profile };
    updated.projects[index] = { ...updated.projects[index], [field]: value };
    setProfile(updated);
  };

  const addProjectBullet = (projIndex: number) => {
    const updated = { ...profile };
    const curBullets = updated.projects[projIndex].bullets || [];
    updated.projects[projIndex].bullets = [...curBullets, "Engineered performance optimization and test coverage."];
    setProfile(updated);
  };

  const updateProjectBullet = (projIndex: number, bulletIndex: number, value: string) => {
    const updated = { ...profile };
    updated.projects[projIndex].bullets[bulletIndex] = value;
    setProfile(updated);
  };

  const removeProjectBullet = (projIndex: number, bulletIndex: number) => {
    const updated = { ...profile };
    updated.projects[projIndex].bullets = updated.projects[projIndex].bullets.filter(
      (_: any, i: number) => i !== bulletIndex
    );
    setProfile(updated);
  };

  // Work History Helpers
  const addExperience = () => {
    const updated = { ...profile };
    updated.work_history = [
      {
        company: "Company Name",
        role: "Senior Software Engineer",
        period: "2023 - Present",
        location: "Bengaluru, India",
        client: "",
        bullets: ["Spearheaded engineering initiatives and microservices architecture."],
      },
      ...updated.work_history,
    ];
    setProfile(updated);
  };

  const removeExperience = (index: number) => {
    const updated = { ...profile };
    updated.work_history = updated.work_history.filter((_: any, i: number) => i !== index);
    setProfile(updated);
  };

  const updateExperienceField = (index: number, field: string, value: any) => {
    const updated = { ...profile };
    updated.work_history[index] = { ...updated.work_history[index], [field]: value };
    setProfile(updated);
  };

  const addExperienceBullet = (workIndex: number) => {
    const updated = { ...profile };
    const curBullets = updated.work_history[workIndex].bullets || [];
    updated.work_history[workIndex].bullets = [...curBullets, "Delivered mission-critical backend enhancements."];
    setProfile(updated);
  };

  const updateExperienceBullet = (workIndex: number, bulletIndex: number, value: string) => {
    const updated = { ...profile };
    updated.work_history[workIndex].bullets[bulletIndex] = value;
    setProfile(updated);
  };

  const removeExperienceBullet = (workIndex: number, bulletIndex: number) => {
    const updated = { ...profile };
    updated.work_history[workIndex].bullets = updated.work_history[workIndex].bullets.filter(
      (_: any, i: number) => i !== bulletIndex
    );
    setProfile(updated);
  };

  if (!profile) {
    return (
      <div className="flex-1 flex items-center justify-center bg-zinc-950 font-mono text-xs text-zinc-500">
        Loading candidate profile...
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Top Header */}
      <header className="h-auto md:h-14 px-4 md:px-6 py-2.5 md:py-0 border-b border-zinc-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-2 shrink-0 bg-zinc-950/90">
        <div className="flex items-center gap-3">
          <User className="w-4 h-4 text-emerald-400" />
          <span className="font-semibold text-sm text-zinc-100">Candidate Profile &amp; Project Vault</span>
          <span className="hidden lg:inline text-[11px] font-mono text-zinc-500">Personal Details, Master Resume &amp; Project Portfolio</span>
        </div>

        <div className="flex items-center gap-1.5 md:gap-2 overflow-x-auto w-full md:w-auto">
          <button
            onClick={() => setActiveTab("edit")}
            className={`px-3 py-1.5 rounded text-xs font-mono transition-colors whitespace-nowrap ${
              activeTab === "edit"
                ? "bg-zinc-800 text-zinc-100 border border-zinc-700"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Profile Details
          </button>
          <button
            onClick={() => setActiveTab("resume")}
            className={`px-3 py-1.5 rounded text-xs font-mono transition-colors whitespace-nowrap ${
              activeTab === "resume"
                ? "bg-zinc-800 text-zinc-100 border border-zinc-700"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Master Resume (LaTeX)
          </button>
          <button
            onClick={() => setActiveTab("preview")}
            className={`px-3 py-1.5 rounded text-xs font-mono transition-colors whitespace-nowrap ${
              activeTab === "preview"
                ? "bg-zinc-800 text-zinc-100 border border-zinc-700"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            AI Profile View
          </button>
          <button
            onClick={() => setActiveTab("raw")}
            className={`px-3 py-1.5 rounded text-xs font-mono transition-colors whitespace-nowrap ${
              activeTab === "raw"
                ? "bg-zinc-800 text-zinc-100 border border-zinc-700"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Raw JSON
          </button>

          <button
            onClick={() => handleSave()}
            disabled={isSaving}
            className="flex items-center gap-1.5 ml-2 px-3.5 py-1.5 rounded bg-zinc-100 hover:bg-white text-zinc-950 text-xs font-semibold transition-colors disabled:opacity-50 shrink-0"
          >
            {saveSuccess ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Save className="w-3.5 h-3.5" />}
            <span>{isSaving ? "Saving..." : saveSuccess ? "Saved" : "Save Profile"}</span>
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === "edit" ? (
          <div className="max-w-5xl mx-auto space-y-8">
            {/* Dual Onboarding Banner / Dropzone */}
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={`p-6 rounded-lg border-2 border-dashed transition-all text-center ${
                isDragging
                  ? "border-emerald-500 bg-emerald-950/20"
                  : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700"
              }`}
            >
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileInputChange}
                accept=".pdf,.docx,.txt"
                className="hidden"
              />
              <div className="flex flex-col items-center justify-center gap-2">
                <div className="p-3 bg-zinc-800/80 rounded-full text-emerald-400">
                  <UploadCloud className="w-6 h-6" />
                </div>
                <h4 className="text-sm font-semibold text-zinc-100">
                  {isUploading ? "Parsing Resume Facts via AI..." : "Upload Existing Resume to Auto-Populate Profile"}
                </h4>
                <p className="text-xs text-zinc-400 max-w-md">
                  Drop your PDF or DOCX resume here. Our engine extracts your contact info, project vault, work
                  experience, and tech skills directly into your candidate profile.
                </p>
                <button
                  type="button"
                  disabled={isUploading}
                  onClick={() => fileInputRef.current?.click()}
                  className="mt-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium rounded border border-zinc-700 transition-colors disabled:opacity-50"
                >
                  {isUploading ? "Extracting Sections..." : "Browse PDF or DOCX"}
                </button>
              </div>

              {uploadMessage && (
                <div className="mt-4 p-3 bg-emerald-950/40 border border-emerald-800/60 rounded text-xs text-emerald-300 flex items-center justify-center gap-2">
                  <Check className="w-4 h-4 text-emerald-400" />
                  <span>{uploadMessage}</span>
                </div>
              )}
              {uploadError && (
                <div className="mt-4 p-3 bg-rose-950/40 border border-rose-800/60 rounded text-xs text-rose-300 flex items-center justify-center gap-2">
                  <AlertCircle className="w-4 h-4 text-rose-400" />
                  <span>{uploadError}</span>
                </div>
              )}
            </div>

            {/* Section 1: Core Contact & Identity */}
            <div className="p-5 bg-zinc-900/60 border border-zinc-800/80 rounded-lg space-y-4">
              <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
                <div className="flex items-center gap-2">
                  <User className="w-4 h-4 text-zinc-400" />
                  <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-200 font-semibold">
                    1. Identity &amp; Contact Coordinates
                  </h3>
                </div>
                <span className="text-[11px] font-mono text-zinc-500">Unmasked for direct PDF compilation</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 text-xs">
                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">Candidate Full Name</label>
                  <input
                    type="text"
                    value={profile.candidate_name || ""}
                    onChange={(e) => setProfile({ ...profile, candidate_name: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-600 font-sans"
                    placeholder="e.g. Alex Morgan"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">Phone Number</label>
                  <input
                    type="text"
                    value={profile.phone || ""}
                    onChange={(e) => setProfile({ ...profile, phone: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-600 font-sans"
                    placeholder="e.g. +1 555-0199"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">Email Address</label>
                  <input
                    type="text"
                    value={profile.email || ""}
                    onChange={(e) => setProfile({ ...profile, email: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-600 font-sans"
                    placeholder="e.g. alex@example.com"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">Location</label>
                  <input
                    type="text"
                    value={profile.location || ""}
                    onChange={(e) => setProfile({ ...profile, location: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-600 font-sans"
                    placeholder="e.g. San Francisco, CA / Bengaluru, India"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">LinkedIn Profile URL</label>
                  <input
                    type="text"
                    value={profile.linkedin_url || ""}
                    onChange={(e) => setProfile({ ...profile, linkedin_url: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-600 font-sans"
                    placeholder="e.g. https://linkedin.com/in/alexmorgan"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1 font-medium">GitHub Profile URL</label>
                  <input
                    type="text"
                    value={profile.github_url || ""}
                    onChange={(e) => setProfile({ ...profile, github_url: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-zinc-100 focus:outline-none focus:border-zinc-600 font-sans"
                    placeholder="e.g. https://github.com/alexmorgan"
                  />
                </div>
              </div>
            </div>

            {/* Section 2: Project Vault (Unlimited Projects) */}
            <div className="p-5 bg-zinc-900/60 border border-zinc-800/80 rounded-lg space-y-4">
              <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
                <div className="flex items-center gap-2">
                  <FolderGit2 className="w-4 h-4 text-emerald-400" />
                  <div>
                    <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-200 font-semibold">
                      2. Project Vault (AI Handpicking Portfolio)
                    </h3>
                    <p className="text-[11px] text-zinc-400 mt-0.5">
                      Store all your projects here. When tailoring for a specific job, the AI evaluates the JD and
                      hand-picks the top 1 or 2 best-fit projects, tailoring their bullets to match the role.
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={addProject}
                  className="flex items-center gap-1 px-3 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 text-xs font-mono rounded border border-emerald-500/30 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Add Project</span>
                </button>
              </div>

              <div className="space-y-4">
                {profile.projects && profile.projects.length > 0 ? (
                  profile.projects.map((proj: ProjectItem, pIdx: number) => (
                    <div
                      key={pIdx}
                      className="p-4 bg-zinc-950/80 border border-zinc-800 rounded-md space-y-3 relative group"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 flex-1 text-xs">
                          <div>
                            <label className="block text-zinc-400 mb-1 font-medium">Project Name</label>
                            <input
                              type="text"
                              value={proj.name || ""}
                              onChange={(e) => updateProjectField(pIdx, "name", e.target.value)}
                              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-100 font-medium"
                              placeholder="e.g. Distributed Task Orchestrator"
                            />
                          </div>
                          <div>
                            <label className="block text-zinc-400 mb-1 font-medium">Tech Stack (comma separated)</label>
                            <input
                              type="text"
                              value={proj.tech_stack || ""}
                              onChange={(e) => updateProjectField(pIdx, "tech_stack", e.target.value)}
                              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 font-mono text-[11px]"
                              placeholder="e.g. Go, Kubernetes, gRPC, Redis"
                            />
                          </div>
                          <div>
                            <label className="block text-zinc-400 mb-1 font-medium">Repository / Live URL</label>
                            <input
                              type="text"
                              value={proj.github_url || ""}
                              onChange={(e) => updateProjectField(pIdx, "github_url", e.target.value)}
                              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-300 font-mono text-[11px]"
                              placeholder="e.g. github.com/user/project"
                            />
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={() => removeProject(pIdx)}
                          className="text-zinc-500 hover:text-rose-400 p-1.5 transition-colors"
                          title="Delete Project"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>

                      {/* Bullets Repeater */}
                      <div className="space-y-2 pt-2 border-t border-zinc-800/60">
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-mono text-zinc-400 text-[11px]">Technical Accomplishments &amp; Bullets</span>
                          <button
                            type="button"
                            onClick={() => addProjectBullet(pIdx)}
                            className="text-[11px] font-mono text-emerald-400 hover:underline flex items-center gap-1"
                          >
                            <Plus className="w-3 h-3" /> Add Bullet
                          </button>
                        </div>

                        {(proj.bullets || []).map((bText: string, bIdx: number) => (
                          <div key={bIdx} className="flex items-center gap-2">
                            <span className="text-zinc-600 font-mono text-xs">•</span>
                            <input
                              type="text"
                              value={bText}
                              onChange={(e) => updateProjectBullet(pIdx, bIdx, e.target.value)}
                              className="flex-1 bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-zinc-600"
                              placeholder="Describe architecture, outcome, and metrics..."
                            />
                            <button
                              type="button"
                              onClick={() => removeProjectBullet(pIdx, bIdx)}
                              className="text-zinc-600 hover:text-rose-400 p-1"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="text-center py-6 border border-zinc-800/80 rounded bg-zinc-950/40 text-xs text-zinc-500 font-mono">
                    No projects in vault yet. Click "+ Add Project" or upload your resume above.
                  </div>
                )}
              </div>
            </div>

            {/* Section 3: Professional Experience (Work History) */}
            <div className="p-5 bg-zinc-900/60 border border-zinc-800/80 rounded-lg space-y-4">
              <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
                <div className="flex items-center gap-2">
                  <Briefcase className="w-4 h-4 text-emerald-400" />
                  <div>
                    <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-200 font-semibold">
                      3. Professional Work Experience
                    </h3>
                    <p className="text-[11px] text-zinc-400 mt-0.5">
                      Chronological career history. Bullets are grounded strictly by the AI reviewer to prevent hallucination.
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={addExperience}
                  className="flex items-center gap-1 px-3 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 text-xs font-mono rounded border border-emerald-500/30 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Add Experience</span>
                </button>
              </div>

              <div className="space-y-4">
                {profile.work_history && profile.work_history.length > 0 ? (
                  profile.work_history.map((wItem: WorkItem, wIdx: number) => (
                    <div
                      key={wIdx}
                      className="p-4 bg-zinc-950/80 border border-zinc-800 rounded-md space-y-3 relative group"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 flex-1 text-xs">
                          <div>
                            <label className="block text-zinc-400 mb-1 font-medium">Company Name</label>
                            <input
                              type="text"
                              value={wItem.company || ""}
                              onChange={(e) => updateExperienceField(wIdx, "company", e.target.value)}
                              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-100 font-medium"
                              placeholder="e.g. PostFinance AG"
                            />
                          </div>
                          <div>
                            <label className="block text-zinc-400 mb-1 font-medium">Job Title / Role</label>
                            <input
                              type="text"
                              value={wItem.role || ""}
                              onChange={(e) => updateExperienceField(wIdx, "role", e.target.value)}
                              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-100"
                              placeholder="e.g. Backend Software Engineer"
                            />
                          </div>
                          <div>
                            <label className="block text-zinc-400 mb-1 font-medium">Period / Dates</label>
                            <input
                              type="text"
                              value={wItem.period || ""}
                              onChange={(e) => updateExperienceField(wIdx, "period", e.target.value)}
                              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200 font-mono text-[11px]"
                              placeholder="e.g. Jan 2022 - Present"
                            />
                          </div>
                          <div>
                            <label className="block text-zinc-400 mb-1 font-medium">Location</label>
                            <input
                              type="text"
                              value={wItem.location || ""}
                              onChange={(e) => updateExperienceField(wIdx, "location", e.target.value)}
                              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-zinc-200"
                              placeholder="e.g. Bern, Switzerland"
                            />
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={() => removeExperience(wIdx)}
                          className="text-zinc-500 hover:text-rose-400 p-1.5 transition-colors"
                          title="Delete Experience"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>

                      {/* Experience Bullets */}
                      <div className="space-y-2 pt-2 border-t border-zinc-800/60">
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-mono text-zinc-400 text-[11px]">Role Impact Bullets (Grounded Facts)</span>
                          <button
                            type="button"
                            onClick={() => addExperienceBullet(wIdx)}
                            className="text-[11px] font-mono text-emerald-400 hover:underline flex items-center gap-1"
                          >
                            <Plus className="w-3 h-3" /> Add Bullet
                          </button>
                        </div>

                        {(wItem.bullets || []).map((bText: string, bIdx: number) => (
                          <div key={bIdx} className="flex items-center gap-2">
                            <span className="text-zinc-600 font-mono text-xs">•</span>
                            <input
                              type="text"
                              value={bText}
                              onChange={(e) => updateExperienceBullet(wIdx, bIdx, e.target.value)}
                              className="flex-1 bg-zinc-900 border border-zinc-800 rounded px-2.5 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-zinc-600"
                              placeholder="Architected SWIFT ingestion pipeline handling 100K transactions..."
                            />
                            <button
                              type="button"
                              onClick={() => removeExperienceBullet(wIdx, bIdx)}
                              className="text-zinc-600 hover:text-rose-400 p-1"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="text-center py-6 border border-zinc-800/80 rounded bg-zinc-950/40 text-xs text-zinc-500 font-mono">
                    No work experiences added yet. Click "+ Add Experience" or upload your resume above.
                  </div>
                )}
              </div>
            </div>

            {/* Section 4: Technical Skills Matrix */}
            <div className="p-5 bg-zinc-900/60 border border-zinc-800/80 rounded-lg space-y-4">
              <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
                <div className="flex items-center gap-2">
                  <Code2 className="w-4 h-4 text-emerald-400" />
                  <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-200 font-semibold">
                    4. Technical Skills Matrix
                  </h3>
                </div>
                <span className="text-[11px] font-mono text-zinc-500">Categorized competencies</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                {["languages", "frameworks", "databases", "devops_cloud", "tools"].map((catKey) => {
                  const val = profile.technical_skills?.[catKey];
                  const displayStr = Array.isArray(val) ? val.join(", ") : val || "";
                  const catTitle = catKey.replace("_", " ").toUpperCase();

                  return (
                    <div key={catKey}>
                      <label className="block text-zinc-400 mb-1 font-mono text-[11px]">{catTitle}</label>
                      <input
                        type="text"
                        value={displayStr}
                        onChange={(e) => {
                          const items = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
                          const updated = {
                            ...profile,
                            technical_skills: {
                              ...profile.technical_skills,
                              [catKey]: items,
                            },
                          };
                          setProfile(updated);
                        }}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-zinc-200 font-mono text-xs focus:outline-none focus:border-zinc-600"
                        placeholder="Comma-separated items (e.g. Java, Python, TypeScript)"
                      />
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Save Profile Button */}
            <div className="pt-2">
              <button
                type="button"
                onClick={() => handleSave()}
                disabled={isSaving}
                className="flex items-center gap-2 px-6 py-2.5 rounded bg-zinc-100 hover:bg-white text-zinc-950 text-xs font-semibold transition-colors disabled:opacity-50"
              >
                {saveSuccess ? <Check className="w-4 h-4 text-emerald-600" /> : <Save className="w-4 h-4" />}
                <span>{isSaving ? "Saving..." : saveSuccess ? "Saved to Database" : "Save Profile & Vault"}</span>
              </button>
            </div>
          </div>
        ) : activeTab === "resume" ? (
          <div className="max-w-5xl mx-auto space-y-4">
            {/* Top Toolbar for LaTeX */}
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 p-4 bg-zinc-900/60 border border-zinc-800 rounded-lg">
              <div>
                <div className="flex items-center gap-2 text-zinc-100 font-semibold text-xs">
                  <FileCode className="w-4 h-4 text-emerald-400" />
                  <span>Master LaTeX Resume Source</span>
                </div>
                <p className="text-[11px] text-zinc-400 mt-0.5">
                  Your baseline ATS LaTeX resume template used as the source for all tailored applications.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleDownloadTex}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-800 transition-colors"
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Export .tex</span>
                </button>

                <button
                  type="button"
                  onClick={handleCopyLatex}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold bg-zinc-100 hover:bg-white text-zinc-950 transition-colors"
                >
                  {isCopiedLatex ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{isCopiedLatex ? "Copied" : "Copy Code"}</span>
                </button>

                <a
                  href="https://www.overleaf.com/project"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-emerald-950/80 hover:bg-emerald-900/80 text-emerald-400 border border-emerald-800/80 transition-colors"
                >
                  <span>Open Overleaf</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            </div>

            {/* LaTeX Code Editor */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-950 overflow-hidden flex flex-col">
              <div className="px-4 py-2 border-b border-zinc-800 bg-zinc-900/40 text-[11px] font-mono text-zinc-500 uppercase tracking-wider flex items-center justify-between">
                <span>Source: master_resume.tex</span>
                <span className="text-zinc-500 font-mono text-[10px]">
                  {(profile?.master_latex_source || "").length} characters
                </span>
              </div>
              <textarea
                rows={22}
                value={profile?.master_latex_source || ""}
                onChange={(e) => {
                  setProfile((prev: any) => ({
                    ...prev,
                    master_latex_source: e.target.value,
                  }));
                }}
                placeholder="Paste or write LaTeX moderncv / article resume code here..."
                className="w-full p-4 bg-transparent font-mono text-xs leading-relaxed text-zinc-200 placeholder-zinc-600 resize-y focus:outline-none"
                spellCheck={false}
              />
            </div>
          </div>
        ) : activeTab === "preview" ? (
          <div className="max-w-4xl mx-auto space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono text-zinc-400">AI Profile Facts Preview (Used for Tailoring &amp; Grounding)</span>
            </div>

            <pre className="p-4 bg-zinc-900 border border-zinc-800 rounded text-xs font-mono text-zinc-300 overflow-x-auto leading-relaxed">
              {profileMd}
            </pre>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono text-zinc-400">Full Raw Database Profile JSON</span>
            </div>

            <textarea
              rows={24}
              value={JSON.stringify(profile, null, 2)}
              onChange={(e) => {
                try {
                  setProfile(JSON.parse(e.target.value));
                } catch {
                  // Allow continuous typing
                }
              }}
              className="w-full bg-zinc-900 border border-zinc-800 rounded p-4 font-mono text-xs text-zinc-300 focus:outline-none focus:border-zinc-600"
            />
          </div>
        )}
      </div>
    </div>
  );
}
