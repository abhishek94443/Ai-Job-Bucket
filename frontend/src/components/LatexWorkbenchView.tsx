"use client";

import React, { useState, useEffect } from "react";
import { FileCode, Download, Save, Check, Copy, ExternalLink, Code2 } from "lucide-react";

export function LatexWorkbenchView() {
  const [latexSource, setLatexSource] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // Load profile's master LaTeX source
  useEffect(() => {
    async function loadMasterLatex() {
      try {
        const res = await fetch("/api/profile");
        if (res.ok) {
          const data = await res.json();
          setLatexSource(data.master_latex_source || "");
        }
      } catch (err) {
        console.error("Failed to fetch profile LaTeX", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadMasterLatex();
  }, []);

  const handleCopyForOverleaf = () => {
    if (!latexSource) return;
    navigator.clipboard.writeText(latexSource);
    setIsCopied(true);
    setStatusMessage("LaTeX source copied to clipboard for Overleaf.");
    setTimeout(() => {
      setIsCopied(false);
      setStatusMessage(null);
    }, 3000);
  };

  const handleSaveToDb = async () => {
    setIsSaving(true);
    try {
      const getRes = await fetch("/api/profile");
      const current = await getRes.json();
      current.master_latex_source = latexSource;

      const putRes = await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(current),
      });
      if (putRes.ok) {
        setStatusMessage("Master LaTeX template saved to Neon PostgreSQL.");
        setTimeout(() => setStatusMessage(null), 3000);
      }
    } catch (err) {
      console.error(err);
      setStatusMessage("Failed to save template to database.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDownloadTex = () => {
    const blob = new Blob([latexSource], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "Abhishek_Dwivedi_Master_CV.tex";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden bg-zinc-950">
      {/* Top Controls Bar */}
      <header className="h-14 px-6 border-b border-zinc-800 flex items-center justify-between shrink-0 bg-zinc-950/90">
        <div className="flex items-center gap-3">
          <FileCode className="w-4 h-4 text-zinc-400" />
          <span className="font-semibold text-sm text-zinc-100">Master LaTeX Workbench</span>
          <span className="text-[11px] font-mono text-zinc-500">Source Editor &amp; Overleaf Pipeline</span>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={handleSaveToDb}
            disabled={isSaving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-800 transition-colors disabled:opacity-50"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{isSaving ? "Saving..." : "Save to DB"}</span>
          </button>

          <button
            onClick={handleDownloadTex}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-800 transition-colors"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Export .tex</span>
          </button>

          <button
            onClick={handleCopyForOverleaf}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded text-xs font-semibold bg-zinc-100 hover:bg-white text-zinc-950 transition-colors"
          >
            {isCopied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{isCopied ? "Copied" : "Copy for Overleaf"}</span>
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
      </header>

      {/* Subheader status */}
      <div className="px-6 py-2 border-b border-zinc-800/80 bg-zinc-900/40 text-[11px] font-mono text-zinc-400 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <span>ATS Compliant ModernCV Master Template</span>
          <span className="text-zinc-600">|</span>
          <span className="text-zinc-400">Ground Truth Factual Resume Baseline</span>
        </div>
        {statusMessage && (
          <span className="text-emerald-400 font-medium">{statusMessage}</span>
        )}
      </div>

      {/* Main Workbench Layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: LaTeX Code Editor (Expanded) */}
        <div className="flex-1 border-r border-zinc-800 flex flex-col bg-zinc-950">
          <div className="px-4 py-2 border-b border-zinc-800/60 bg-zinc-900/30 text-[11px] font-mono text-zinc-500 uppercase tracking-wider flex items-center justify-between">
            <span>Source: resume.tex (Master Template)</span>
            <span className="text-zinc-600 font-mono text-[10px]">{latexSource.length} chars</span>
          </div>
          <div className="flex-1 relative overflow-hidden">
            {isLoading ? (
              <div className="h-full flex items-center justify-center text-zinc-500 font-mono text-xs">
                Loading master template...
              </div>
            ) : (
              <textarea
                value={latexSource}
                onChange={(e) => setLatexSource(e.target.value)}
                placeholder="Paste or write LaTeX moderncv / article resume code here..."
                className="w-full h-full p-4 bg-transparent font-mono text-xs leading-relaxed text-zinc-200 placeholder-zinc-600 resize-none focus:outline-none"
                spellCheck={false}
              />
            )}
          </div>
        </div>

        {/* Right: Overleaf Workflow Guide & Quick Actions */}
        <div className="w-80 flex flex-col bg-zinc-900/20 p-5 space-y-5 shrink-0 overflow-y-auto">
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-zinc-200 font-semibold text-xs">
              <Code2 className="w-4 h-4 text-emerald-400" />
              <span>Overleaf Compilation Guide</span>
            </div>
            <p className="text-xs text-zinc-400 leading-relaxed">
              Compile your master and job-tailored resumes in Overleaf for guaranteed typography and PDF rendering.
            </p>
          </div>

          <div className="space-y-3 font-mono text-[11px]">
            <div className="p-3 bg-zinc-900 border border-zinc-800 rounded space-y-1.5">
              <div className="text-zinc-300 font-semibold flex items-center gap-2">
                <span className="w-4 h-4 rounded-full bg-zinc-800 text-zinc-300 inline-flex items-center justify-center text-[10px]">1</span>
                <span>Copy or Export</span>
              </div>
              <p className="text-zinc-400 leading-relaxed text-[11px] font-sans">
                Click &quot;Copy for Overleaf&quot; above, or export as a raw <code className="text-zinc-300">.tex</code> file.
              </p>
            </div>

            <div className="p-3 bg-zinc-900 border border-zinc-800 rounded space-y-1.5">
              <div className="text-zinc-300 font-semibold flex items-center gap-2">
                <span className="w-4 h-4 rounded-full bg-zinc-800 text-zinc-300 inline-flex items-center justify-center text-[10px]">2</span>
                <span>Open Overleaf</span>
              </div>
              <p className="text-zinc-400 leading-relaxed text-[11px] font-sans">
                In Overleaf, click &quot;New Project&quot; then select &quot;Blank Project&quot; (or upload the exported file).
              </p>
            </div>

            <div className="p-3 bg-zinc-900 border border-zinc-800 rounded space-y-1.5">
              <div className="text-zinc-300 font-semibold flex items-center gap-2">
                <span className="w-4 h-4 rounded-full bg-zinc-800 text-zinc-300 inline-flex items-center justify-center text-[10px]">3</span>
                <span>Compile &amp; Download</span>
              </div>
              <p className="text-zinc-400 leading-relaxed text-[11px] font-sans">
                Paste the code into <code className="text-zinc-300">main.tex</code> and hit Recompile to download your ATS PDF.
              </p>
            </div>
          </div>

          <div className="pt-2">
            <a
              href="https://www.overleaf.com/project"
              target="_blank"
              rel="noopener noreferrer"
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-100 text-xs font-semibold transition-colors border border-zinc-700"
            >
              <span>Launch Overleaf Project</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
