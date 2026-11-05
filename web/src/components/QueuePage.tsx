import { useCallback, useEffect, useState } from "react";
import { Gauge, ListChecks, Play, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { api, ELIGIBILITY_META, type BatchControls, type QueueLimits, type QueueRow, type ReadinessScore } from "../api";
import { Card, useToast } from "./ui";
const QUEUE_META: Record<string, { label: string; cls: string }> = {
  new: { label: "New", cls: "bg-slate-100 text-slate-600" },
  analyzing: { label: "Analyzing", cls: "bg-blue-100 text-blue-700" },
  eligible: { label: "Eligible", cls: "bg-emerald-100 text-emerald-700" },
  shortlisted: { label: "Shortlisted", cls: "bg-cyan-100 text-cyan-700" },
  resume_prep: { label: "Resume prep", cls: "bg-indigo-100 text-indigo-700" },
  ready: { label: "Ready to apply", cls: "bg-indigo-100 text-indigo-700" },
  awaiting_approval: { label: "Awaiting approval", cls: "bg-amber-100 text-amber-700" },
  applying: { label: "Applying", cls: "bg-blue-100 text-blue-700" },
  submitted: { label: "Submitted", cls: "bg-emerald-100 text-emerald-800" },
  failed: { label: "Failed", cls: "bg-red-100 text-red-700" },
  needs_manual: { label: "Needs manual", cls: "bg-orange-100 text-orange-700" },
};

const VERIFY_META: Record<string, { label: string; cls: string }> = {
  verified_open: { label: "Verified open", cls: "bg-emerald-100 text-emerald-700" },
  probably_open: { label: "Probably open", cls: "bg-lime-100 text-lime-700" },
  needs_manual: { label: "Unverified page", cls: "bg-amber-100 text-amber-700" },
  expired: { label: "Expired", cls: "bg-red-100 text-red-700" },
  suspicious: { label: "Suspicious", cls: "bg-red-100 text-red-800" },
  unavailable: { label: "Unavailable", cls: "bg-slate-100 text-slate-500" },
  unverified: { label: "Not checked yet", cls: "bg-slate-100 text-slate-500" },
};

function Chip({ meta }: { meta: { label: string; cls: string } }) {
  return <span className={`inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ${meta.cls}`}>{meta.label}</span>;
}

function readinessCls(score: number): string {
  if (score >= 85) return "text-emerald-600";
  if (score >= 70) return "text-blue-600";
  if (score >= 50) return "text-amber-600";
  return "text-red-600";
}

export function QueuePage({ onStarted }: { onStarted: () => void }) {
  const toast = useToast();
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [limits, setLimits] = useState<QueueLimits | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [readiness, setReadiness] = useState<Record<string, ReadinessScore>>({});
  const [excluded, setExcluded] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const q = await api.queue();
      setRows(q.rows);
      setLimits(q.limits);
    } catch {
      /* offline banner handled globally */
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const act = async (jobId: string, action: "analyze" | "shortlist" | "prepare" | "approve") => {
    setBusy(`${jobId}:${action}`);
    try {
      const out = await api.queueAction(jobId, action);
      if (out.error) toast(`Blocked: ${out.error}`, "error");
      else toast(`Queue: ${out.queue_state ?? action}`, "ok");
      await refresh();
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusy(null);
    }
  };

  const applyMode = async (jobId: string, mode: "assisted" | "authorized") => {
    setBusy(`${jobId}:apply`);
    try {
      const out = await api.queueApply(jobId, mode);
      if (!out.ok) toast(`Blocked: ${out.error}`, "error");
      else {
        toast(mode === "authorized" ? "Authorized submission started" : "Assisted session opening", "ok");
        onStarted();
      }
      await refresh();
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusy(null);
    }
  };

  const checkReadiness = async (jobId: string) => {
    setBusy(`${jobId}:readiness`);
    try {
      const r = await api.readiness(jobId);
      setReadiness((prev) => ({ ...prev, [jobId]: r }));
      const blockers = r.blocking_issues.length;
      toast(`Readiness ${r.overall}/100${blockers ? ` — ${blockers} blocking issue(s)` : ""}`,
        r.submission_ready ? "ok" : "error");
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusy(null);
    }
  };

  const toggle = (jobId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  const batchPrepare = async () => {
    const ids = Array.from(selected);
    if (!ids.length) return;
    setBatchBusy(true);
    try {
      const controls: BatchControls = {};
      const ex = excluded.split(",").map((s) => s.trim()).filter(Boolean);
      if (ex.length) controls.excluded_companies = ex;
      const out = await api.batchPrepare(ids, controls);
      toast(`Batch: ${out.prepared} prepared, ${out.skipped} skipped`, out.prepared ? "ok" : "error");
      const skipped = out.results.filter((r) => r.skipped);
      if (skipped.length) {
        toast(`Skipped: ${skipped.map((s) => `${s.company ?? s.job_id} (${s.skipped})`).join("; ").slice(0, 220)}`, "error");
      }
      setSelected(new Set());
      await refresh();
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBatchBusy(false);
    }
  };

  const visible = filter ? rows.filter((r) => r.queue_state === filter) : rows;
  const states = Array.from(new Set(rows.map((r) => r.queue_state)));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
          <ListChecks className="size-5 text-blue-600" /> Application Queue
        </h2>
        {limits && (
          <span className="text-sm text-slate-500">
            {limits.daily_used}/{limits.max_per_day} today · min match {limits.min_match_score} ·
            min ₹{limits.min_salary_lpa} LPA · {limits.max_per_company}/company ·
            authorized submission {limits.authorized_submission ? "ON" : "OFF"}
          </span>
        )}
        <button onClick={refresh} className="ml-auto inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
          <RefreshCw className="size-3.5" /> Refresh
        </button>
      </div>

      {/* Phase 18: controlled batch preparation (preparation only - submission stays per-job) */}
      {selected.size > 0 && (
        <Card className="flex flex-wrap items-center gap-3 border-blue-200 bg-blue-50/60 p-3">
          <span className="text-sm font-medium text-blue-800">{selected.size} selected</span>
          <input
            value={excluded}
            onChange={(e) => setExcluded(e.target.value)}
            placeholder="excluded companies (comma separated, optional)"
            className="min-w-56 flex-1 rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-sm"
          />
          <button disabled={batchBusy} onClick={batchPrepare}
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40">
            <Sparkles className="mr-1 inline size-3.5" />
            {batchBusy ? "Preparing…" : "Prepare selected (batch)"}
          </button>
          <span className="text-xs text-slate-500">
            Prepares resumes + answers with quality gates. Each submission still needs its own approval.
          </span>
        </Card>
      )}

      <div className="flex flex-wrap gap-1.5">
        <button onClick={() => setFilter("")}
          className={`rounded-lg px-2.5 py-1 text-sm ${filter === "" ? "bg-blue-50 font-medium text-blue-700" : "text-slate-600 hover:bg-slate-100"}`}>
          All ({rows.length})
        </button>
        {states.map((s) => (
          <button key={s} onClick={() => setFilter(s)}
            className={`rounded-lg px-2.5 py-1 text-sm ${filter === s ? "bg-blue-50 font-medium text-blue-700" : "text-slate-600 hover:bg-slate-100"}`}>
            {QUEUE_META[s]?.label ?? s} ({rows.filter((r) => r.queue_state === s).length})
          </button>
        ))}
      </div>

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="w-8 px-2 py-2.5"></th>
              <th className="px-3 py-2.5">Company / Role</th>
              <th className="px-3 py-2.5">Salary</th>
              <th className="px-3 py-2.5">Location</th>
              <th className="px-3 py-2.5">Eligibility</th>
              <th className="px-3 py-2.5">Match</th>
              <th className="px-3 py-2.5">Quality</th>
              <th className="px-3 py-2.5">Readiness</th>
              <th className="px-3 py-2.5">Verification</th>
              <th className="px-3 py-2.5">Deadline</th>
              <th className="px-3 py-2.5">Resume</th>
              <th className="px-3 py-2.5">Queue state</th>
              <th className="px-3 py-2.5">Actions</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const rd = readiness[r.job_id];
              return (
              <tr key={r.job_id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
                <td className="px-2 py-2.5">
                  <input type="checkbox" checked={selected.has(r.job_id)} onChange={() => toggle(r.job_id)}
                    className="size-3.5 accent-blue-600" title="Select for batch preparation" />
                </td>
                <td className="px-3 py-2.5">
                  <div className="font-medium text-slate-800">{r.company}</div>
                  <div className="text-xs text-slate-500">{r.title}</div>
                </td>
                <td className="px-3 py-2.5 text-slate-600">
                  {r.stipend ? `₹${r.stipend.toLocaleString("en-IN")}/mo` : r.salary || "—"}
                </td>
                <td className="px-3 py-2.5 text-slate-600">
                  {r.remote_type === "remote" ? "Remote" : r.location || "—"}
                </td>
                <td className="px-3 py-2.5">
                  <Chip meta={ELIGIBILITY_META[r.eligibility] ?? { label: r.eligibility, cls: "bg-slate-100 text-slate-500" }} />
                </td>
                <td className="px-3 py-2.5 font-semibold text-slate-700">{r.match_score ?? "—"}</td>
                <td className="px-3 py-2.5 text-slate-600">{r.quality_score ?? "—"}</td>
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-1.5">
                    {rd && <span className={`font-semibold ${readinessCls(rd.overall)}`}>{rd.overall}</span>}
                    <button disabled={busy !== null} onClick={() => checkReadiness(r.job_id)}
                      className="rounded border border-slate-200 px-1.5 py-0.5 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                      title="Application readiness score (9 dimensions)">
                      <Gauge className="inline size-3" />
                    </button>
                  </div>
                  {rd && rd.blocking_issues.length > 0 && (
                    <div className="mt-0.5 max-w-44 text-[11px] text-red-600" title={rd.blocking_issues.join("\n")}>
                      {rd.blocking_issues.length} blocking issue{rd.blocking_issues.length > 1 ? "s" : ""}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2.5">
                  <Chip meta={VERIFY_META[r.verification_state] ?? { label: r.verification_state, cls: "bg-slate-100 text-slate-500" }} />
                </td>
                <td className={`px-3 py-2.5 ${r.deadline_urgency >= 3 ? "font-semibold text-red-600" : r.deadline_urgency === 2 ? "text-amber-600" : "text-slate-500"}`}>
                  {r.deadline || "—"}
                </td>
                <td className="px-3 py-2.5">{r.resume_ready ? "✅" : "—"}</td>
                <td className="px-3 py-2.5">
                  <Chip meta={QUEUE_META[r.queue_state] ?? { label: r.queue_state, cls: "bg-slate-100 text-slate-500" }} />
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex flex-wrap gap-1">
                    <button disabled={busy !== null} onClick={() => act(r.job_id, "analyze")}
                      className="rounded border border-slate-200 px-1.5 py-0.5 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                      title="Verify + score + eligibility">Analyze</button>
                    <button disabled={busy !== null || !r.apply_allowed} onClick={() => act(r.job_id, "shortlist")}
                      className="rounded border border-slate-200 px-1.5 py-0.5 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                      title={r.apply_allowed ? "Add to shortlist" : r.apply_block_reason}>Shortlist</button>
                    <button disabled={busy !== null} onClick={() => act(r.job_id, "prepare")}
                      className="rounded border border-slate-200 px-1.5 py-0.5 text-xs text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
                      title="Tailor resume + cover letter"><Sparkles className="inline size-3" /> Prepare</button>
                    <button disabled={busy !== null} onClick={() => act(r.job_id, "approve")}
                      className="rounded border border-slate-200 px-1.5 py-0.5 text-xs text-amber-600 hover:bg-amber-50 disabled:opacity-40"
                      title="Approve for authorized submission"><ShieldCheck className="inline size-3" /> Approve</button>
                    <button disabled={busy !== null || !r.apply_allowed} onClick={() => applyMode(r.job_id, "assisted")}
                      className="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40"
                      title={r.apply_allowed ? "Open assisted session (you submit)" : r.apply_block_reason}>
                      <Play className="inline size-3" /> Assisted
                    </button>
                    <button disabled={busy !== null || !r.apply_allowed || !limits?.authorized_submission}
                      onClick={() => applyMode(r.job_id, "authorized")}
                      className="rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-40"
                      title={limits?.authorized_submission ? "Authorized submission (agent submits after gates)" : "authorized_submission is OFF in config"}>
                      Auto-submit
                    </button>
                  </div>
                </td>
              </tr>
              );
            })}
            {visible.length === 0 && (
              <tr><td colSpan={13} className="px-3 py-10 text-center text-sm text-slate-400">
                Queue is empty. Run a search, then use "Analyze" on jobs from the Jobs page drawer
                (or queue action buttons here once jobs are tracked).
              </td></tr>
            )}
          </tbody>
        </table>
      </Card>
      <p className="text-xs text-slate-400">
        Gates block low-quality applications automatically: daily/company caps, match + quality +
        eligibility floors, fraud flags, duplicate submissions and verification state. Batch prepare
        only PREPARES applications; every submission needs its own approval. Auto-submit requires
        both per-job approval and <code>automation.authorized_submission: true</code> in config.yaml —
        and it still stops on CAPTCHAs, logins, legal declarations or unanswered sensitive questions.
      </p>
    </div>
  );
}
