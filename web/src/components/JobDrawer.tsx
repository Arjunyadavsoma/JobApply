import { useEffect, useState } from "react";
import {
  Check, CircleAlert, Download, ExternalLink, FileText, FolderOpen, History, Mail, MapPin,
  MousePointerClick, ShieldCheck, Sparkles, Undo2, WandSparkles, X,
} from "lucide-react";
import { api, ELIGIBILITY_META, salaryLabel, STATUS_META, type JobDetail, type Status, type Task } from "../api";
import { Button, ScoreBadge, useAction } from "./ui";

type Tab = "match" | "resume" | "cover" | "jd" | "activity";

export function JobDrawer({ jobId, task, version, onClose, onChanged }: {
  jobId: string; task: Task | null; version: number; onClose: () => void; onChanged: () => void;
}) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [tab, setTab] = useState<Tab>("match");
  const { busy, run } = useAction();
  const running = !!task?.running;
  const applyOpen = running && task?.args[0] === "apply-one" && task.args[1] === jobId;

  useEffect(() => {
    api.job(jobId).then(setJob).catch(() => setJob(null));
  }, [jobId, version]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const act = (key: string, fn: () => Promise<unknown>, msg?: string) =>
    run(key, async () => { await fn(); onChanged(); }, msg);

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "match", label: "Why it matches", icon: <Sparkles className="size-4" /> },
    { id: "resume", label: "Tailored resume", icon: <FileText className="size-4" /> },
    { id: "cover", label: "Cover letter", icon: <Mail className="size-4" /> },
    { id: "jd", label: "Job description", icon: <FileText className="size-4" /> },
    { id: "activity", label: "Activity", icon: <History className="size-4" /> },
  ];

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-3xl flex-col bg-white shadow-2xl">
        {!job ? (
          <div className="p-8 text-slate-500">Loading...</div>
        ) : (
          <>
            {/* Header */}
            <div className="border-b border-slate-200 px-6 py-5">
              <div className="flex items-start gap-4">
                <ScoreBadge score={job.score} estimate={job.prefit} size="lg" />
                <div className="min-w-0 flex-1">
                  <h2 className="text-xl font-semibold text-slate-900">{job.title}</h2>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-600">
                    <span className="font-medium">{job.company}</span>
                    <span className="inline-flex items-center gap-1"><MapPin className="size-3.5" />{job.location || "—"}</span>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_META[job.status].cls}`}>
                      {STATUS_META[job.status].label}
                    </span>
                  </div>
                </div>
                <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                  <X className="size-5" />
                </button>
              </div>

              {/* Actions */}
              <div className="mt-4 flex flex-wrap gap-2">
                {job.status === "new" && (
                  <Button variant="primary" icon={<Sparkles className="size-4" />} disabled={running} loading={busy === "score"}
                    onClick={() => act("score", () => api.scoreOne(job.id), "Scoring started")}>
                    Score this job
                  </Button>
                )}
                <Button variant={job.has_resume ? "secondary" : "primary"} icon={<WandSparkles className="size-4" />}
                  disabled={running} loading={busy === "tailor"}
                  title="Rewrites your resume and a cover letter for this job description (~30-60 s)"
                  onClick={() => act("tailor", () => api.tailorOne(job.id), "Tailoring started (~30-60 s)")}>
                  {job.has_resume ? "Re-tailor resume" : "Tailor resume"}
                </Button>
                {!job.has_resume && (
                  <Button variant="primary" icon={<WandSparkles className="size-4" />} disabled={running}
                    loading={busy === "tailor-apply"}
                    title="Tailors the resume, then opens the application in Chrome and fills it in"
                    onClick={() => act("tailor-apply", () => api.applyOne(job.id, true),
                      "Tailoring, then opening the application...")}>
                    Tailor &amp; auto-fill
                  </Button>
                )}
                <Button variant="primary" icon={<MousePointerClick className="size-4" />} disabled={running}
                  loading={busy === "apply"}
                  title={job.has_resume
                    ? "Opens the application in Chrome and fills it in with your tailored resume"
                    : "Opens the application in Chrome and fills it in with your original resume"}
                  onClick={() => act("apply", () => api.applyOne(job.id), "Opening the application in Chrome...")}>
                  {job.has_resume ? "Auto-fill application" : "Auto-fill (original resume)"}
                </Button>
                {job.status !== "applied" && !["assessment", "interview", "offer", "rejected", "withdrawn"].includes(job.status) && (
                  <Button icon={<Check className="size-4" />} loading={busy === "applied"}
                    onClick={() => act("applied", () => api.setStatus(job.id, "applied"), "Marked as applied")}>
                    Mark applied
                  </Button>
                )}
                {["applied", "assessment", "interview"].includes(job.status) && (
                  <select
                    value={job.status}
                    disabled={busy?.startsWith("status")}
                    onChange={(e) => act("status-" + e.target.value, () => api.setStatus(job.id, e.target.value as Status), "Status updated")}
                    className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm font-medium text-slate-700"
                    title="Move through the pipeline"
                  >
                    {(["applied", "assessment", "interview", "offer", "rejected", "withdrawn"] as Status[])
                      .filter((s) => job.status === "applied" || s !== "applied")
                      .map((s) => <option key={s} value={s}>{STATUS_META[s].label}</option>)}
                  </select>
                )}
                {["skipped", "dismissed", "error", "applied"].includes(job.status) ? (
                  <Button icon={<Undo2 className="size-4" />} loading={busy === "restore"}
                    onClick={() => act("restore", () => api.setStatus(job.id, "scored"), "Moved to matches")}>
                    Move to matches
                  </Button>
                ) : (
                  <Button variant="ghost" icon={<X className="size-4" />} loading={busy === "dismiss"}
                    onClick={() => act("dismiss", () => api.setStatus(job.id, "dismissed"), "Dismissed")}>
                    Dismiss
                  </Button>
                )}
                <a href={job.url} target="_blank" rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium text-blue-700 hover:bg-blue-50">
                  <ExternalLink className="size-4" /> View posting
                </a>
              </div>

              {applyOpen && (
                <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm">
                  <div className="font-medium text-blue-900">The application is open in Chrome.</div>
                  <div className="mt-0.5 text-blue-800">
                    Fields outlined <b className="text-red-600">red</b> need your answer, <b className="text-orange-600">orange</b> couldn't be
                    filled. Check everything, click <b>Submit</b> there yourself, then press <b>Mark applied</b>.
                  </div>
                  <div className="mt-2 flex gap-2">
                    <Button icon={<Sparkles className="size-4" />} loading={busy === "fill"}
                      title="After clicking Next / Easy Apply, or if fields loaded late"
                      onClick={() => run("fill", () => api.applyCommand("fill"), "Filling the current page...")}>
                      Fill current page
                    </Button>
                    <Button variant="ghost" icon={<X className="size-4" />}
                      onClick={() => run("close", () => api.applyCommand("close"))}>
                      Close browser
                    </Button>
                  </div>
                </div>
              )}
              {/* Fraud / trust warnings block auto-apply; always visible here */}
              {(job.fraud_flags ?? []).length > 0 && (
                <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                  <div className="flex items-center gap-1.5 font-semibold"><CircleAlert className="size-4" /> Check before applying</div>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5">
                    {(job.fraud_flags ?? []).map((f) => <li key={f}>{f}</li>)}
                  </ul>
                </div>
              )}

              {job.status === "error" && job.error && (
                <div className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-700">
                  <CircleAlert className="mt-0.5 size-4 shrink-0" /> {job.error}
                </div>
              )}
            </div>

            {/* Tabs */}
            <div className="flex gap-1 border-b border-slate-200 px-4">
              {tabs.map((t) => (
                <button key={t.id} onClick={() => setTab(t.id)}
                  className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-3 text-sm font-medium ${
                    tab === t.id ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-700"
                  }`}>
                  {t.icon}{t.label}
                </button>
              ))}
            </div>

            <div className="flex-1 overflow-auto px-6 py-5">
              {tab === "match" && (
                <div className="space-y-6">
                  {/* Deterministic eligibility */}
                  {job.eligibility && (
                    <div className="rounded-xl border border-slate-200 p-4">
                      <div className="mb-3 flex items-center gap-2">
                        <ShieldCheck className="size-4 text-slate-500" />
                        <h3 className="text-sm font-semibold text-slate-800">Eligibility check</h3>
                        <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${ELIGIBILITY_META[job.eligibility.status].cls}`}>
                          {ELIGIBILITY_META[job.eligibility.status].label}
                        </span>
                        <span className="text-xs text-slate-400">deterministic - the AI cannot override this</span>
                      </div>
                      {job.eligibility.checks.length ? (
                        <table className="w-full text-sm">
                          <tbody>
                            {job.eligibility.checks.map((c) => (
                              <tr key={c.criterion + c.required} className="border-t border-slate-100 first:border-0">
                                <td className="w-28 py-1.5 pr-2 align-top text-xs font-medium uppercase tracking-wide text-slate-500">{c.criterion}</td>
                                <td className="py-1.5 pr-2 align-top text-slate-700">needs {c.required}</td>
                                <td className="py-1.5 pr-2 align-top text-slate-500">you: {c.candidate}</td>
                                <td className="w-16 py-1.5 align-top">
                                  <span className={`text-xs font-semibold ${
                                    c.result === "pass" ? "text-emerald-600" : c.result === "fail" ? "text-red-600" : "text-amber-600"
                                  }`}>{c.result === "pass" ? "✓ pass" : c.result === "fail" ? "✗ fail" : "? unknown"}</span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ) : (
                        <p className="text-sm text-slate-500">No explicit eligibility criteria found in the posting.</p>
                      )}
                    </div>
                  )}

                  {job.match ? (
                    <div className="grid gap-6 md:grid-cols-2">
                      <div>
                        <h3 className="mb-2 font-semibold text-emerald-700">Why it fits</h3>
                        <ul className="list-disc space-y-1.5 pl-5 text-sm">{job.match.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                        {(job.match.recommendations ?? []).length > 0 && (
                          <>
                            <h3 className="mb-2 mt-4 font-semibold text-blue-700">Recommendations</h3>
                            <ul className="list-disc space-y-1.5 pl-5 text-sm">{job.match.recommendations!.map((r) => <li key={r}>{r}</li>)}</ul>
                          </>
                        )}
                      </div>
                      <div>
                        <h3 className="mb-2 font-semibold text-amber-700">Gaps</h3>
                        {(job.match.missing_requirements ?? []).length || (job.match.missing_skills ?? []).length ? (
                          <ul className="list-disc space-y-1.5 pl-5 text-sm">
                            {(job.match.missing_requirements ?? []).map((r) => <li key={r}>{r}</li>)}
                            {(job.match.missing_skills ?? []).map((r) => <li key={r}>{r}</li>)}
                          </ul>
                        ) : <p className="text-sm text-slate-500">None noted.</p>}
                        {(job.match.disqualifiers ?? []).length > 0 && (
                          <div className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">
                            <b>Disqualifiers:</b> {(job.match.disqualifiers ?? []).join("; ")}
                          </div>
                        )}
                      </div>
                      {job.match.skills_score !== undefined && (
                        <div className="md:col-span-2 grid grid-cols-2 gap-3 md:grid-cols-4">
                          {["skills_score", "education_score", "experience_score", "location_score"].map((k) => {
                            const v = (job.match as unknown as Record<string, number | undefined>)[k];
                            if (v === undefined) return null;
                            const labels: Record<string, string> = { skills_score: "Skills", education_score: "Education", experience_score: "Experience", location_score: "Location" };
                            return (
                              <div key={k} className="rounded-lg border border-slate-200 p-3 text-center">
                                <div className="text-2xl font-bold text-slate-800">{v}</div>
                                <div className="text-xs text-slate-500">{labels[k]}</div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  ) : <p className="text-sm text-slate-500">Not scored yet - press <b>Score this job</b>.</p>}

                  {/* Enriched facts */}
                  {(job.salary_min || job.stipend || job.batch_years?.length || job.deadline || job.min_cgpa) && (
                    <div className="flex flex-wrap gap-2 text-sm">
                      {salaryLabel(job) && <span className="rounded bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700">CTC {salaryLabel(job)}</span>}
                      {job.deadline && <span className="rounded bg-red-50 px-2.5 py-1 font-medium text-red-700">Deadline {job.deadline}</span>}
                      {job.batch_years?.length ? <span className="rounded bg-blue-50 px-2.5 py-1 font-medium text-blue-700">Batches {job.batch_years.join(", ")}</span> : null}
                      {job.min_cgpa && <span className="rounded bg-slate-100 px-2.5 py-1 font-medium text-slate-600">Min CGPA {job.min_cgpa}</span>}
                      {job.experience_min !== null && job.experience_min !== undefined && (
                        <span className="rounded bg-slate-100 px-2.5 py-1 font-medium text-slate-600">
                          Exp {job.experience_min}{job.experience_max ? `-${job.experience_max}` : "+"} yrs
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {tab === "resume" && (
                job.has_resume ? (
                  <div className="flex h-full flex-col gap-4">
                    <div className="flex flex-wrap gap-2">
                      <a href={`/api/jobs/${job.id}/resume.pdf`} target="_blank" rel="noreferrer"
                        className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-medium hover:bg-slate-50">
                        <ExternalLink className="size-4" /> Open in new tab
                      </a>
                      <a href={`/api/jobs/${job.id}/resume.pdf`} download={job.resume_file ?? undefined}
                        className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-medium hover:bg-slate-50">
                        <Download className="size-4" /> Download
                      </a>
                      <Button icon={<FolderOpen className="size-4" />} onClick={() => run("folder", () => api.openFolder(job.id))}>
                        Open folder
                      </Button>
                    </div>
                    {job.changes.filter((c) => c.startsWith("Added skills")).map((c) => (
                      <div key={c} className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                        <CircleAlert className="mt-0.5 size-4 shrink-0" />
                        <span><b>Added skills</b>{c.slice("Added skills".length)}</span>
                      </div>
                    ))}
                    {job.changes.length > 0 && (
                      <details open className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
                        <summary className="cursor-pointer font-medium">What was changed from your base resume</summary>
                        <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-700">
                          {job.changes.filter((c) => !c.startsWith("Added skills")).map((c) => <li key={c}>{c}</li>)}
                        </ul>
                      </details>
                    )}
                    <iframe title="Tailored resume" src={`/api/jobs/${job.id}/resume.pdf#view=FitH`}
                      className="min-h-[900px] w-full flex-1 rounded-lg border border-slate-200" />
                  </div>
                ) : (
                  <div className="space-y-3">
                    <p className="text-sm text-slate-500">
                      Not tailored yet - press <b>Tailor resume</b> to rewrite your resume for this job. Until then,
                      <b> Auto-fill</b> uploads your original resume:
                    </p>
                    <iframe title="Original resume" src="/api/resume/original.pdf#view=FitH"
                      className="min-h-[900px] w-full rounded-lg border border-slate-200" />
                  </div>
                )
              )}

              {tab === "cover" && (
                job.cover_letter ? (
                  <div className="space-y-3">
                    <a href={`/api/jobs/${job.id}/cover.pdf`} target="_blank" rel="noreferrer"
                      className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-medium hover:bg-slate-50">
                      <ExternalLink className="size-4" /> Open PDF
                    </a>
                    <div className="whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-5 text-sm leading-relaxed">
                      {job.cover_letter}
                    </div>
                  </div>
                ) : <p className="text-sm text-slate-500">Written when you press <b>Tailor resume</b>.</p>
              )}

              {tab === "jd" && (
                <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{job.description}</div>
              )}

              {tab === "activity" && (
                <div className="space-y-4">
                  <NotesEditor job={job} onSaved={onChanged} />
                  <div>
                    <h3 className="mb-3 text-sm font-semibold text-slate-800">Timeline</h3>
                    <div className="space-y-0">
                      {job.events.map((e) => (
                        <div key={e.id} className="flex gap-3">
                          <div className="flex flex-col items-center">
                            <span className={`mt-1.5 size-2.5 shrink-0 rounded-full ${
                              e.kind === "status" ? "bg-blue-500" : e.kind === "error" ? "bg-red-500" :
                              e.kind === "apply" ? "bg-emerald-500" : "bg-slate-300"
                            }`} />
                            <span className="w-px flex-1 bg-slate-200" />
                          </div>
                          <div className="pb-5">
                            <div className="text-sm text-slate-800">{e.text || e.kind}</div>
                            <div className="text-xs text-slate-400">{e.created_at.replace("T", " ").slice(0, 16)}</div>
                          </div>
                        </div>
                      ))}
                      {job.events.length === 0 && <p className="text-sm text-slate-500">No activity recorded yet.</p>}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function NotesEditor({ job, onSaved }: { job: JobDetail; onSaved: () => void }) {
  const [notes, setNotes] = useState(job.notes ?? "");
  const { busy, run } = useAction();
  useEffect(() => setNotes(job.notes ?? ""), [job.id]);
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-slate-800">Notes</h3>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3}
        placeholder="Recruiter contact, referral, interview date, follow-up reminders…"
        className="w-full rounded-lg border border-slate-300 p-3 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
      <div className="mt-2 flex justify-end">
        <Button variant="primary" disabled={notes === (job.notes ?? "")} loading={busy === "notes"}
          onClick={() => run("notes", async () => { await api.setNotes(job.id, notes); onSaved(); }, "Notes saved")}>
          Save notes
        </Button>
      </div>
    </div>
  );
}
