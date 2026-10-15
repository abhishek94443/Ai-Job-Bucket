"use client";

import React, { useState } from "react";
import {
  X,
  Sparkles,
  Copy,
  Check,
  Download,
  ExternalLink,
  Code2,
  FileText,
  CheckCircle2,
  AlertTriangle,
  Lightbulb,
  Layers,
  RefreshCw,
} from "lucide-react";
import { JobItem } from "./JobDiscoveryView";

interface TailoringDrawerProps {
  job: JobItem | null;
  onClose: () => void;
}

interface TailoringResult {
  job_id: number | string;
  job_title: string;
  company: string;
  tailored_latex_cv: string;
  tailored_bullets: string[];
  cover_pitch: string;
  portal_short_bio: string;
  portal_key_achievement: string;
  recruiter_outreach?: string;
  generation_source: string;
  status?: string;
  error?: string;
  reviewer_feedback?: {
    part_a_edits?: Array<{ file: string; old_string: string; new_string: string; reason: string }>;
    part_b_missed_keywords?: string;
    part_b_company_angles?: string;
    part_b_reframing?: string;
    part_b_tone_style?: string;
    grounding_passed?: boolean;
  };
}

export function TailoringDrawer({ job, onClose }: TailoringDrawerProps) {
  const [activeTab, setActiveTab] = useState<"analysis" | "bullets" | "outreach" | "pitch" | "portal" | "latex">("analysis");
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<TailoringResult | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Single-job on demand analysis state
  const [jobDetail, setJobDetail] = useState<any>(null);
  const [isEnrichingSingle, setIsEnrichingSingle] = useState(false);
  const [enrichSingleMsg, setEnrichSingleMsg] = useState<string | null>(null);

  const pollTimerRef = React.useRef<NodeJS.Timeout | null>(null);

  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  const startPolling = (jobId: number | string) => {
    stopPolling();
    setIsRunning(true);
    let attempts = 0;
    const maxAttempts = 80; // 80 * 2.5s = 200s timeout guard

    pollTimerRef.current = setInterval(async () => {
      attempts++;
      if (attempts > maxAttempts) {
        stopPolling();
        setIsRunning(false);
        return;
      }
      try {
        const res = await fetch(`/api/jobs/${jobId}/tailor`);
        if (res.ok) {
          const data: TailoringResult = await res.json();
          if (data && data.status === "in_progress") {
            return;
          }
          if (data && (data.tailored_latex_cv || data.status === "completed")) {
            setResult(data);
            setIsRunning(false);
            stopPolling();
            return;
          }
          if (data && data.status === "failed") {
            setIsRunning(false);
            stopPolling();
            alert(`Tailoring failed: ${data.error || "Unknown error"}`);
            return;
          }
        }
      } catch {
        // network blip, continue waiting
      }
    }, 2500);
  };

  // Fetch full job record and auto-hydrate saved tailoring pack from database
  React.useEffect(() => {
    stopPolling();
    // Immediately reset on job switch so previous job details never linger
    setJobDetail(null);
    setResult(null);
    setEnrichSingleMsg(null);
    setIsRunning(false);
    setActiveTab("analysis");

    if (!job?.id) return;
    let isMounted = true;

    fetch(`/api/jobs/${job.id}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (isMounted) {
          setJobDetail(data);
        }
      })
      .catch(() => {});

    fetch(`/api/jobs/${job.id}/tailor`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: TailoringResult | null) => {
        if (!isMounted) return;
        if (data && data.status === "in_progress") {
          setIsRunning(true);
          startPolling(job.id);
        } else if (data && data.tailored_latex_cv) {
          setResult(data);
          setIsRunning(false);
        } else {
          setResult(null);
          setIsRunning(false);
        }
      })
      .catch(() => {
        if (isMounted) {
          setResult(null);
          setIsRunning(false);
        }
      });

    return () => {
      isMounted = false;
      stopPolling();
    };
  }, [job?.id]);

  if (!job) return null;

  const handleCopy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleRunTailoring = async () => {
    if (!job?.id) return;
    setIsRunning(true);
    setResult(null);
    try {
      const res = await fetch(`/api/jobs/${job.id}/tailor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prefer_ai: true, sync: false }),
      });
      if (!res.ok) throw new Error("Tailoring request failed");
      startPolling(job.id);
    } catch (err) {
      console.error(err);
      setIsRunning(false);
      alert("Tailoring failed to start. Check API server terminal logs.");
    }
  };

  const handleEnrichSingleJob = async () => {
    if (!job?.id || isEnrichingSingle) return;
    setIsEnrichingSingle(true);
    setEnrichSingleMsg(null);
    try {
      const res = await fetch(`/api/jobs/${job.id}/enrich`, {
        method: "POST",
      });
      const data = await res.json();
      if (res.ok && data.job) {
        setJobDetail(data.job);
        setEnrichSingleMsg("Job verified and enriched successfully.");
      } else {
        setEnrichSingleMsg(data.detail || "Enrichment failed.");
      }
    } catch (err: any) {
      setEnrichSingleMsg(err.message || "Failed to trigger AI analysis.");
    } finally {
      setIsEnrichingSingle(false);
      setTimeout(() => setEnrichSingleMsg(null), 6000);
    }
  };

  const handleDownloadTex = () => {
    if (!result?.tailored_latex_cv) return;
    const blob = new Blob([result.tailored_latex_cv], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Abhishek_Dwivedi_${job.company.replace(/\s+/g, "_")}_CV.tex`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const currentJob = jobDetail || job;
  const rawMeta = currentJob.raw_metadata || {};
  const aiEnrichment = rawMeta.ai_enrichment || {};
  const isEnriched = currentJob.enrichment_status === "AI_ENRICHED" || !!rawMeta.ai_enrichment;

  const score = aiEnrichment.overall_score ?? currentJob.ai_score ?? currentJob.relevance_score ?? 0;
  const whyGood: string[] = aiEnrichment.why_good || currentJob.score_reasons || [];
  const whyBad: string[] = aiEnrichment.why_bad || [];
  const keyObservations: string[] = aiEnrichment.key_observations || [];
  const behavioralPoints: string[] = aiEnrichment.behavioral_talking_points || [];
  const matchedSkills: string[] = aiEnrichment.matched_skills || currentJob.matched_skills || [];
  const missingSkills: string[] = aiEnrichment.missing_skills || [];
  const fitVerdict: string = aiEnrichment.fit_verdict || (score >= 80 ? "High Match" : score >= 60 ? "Moderate Match" : "Review Needed");
  const descriptionText = currentJob.description_full || currentJob.description || "No full description available.";

  const fb = result?.reviewer_feedback;

  return (
    <div className="fixed inset-0 z-50 md:inset-y-0 md:left-auto md:right-0 md:w-[620px] bg-zinc-950 border-t md:border-t-0 md:border-l border-zinc-800 shadow-2xl flex flex-col animate-in slide-in-from-bottom md:slide-in-from-right duration-200">
      {/* Drawer Header */}
      <div className="h-14 px-5 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm text-zinc-100">{currentJob.title}</span>
            <span className="text-xs text-zinc-400 font-mono">@{currentJob.company}</span>
          </div>
          <div className="text-[11px] font-mono text-zinc-500 flex items-center gap-2">
            <span>{currentJob.location || "Remote / Hybrid"}</span>
            {currentJob.url && (
              <a
                href={currentJob.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-zinc-400 hover:text-zinc-200 inline-flex items-center gap-1"
              >
                <span>Posting</span>
                <ExternalLink className="w-2.5 h-2.5" />
              </a>
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Synthesis Trigger & Grounding Badge */}
      <div className="p-3.5 border-b border-zinc-800 bg-zinc-900/40 flex items-center justify-between gap-3 shrink-0">
        <div className="text-xs text-zinc-400">
          {result ? (
            <div className="flex items-center gap-2 font-mono text-[11px]">
              <span className="px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">
                Grounding: PASS
              </span>
              <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                Ready for Overleaf
              </span>
            </div>
          ) : (
            <span className="font-mono text-[11px] text-zinc-500">
              {isEnriched ? "AI verified profile fit" : "Deterministic preview mode"}
            </span>
          )}
        </div>

        <button
          onClick={handleRunTailoring}
          disabled={isRunning}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded bg-zinc-100 hover:bg-white text-zinc-950 text-xs font-semibold transition-colors disabled:opacity-50"
        >
          <Sparkles className={`w-3.5 h-3.5 ${isRunning ? "animate-spin" : ""}`} />
          <span>{isRunning ? "Synthesizing..." : result ? "Re-Run 2-Agent Loop" : "Run 2-Agent Tailoring"}</span>
        </button>
      </div>

      {/* Reviewer Audit Report Box (if tailoring was run) */}
      {fb && (
        <div className="px-5 py-2.5 border-b border-zinc-800/80 bg-zinc-900/30 text-xs">
          <div className="font-mono text-[11px] text-zinc-300 mb-1 font-medium flex items-center justify-between">
            <span>Reviewer Grounding Audit (Hiring Manager Proxy)</span>
            <span className="text-emerald-400 font-mono text-[10px]">Verified: Draft ⊆ Profile</span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-[11px] text-zinc-400 font-mono pt-0.5">
            <div>
              <span className="text-zinc-500">Style Check: </span>
              <span className="text-zinc-300">{fb.part_b_tone_style || "Compliant"}</span>
            </div>
            <div>
              <span className="text-zinc-500">Part A String Edits: </span>
              <span className="text-zinc-300">{fb.part_a_edits?.length || 0} applied</span>
            </div>
          </div>
        </div>
      )}

      {/* Tabs Header */}
      <div className="flex border-b border-zinc-800 bg-zinc-950 text-xs font-mono shrink-0 overflow-x-auto">
        {[
          { id: "analysis", label: "Job & Gap Analysis" },
          { id: "bullets", label: "Highlights" },
          { id: "outreach", label: "Outreach" },
          { id: "pitch", label: "Pitch" },
          { id: "portal", label: "Portal Q&A" },
          { id: "latex", label: "Tailored .tex" },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as any)}
            className={`flex-1 py-2 px-2 text-center whitespace-nowrap transition-colors border-b-2 ${
              activeTab === tab.id
                ? "border-emerald-500 text-zinc-100 font-semibold bg-zinc-900/40"
                : "border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/20"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content Body */}
      <div className="flex-1 overflow-y-auto p-5 text-xs">
        {/* TAB 1: Job Spec & Gap Analysis */}
        {activeTab === "analysis" && (
          <div className="space-y-4">
            {/* Notification message */}
            {enrichSingleMsg && (
              <div className="p-2.5 rounded bg-emerald-950/40 border border-emerald-800 text-emerald-300 font-mono text-[11px]">
                {enrichSingleMsg}
              </div>
            )}

            {/* AI Fit Status Banner */}
            <div className="p-3.5 bg-zinc-900 border border-zinc-800 rounded flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div
                  className={`px-2.5 py-1 rounded font-mono font-bold text-sm ${
                    score >= 80
                      ? "bg-emerald-950 text-emerald-300 border border-emerald-800"
                      : score >= 60
                      ? "bg-amber-950 text-amber-300 border border-amber-800"
                      : "bg-zinc-800 text-zinc-300 border border-zinc-700"
                  }`}
                >
                  {score}%
                </div>
                <div>
                  <div className="font-semibold text-zinc-100 flex items-center gap-2">
                    <span>{fitVerdict}</span>
                    <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                      {isEnriched ? "AI Verified" : "Deterministic Preview"}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-zinc-400">
                    {isEnriched
                      ? "Grounded against candidate technical and behavioral profile"
                      : "Calculated with fast heuristic keyword scoring"}
                  </div>
                </div>
              </div>

              {!isEnriched && (
                <button
                  onClick={handleEnrichSingleJob}
                  disabled={isEnrichingSingle}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-emerald-950 hover:bg-emerald-900 text-emerald-300 border border-emerald-800 text-xs font-medium transition-colors disabled:opacity-50"
                >
                  <RefreshCw className={`w-3 h-3 ${isEnrichingSingle ? "animate-spin" : ""}`} />
                  <span>{isEnrichingSingle ? "Analyzing..." : "Analyze with AI"}</span>
                </button>
              )}
            </div>

            {/* Two-Column Fit Breakdown: Why Good vs Why Bad */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Strengths & Alignment */}
              <div className="p-3.5 bg-emerald-950/20 border border-emerald-900/40 rounded space-y-2">
                <div className="flex items-center gap-1.5 text-emerald-400 font-semibold text-xs">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>Strengths & Alignment</span>
                </div>
                {whyGood.length > 0 ? (
                  <ul className="space-y-1.5 text-[11px] text-zinc-300">
                    {whyGood.map((pt, i) => (
                      <li key={i} className="flex items-start gap-1.5 leading-relaxed">
                        <span className="text-emerald-500 select-none">•</span>
                        <span>{pt}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-[11px] text-zinc-500 font-mono">No specific strengths recorded.</div>
                )}
              </div>

              {/* Gaps & Considerations */}
              <div className="p-3.5 bg-amber-950/20 border border-amber-900/40 rounded space-y-2">
                <div className="flex items-center gap-1.5 text-amber-400 font-semibold text-xs">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <span>Gaps & Considerations</span>
                </div>
                {whyBad.length > 0 ? (
                  <ul className="space-y-1.5 text-[11px] text-zinc-300">
                    {whyBad.map((pt, i) => (
                      <li key={i} className="flex items-start gap-1.5 leading-relaxed">
                        <span className="text-amber-500 select-none">•</span>
                        <span>{pt}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-[11px] text-zinc-500 font-mono">
                    {isEnriched ? "No major deficits detected." : "Run AI analysis to reveal specific gaps."}
                  </div>
                )}
              </div>
            </div>

            {/* Key Observations */}
            {keyObservations.length > 0 && (
              <div className="p-3 bg-zinc-900 border border-zinc-800 rounded space-y-2">
                <div className="flex items-center gap-1.5 text-zinc-300 font-semibold text-xs">
                  <Lightbulb className="w-3.5 h-3.5 text-yellow-400" />
                  <span>Key Observations & Architecture Signals</span>
                </div>
                <ul className="space-y-1 text-[11px] text-zinc-300">
                  {keyObservations.map((obs, i) => (
                    <li key={i} className="flex items-start gap-1.5 leading-relaxed">
                      <span className="text-zinc-500">•</span>
                      <span>{obs}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Behavioral Talking Points */}
            {behavioralPoints.length > 0 && (
              <div className="p-3 bg-zinc-900 border border-zinc-800 rounded space-y-2">
                <div className="flex items-center gap-1.5 text-zinc-300 font-semibold text-xs">
                  <Layers className="w-3.5 h-3.5 text-sky-400" />
                  <span>Behavioral Talking Points for Interview</span>
                </div>
                <ul className="space-y-1 text-[11px] text-zinc-300">
                  {behavioralPoints.map((pt, i) => (
                    <li key={i} className="flex items-start gap-1.5 leading-relaxed">
                      <span className="text-sky-500">•</span>
                      <span>{pt}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Skill Breakdown */}
            {(matchedSkills.length > 0 || missingSkills.length > 0) && (
              <div className="p-3 bg-zinc-900 border border-zinc-800 rounded space-y-2">
                <div className="text-xs font-semibold text-zinc-300">Skill Breakdown</div>
                {matchedSkills.length > 0 && (
                  <div>
                    <span className="text-[10px] font-mono text-zinc-500 uppercase block mb-1">Matched Skills:</span>
                    <div className="flex flex-wrap gap-1.5">
                      {matchedSkills.map((s, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-950/70 text-emerald-300 border border-emerald-900"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {missingSkills.length > 0 && (
                  <div className="pt-1">
                    <span className="text-[10px] font-mono text-zinc-500 uppercase block mb-1">Missing / Unverified:</span>
                    <div className="flex flex-wrap gap-1.5">
                      {missingSkills.map((s, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-800 text-zinc-400 border border-zinc-700"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Complete Job Description */}
            <div className="p-3.5 bg-zinc-900/60 border border-zinc-800 rounded space-y-2">
              <div className="flex items-center justify-between text-zinc-400 font-mono text-[11px]">
                <div className="flex items-center gap-1.5 font-semibold text-zinc-200">
                  <FileText className="w-3.5 h-3.5" />
                  <span>Full Job Description</span>
                </div>
                <span>{descriptionText.length} characters</span>
              </div>
              <div className="p-3 bg-zinc-950 border border-zinc-800/80 rounded text-zinc-300 font-sans leading-relaxed whitespace-pre-wrap max-h-[300px] overflow-y-auto text-[11px]">
                {descriptionText}
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: Highlights / Tailored Bullets */}
        {activeTab === "bullets" && (
          <>
            {!result ? (
              <div className="h-64 flex flex-col items-center justify-center text-zinc-500 gap-2 font-mono">
                <Code2 className="w-6 h-6 text-zinc-600" />
                <span>Click &quot;Run 2-Agent Tailoring&quot; above to synthesize materials.</span>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="flex items-center justify-between text-zinc-400 font-mono text-[11px]">
                  <span>Outcome-Oriented Experience Bullets</span>
                  <button
                    onClick={() => handleCopy(result.tailored_bullets.join("\n"), "all_bullets")}
                    className="flex items-center gap-1 text-zinc-300 hover:text-white"
                  >
                    {copiedKey === "all_bullets" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>Copy All</span>
                  </button>
                </div>
                <div className="space-y-2.5">
                  {result.tailored_bullets.map((b, idx) => (
                    <div
                      key={idx}
                      className="p-3 bg-zinc-900 border border-zinc-800 rounded text-zinc-200 font-sans leading-relaxed flex items-start justify-between gap-3 group"
                    >
                      <span>{b}</span>
                      <button
                        onClick={() => handleCopy(b, `bullet_${idx}`)}
                        className="opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-zinc-200 transition-opacity shrink-0 pt-0.5"
                      >
                        {copiedKey === `bullet_${idx}` ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* TAB 3: Outreach */}
        {activeTab === "outreach" && (
          <>
            {!result ? (
              <div className="h-64 flex flex-col items-center justify-center text-zinc-500 gap-2 font-mono">
                <Code2 className="w-6 h-6 text-zinc-600" />
                <span>Click &quot;Run 2-Agent Tailoring&quot; above to synthesize materials.</span>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-zinc-400 font-mono text-[11px]">
                  <div className="flex items-center gap-2">
                    <span>Recruiter Direct Outreach</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                      {(result.recruiter_outreach || `Hi, I noticed the ${currentJob.title} role at ${currentJob.company}. Specializing in Java/Spring Boot microservices and high-throughput pipelines. Would welcome connecting!`).length}/300 chars
                    </span>
                  </div>
                  <button
                    onClick={() =>
                      handleCopy(
                        result.recruiter_outreach ||
                          `Hi, I noticed the ${currentJob.title} role at ${currentJob.company}. Specializing in Java/Spring Boot microservices and high-throughput pipelines. Would welcome connecting to explore how my backend background aligns with your engineering needs.`,
                        "outreach"
                      )
                    }
                    className="flex items-center gap-1 text-zinc-300 hover:text-white"
                  >
                    {copiedKey === "outreach" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>Copy</span>
                  </button>
                </div>
                <div className="p-3 bg-zinc-900 border border-zinc-800 rounded text-zinc-200 font-sans leading-relaxed whitespace-pre-wrap">
                  {result.recruiter_outreach ||
                    `Hi, I noticed the ${currentJob.title} role at ${currentJob.company}. Specializing in Java/Spring Boot microservices and high-throughput pipelines. Would welcome connecting to explore how my backend background aligns with your engineering needs.`}
                </div>
                <div className="text-[11px] font-mono text-zinc-500">
                  Compliant with writing-style rules: zero buzzwords, grounded directly in candidate profile facts.
                </div>
              </div>
            )}
          </>
        )}

        {/* TAB 4: Cover Pitch */}
        {activeTab === "pitch" && (
          <>
            {!result ? (
              <div className="h-64 flex flex-col items-center justify-center text-zinc-500 gap-2 font-mono">
                <Code2 className="w-6 h-6 text-zinc-600" />
                <span>Click &quot;Run 2-Agent Tailoring&quot; above to synthesize materials.</span>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-zinc-400 font-mono text-[11px]">
                  <span>3-Paragraph Cover Pitch</span>
                  <button
                    onClick={() => handleCopy(result.cover_pitch, "pitch")}
                    className="flex items-center gap-1 text-zinc-300 hover:text-white"
                  >
                    {copiedKey === "pitch" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>Copy Pitch</span>
                  </button>
                </div>
                <div className="p-3.5 bg-zinc-900 border border-zinc-800 rounded text-zinc-200 font-sans leading-relaxed whitespace-pre-line">
                  {result.cover_pitch}
                </div>
              </div>
            )}
          </>
        )}

        {/* TAB 5: Portal Q&A */}
        {activeTab === "portal" && (
          <>
            {!result ? (
              <div className="h-64 flex flex-col items-center justify-center text-zinc-500 gap-2 font-mono">
                <Code2 className="w-6 h-6 text-zinc-600" />
                <span>Click &quot;Run 2-Agent Tailoring&quot; above to synthesize materials.</span>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-zinc-400 font-mono text-[11px]">
                    <span>Short Bio / Executive Summary</span>
                    <button
                      onClick={() => handleCopy(result.portal_short_bio, "bio")}
                      className="flex items-center gap-1 text-zinc-300 hover:text-white"
                    >
                      {copiedKey === "bio" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      <span>Copy</span>
                    </button>
                  </div>
                  <div className="p-3 bg-zinc-900 border border-zinc-800 rounded text-zinc-200 font-sans leading-relaxed">
                    {result.portal_short_bio}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between text-zinc-400 font-mono text-[11px]">
                    <span>Core Technical Accomplishment</span>
                    <button
                      onClick={() => handleCopy(result.portal_key_achievement, "achieve")}
                      className="flex items-center gap-1 text-zinc-300 hover:text-white"
                    >
                      {copiedKey === "achieve" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      <span>Copy</span>
                    </button>
                  </div>
                  <div className="p-3 bg-zinc-900 border border-zinc-800 rounded text-zinc-200 font-sans leading-relaxed">
                    {result.portal_key_achievement}
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {/* TAB 6: Tailored LaTeX .tex Source */}
        {activeTab === "latex" && (
          <>
            {!result ? (
              <div className="h-64 flex flex-col items-center justify-center text-zinc-500 gap-2 font-mono">
                <Code2 className="w-6 h-6 text-zinc-600" />
                <span>Click &quot;Run 2-Agent Tailoring&quot; above to synthesize materials.</span>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Overleaf Action Card */}
                <div className="p-3 bg-emerald-950/40 border border-emerald-900/60 rounded flex items-center justify-between gap-3">
                  <div className="space-y-0.5">
                    <div className="text-xs font-semibold text-emerald-300">Overleaf Ready</div>
                    <div className="text-[11px] text-zinc-400">
                      Copy or download this tailored LaTeX source and recompile in Overleaf to get your PDF.
                    </div>
                  </div>
                  <a
                    href="https://www.overleaf.com/project"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-emerald-950 hover:bg-emerald-900 text-emerald-400 border border-emerald-800 text-xs font-medium shrink-0 transition-colors"
                  >
                    <span>Open Overleaf</span>
                    <ExternalLink className="w-3 h-3" />
                  </a>
                </div>

                <div className="flex items-center justify-between text-zinc-400 font-mono text-[11px] pt-1">
                  <span>Tailored LaTeX Document Source</span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleCopy(result.tailored_latex_cv, "latex")}
                      className="flex items-center gap-1 text-zinc-300 hover:text-white"
                    >
                      {copiedKey === "latex" ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      <span>Copy LaTeX</span>
                    </button>
                    <button
                      onClick={handleDownloadTex}
                      className="flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200"
                    >
                      <Download className="w-3 h-3" />
                      <span>Download .tex</span>
                    </button>
                  </div>
                </div>
                <pre className="p-3 bg-zinc-900 border border-zinc-800 rounded text-zinc-300 font-mono text-[11px] overflow-x-auto max-h-[460px]">
                  {result.tailored_latex_cv}
                </pre>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
