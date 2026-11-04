import { useCallback, useEffect, useState } from "react";
import { Building2, Gauge, GraduationCap, RefreshCw, Sparkles } from "lucide-react";
import { api, type CompanyIntel, type InterviewPrepPlan, type PrepQuestionUI, type ReadinessScore } from "../api";
import { useToast } from "./ui";

const DIMENSION_LABELS: Record<string, string> = {
  candidate_eligibility: "Eligibility",
  resume_relevance: "Resume relevance",
  skill_coverage: "Skill coverage",
  resume_formatting: "Resume formatting",
  keyword_alignment: "Keyword alignment",
  screening_answers: "Screening answers",
  required_fields: "Required fields",
  factual_consistency: "Factual consistency",
  url_validity: "Apply URL",
};

/** Phase 18: readiness score + company research + interview preparation for one job. */
export function PrepPanel({ jobId }: { jobId: string }) {
  const toast = useToast();
  const [readiness, setReadiness] = useState<ReadinessScore | null>(null);
  const [intel, setIntel] = useState<CompanyIntel | null>(null);
  const [plan, setPlan] = useState<InterviewPrepPlan | null>(null);
  const [progress, setProgress] = useState<{ total_questions: number; per_category: Record<string, number>; coverage: number } | null>(null);
  const [category, setCategory] = useState("");
  const [current, setCurrent] = useState<PrepQuestionUI | null>(null);
  const [revealed, setRevealed] = useState("");
  const [busy, setBusy] = useState("");

  const loadReadiness = useCallback(async () => {
    try { setReadiness(await api.readiness(jobId)); } catch { /* offline */ }
  }, [jobId]);

  const loadIntel = useCallback(async () => {
    try {
      const out = await api.companyIntel(jobId);
      setIntel(out.intel);
    } catch { /* LLM offline: show nothing rather than fake research */ }
  }, [jobId]);

  const loadPlan = useCallback(async () => {
    try {
      const out = await api.interviewPrep(jobId);
      setPlan(out.plan);
      setProgress(out.progress);
    } catch { /* offline */ }
  }, [jobId]);

  useEffect(() => { loadReadiness(); loadIntel(); loadPlan(); }, [loadReadiness, loadIntel, loadPlan]);

  const generate = async () => {
    setBusy("prep");
    try {
      toast("Running the 13-step preparation workflow…", "ok");
      await api.prepare(jobId);
      await Promise.all([loadReadiness(), loadIntel(), loadPlan()]);
      toast("Preparation finished", "ok");
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusy("");
    }
  };

  const nextQuestion = async (cat = category) => {
    setBusy("mock");
    try {
      const out = await api.mockQuestion(jobId, cat);
      setCurrent(out.question);
      setRevealed("");
    } catch {
      toast("Generate a prep plan first (Prepare application)", "error");
    } finally {
      setBusy("");
    }
  };

  const answer = async (answered: boolean) => {
    if (!current) return;
    setBusy("answer");
    try {
      const out = await api.mockAnswer(jobId, current.id, answered);
      setRevealed(out.model_answer);
      const p = await api.interviewPrep(jobId).then((r) => r.progress).catch(() => null);
      if (p) setProgress(p);
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusy("");
    }
  };

  const cats = plan ? Array.from(new Set(plan.questions.map((q) => q.category))) : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <button disabled={busy !== ""} onClick={generate}
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40">
          <Sparkles className="mr-1 inline size-4" />
          {busy === "prep" ? "Preparing…" : "Run full preparation workflow"}
        </button>
        <button onClick={() => { loadReadiness(); loadIntel(); loadPlan(); }}
          className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
          <RefreshCw className="size-3.5" /> Refresh
        </button>
      </div>

      {/* Readiness score */}
      {readiness && (
        <div className="rounded-xl border border-slate-200 p-4">
          <div className="mb-3 flex items-center gap-2">
            <Gauge className="size-4 text-slate-500" />
            <h3 className="text-sm font-semibold text-slate-800">Application readiness</h3>
            <span className={`ml-auto text-lg font-bold ${
              readiness.overall >= 85 ? "text-emerald-600" : readiness.overall >= 70 ? "text-blue-600" : "text-amber-600"}`}>
              {readiness.overall}/100
            </span>
          </div>
          <div className="grid gap-1.5 sm:grid-cols-3">
            {Object.entries(readiness.dimensions).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2 text-xs">
                <div className="h-1.5 flex-1 overflow-hidden rounded bg-slate-100">
                  <div className={`h-full ${v >= 85 ? "bg-emerald-500" : v >= 60 ? "bg-blue-500" : v >= 40 ? "bg-amber-500" : "bg-red-500"}`}
                    style={{ width: `${v}%` }} />
                </div>
                <span className="w-32 shrink-0 text-slate-600">{DIMENSION_LABELS[k] ?? k}</span>
                <span className="w-8 text-right font-medium text-slate-700">{v}</span>
              </div>
            ))}
          </div>
          {readiness.blocking_issues.length > 0 && (
            <div className="mt-3 rounded-lg bg-red-50 p-2.5 text-xs text-red-700">
              <b>Blocking:</b> {readiness.blocking_issues.join(" · ")}
            </div>
          )}
          {readiness.warnings.length > 0 && (
            <div className="mt-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-700">
              <b>Warnings:</b> {readiness.warnings.join(" · ")}
            </div>
          )}
          {readiness.improvements.length > 0 && (
            <div className="mt-2 rounded-lg bg-blue-50 p-2.5 text-xs text-blue-700">
              <b>Improvements:</b> {readiness.improvements.join(" · ")}
            </div>
          )}
          <p className="mt-2 text-xs text-slate-400">
            {readiness.submission_ready
              ? "Ready to submit (still requires your approval)."
              : "Not ready — fix the blocking issues first."}
          </p>
        </div>
      )}

      {/* Company intelligence */}
      {intel && (
        <div className="rounded-xl border border-slate-200 p-4">
          <div className="mb-2 flex items-center gap-2">
            <Building2 className="size-4 text-slate-500" />
            <h3 className="text-sm font-semibold text-slate-800">Company research</h3>
            <span className="text-xs text-slate-400">verified facts separated from AI interpretation</span>
          </div>
          {intel.overview && <p className="text-sm text-slate-700">{intel.overview}</p>}
          {intel.tech_stack.length > 0 && (
            <p className="mt-2 text-xs text-slate-600"><b>Documented stack:</b> {intel.tech_stack.join(", ")}</p>
          )}
          {intel.facts_verified.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-emerald-700">
              {intel.facts_verified.slice(0, 8).map((f, i) => <li key={i}>✓ {f}</li>)}
            </ul>
          )}
          {intel.ai_interpretations.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-slate-500 italic">
              {intel.ai_interpretations.slice(0, 5).map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* Interview preparation */}
      <div className="rounded-xl border border-slate-200 p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <GraduationCap className="size-4 text-slate-500" />
          <h3 className="text-sm font-semibold text-slate-800">Interview preparation</h3>
          {progress && (
            <span className="text-xs text-slate-500">
              {progress.total_questions} questions · {progress.coverage}% categories practised
            </span>
          )}
          <select value={category} onChange={(e) => setCategory(e.target.value)}
            className="ml-auto rounded-lg border border-slate-200 px-2 py-1 text-xs">
            <option value="">All categories</option>
            {cats.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button disabled={busy !== "" || !plan} onClick={() => nextQuestion()}
            className="rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40">
            {busy === "mock" ? "…" : "Mock question"}
          </button>
        </div>

        {current && (
          <div className="rounded-lg border border-blue-200 bg-blue-50/50 p-3">
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="rounded bg-blue-100 px-1.5 py-0.5 font-medium text-blue-700">{current.category}</span>
              <span>{current.difficulty}</span>
            </div>
            <p className="mt-2 font-medium text-slate-800">{current.question}</p>
            {revealed ? (
              <div className="mt-3 space-y-2 text-sm">
                {current.explanation && <p className="text-xs text-slate-500">{current.explanation}</p>}
                <p className="whitespace-pre-wrap rounded-lg bg-white p-2.5 text-slate-700">{revealed}</p>
              </div>
            ) : (
              <div className="mt-3 flex gap-2">
                <button disabled={busy !== ""} onClick={() => answer(true)}
                  className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-40">
                  I answered it — show model answer
                </button>
                <button disabled={busy !== ""} onClick={() => answer(false)}
                  className="rounded-lg border px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40">
                  Skip
                </button>
              </div>
            )}
          </div>
        )}

        {plan && (
          <details className="mt-3">
            <summary className="cursor-pointer text-xs font-medium text-blue-700">
              Show all {plan.questions.length} questions
            </summary>
            <ul className="mt-2 space-y-2">
              {plan.questions.map((q) => (
                <li key={q.id} className="rounded-lg border border-slate-100 p-2.5">
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <span className="rounded bg-slate-100 px-1.5 py-0.5">{q.category}</span>
                    <span>{q.difficulty}</span>
                  </div>
                  <p className="mt-1 text-sm font-medium text-slate-800">{q.question}</p>
                  {q.explanation && <p className="mt-0.5 text-xs text-slate-500">{q.explanation}</p>}
                  {q.model_answer && (
                    <p className="mt-1 whitespace-pre-wrap text-xs text-slate-600">{q.model_answer}</p>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}
        {!plan && (
          <p className="text-sm text-slate-400">
            No prep plan yet — run the preparation workflow above (needs the LLM backend configured).
          </p>
        )}
      </div>
    </div>
  );
}
