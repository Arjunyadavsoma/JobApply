import { useEffect, useState } from "react";
import { Brain, MessageSquareText, Plus, Sparkles, Target, Trash2 } from "lucide-react";
import { api, type Answer, type JobSummary } from "../api";
import { Button, Card, Label, useAction } from "./ui";

/** AI assistant: interview prep, follow-ups, skill gaps, quality checks + the answer knowledge base. */
export function AssistantPage() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobId, setJobId] = useState("");
  const [result, setResult] = useState<{ title: string; items: Record<string, unknown>[] } | null>(null);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [qDraft, setQDraft] = useState({ question: "", answer: "", tags: "" });
  const { busy, run } = useAction();

  const refresh = () => {
    api.jobs({ status: "scored" }).then((j) => { setJobs(j); if (!jobId && j.length) setJobId(j[0].id); }).catch(() => {});
    api.answers().then(setAnswers).catch(() => {});
  };
  useEffect(refresh, []);

  const job = jobs.find((j) => j.id === jobId);

  const call = (service: "interview_prep" | "follow_up" | "skill_gap" | "quality_check",
                title: string) => {
    if (!jobId) return;
    run(service, async () => {
      const r = (await api.aiService(service, jobId)) as Record<string, unknown>;
      setResult({ title, items: [r] });
    });
  };

  const renderValue = (v: unknown): string => {
    if (v === null || v === undefined) return "";
    if (typeof v === "string") return v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    if (Array.isArray(v)) return v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join("\n");
    return JSON.stringify(v);
  };

  const LISTS: Record<string, string> = {
    likely_questions: "Likely questions", talking_points: "Your talking points",
    questions_to_ask: "Questions to ask them", topics_to_revise: "Revise before the interview",
    missing_skills: "Missing skills", strengths: "Strengths", learning_plan: "2-4 week learning plan",
    issues: "Issues found", suggestions: "Suggestions", reasons: "Reasons",
  };

  return (
    <div className="space-y-5">
      <Card className="p-4">
        <Label hint="pick one of your matched jobs">Job</Label>
        <div className="flex flex-wrap items-center gap-2">
          <select value={jobId} onChange={(e) => setJobId(e.target.value)}
            className="min-w-72 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm">
            {jobs.length === 0 && <option value="">No matched jobs yet - run a search and score first</option>}
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>{j.company} — {j.title} (match {j.score ?? "~" + j.prefit})</option>
            ))}
          </select>
          <div className="flex flex-wrap gap-2">
            <Button icon={<MessageSquareText className="size-4" />} disabled={!jobId} loading={busy === "interview_prep"}
              onClick={() => call("interview_prep", "Interview preparation")}>Interview prep</Button>
            <Button icon={<Target className="size-4" />} disabled={!jobId} loading={busy === "skill_gap"}
              onClick={() => call("skill_gap", "Skill gap report")}>Skill gap</Button>
            <Button icon={<Sparkles className="size-4" />} disabled={!jobId} loading={busy === "quality_check"}
              onClick={() => call("quality_check", "Application quality check")}>Quality check</Button>
            <Button icon={<Brain className="size-4" />} disabled={!jobId} loading={busy === "follow_up"}
              onClick={() => call("follow_up", "Follow-up email")}>Follow-up email</Button>
          </div>
        </div>
      </Card>

      {result && (
        <Card className="p-5">
          <h3 className="mb-3 text-sm font-semibold text-slate-800">{result.title}
            {job && <span className="ml-2 font-normal text-slate-500">{job.company} — {job.title}</span>}
          </h3>
          {result.items.map((item, i) => (
            <div key={i} className="space-y-3">
              {Object.entries(item).map(([k, v]) => {
                if (v === null || v === undefined) return null;
                if (Array.isArray(v)) {
                  if (!v.length) return null;
                  return (
                    <div key={k}>
                      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                        {LISTS[k] ?? k}
                      </div>
                      <ul className="space-y-1.5">
                        {v.map((x, idx) => (
                          <li key={idx} className="flex gap-2 text-sm text-slate-700">
                            <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-blue-400" />
                            <span className="whitespace-pre-wrap">{renderValue(x)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                }
                if (typeof v === "string" && v) {
                  return (
                    <div key={k}>
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                        {k === "subject" ? "Subject" : k === "body" ? "Email" : k === "answer" ? "Answer" : k}
                      </div>
                      <p className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm text-slate-700">{v}</p>
                    </div>
                  );
                }
                if (typeof v === "number") {
                  return (
                    <div key={k} className="text-sm">
                      <span className="font-semibold text-slate-800">Score: </span>
                      <span className="text-2xl font-bold text-blue-700">{v}/100</span>
                    </div>
                  );
                }
                return null;
              })}
            </div>
          ))}
        </Card>
      )}

      <Card className="p-5">
        <div className="mb-1 flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-800">Answer knowledge base</h3>
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{answers.length} saved</span>
        </div>
        <p className="mb-4 text-sm text-slate-500">
          Verified answers the agent reuses in application forms. Saved answers are always checked
          <b> before</b> asking the AI, so your wording wins.
        </p>

        <div className="mb-4 grid gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-4 md:grid-cols-[1fr_1fr_auto]">
          <input value={qDraft.question} onChange={(e) => setQDraft({ ...qDraft, question: e.target.value })}
            placeholder="Question, e.g. Why do you want to work here?"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <input value={qDraft.answer} onChange={(e) => setQDraft({ ...qDraft, answer: e.target.value })}
            placeholder="Your verified answer"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <Button variant="primary" icon={<Plus className="size-4" />} disabled={!qDraft.question || !qDraft.answer}
            loading={busy === "saveAnswer"}
            onClick={() => run("saveAnswer", async () => {
              await api.saveAnswer(qDraft.question, qDraft.answer, qDraft.tags);
              setQDraft({ question: "", answer: "", tags: "" });
              refresh();
            }, "Answer saved")}>
            Save answer
          </Button>
        </div>

        <div className="divide-y divide-slate-100">
          {answers.map((a) => (
            <div key={a.id} className="flex items-start gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-slate-800">{a.question}</div>
                <div className="mt-0.5 whitespace-pre-wrap text-sm text-slate-600">{a.answer}</div>
                {a.tags && <div className="mt-1 flex flex-wrap gap-1">
                  {a.tags.split(",").filter(Boolean).map((t) => (
                    <span key={t} className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] text-blue-700">{t.trim()}</span>
                  ))}
                </div>}
              </div>
              <button title="Delete" className="mt-1 text-slate-400 hover:text-red-600"
                onClick={() => run("del" + a.id, async () => { await api.deleteAnswer(a.id); refresh(); })}>
                <Trash2 className="size-4" />
              </button>
            </div>
          ))}
          {answers.length === 0 && (
            <div className="py-6 text-center text-sm text-slate-400">No saved answers yet.</div>
          )}
        </div>
      </Card>
    </div>
  );
}
