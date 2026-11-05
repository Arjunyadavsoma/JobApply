import { useEffect, useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import { api, STATUS_META, type JobSummary, type Status } from "../api";
import { Button, Card, ScoreBadge, useAction } from "./ui";

/** Application CRM: the pipeline after you apply. */
const PIPELINE: { id: Status; label: string }[] = [
  { id: "tailored", label: "Ready to apply" },
  { id: "applied", label: "Submitted" },
  { id: "assessment", label: "Assessment" },
  { id: "interview", label: "Interview" },
  { id: "offer", label: "Offer" },
  { id: "rejected", label: "Rejected" },
  { id: "withdrawn", label: "Withdrawn" },
];

const NEXT: Partial<Record<Status, Status[]>> = {
  tailored: ["applied", "dismissed"],
  applied: ["assessment", "interview", "rejected", "withdrawn"],
  assessment: ["interview", "rejected", "withdrawn"],
  interview: ["offer", "rejected", "withdrawn"],
};

export function ApplicationsPage({ onChanged }: { onChanged: () => void }) {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const { busy, run } = useAction();

  const load = () => api.jobs({ limit: 500 }).then(setJobs).catch(() => {});
  useEffect(() => { load(); }, []);

  const byStatus = useMemo(() => {
    const m = new Map<Status, JobSummary[]>();
    for (const j of jobs) {
      const arr = m.get(j.status) ?? [];
      arr.push(j);
      m.set(j.status, arr);
    }
    return m;
  }, [jobs]);

  const move = (id: string, to: Status) =>
    run("move" + id + to, async () => {
      await api.setStatus(id, to);
      load();
      onChanged();
    }, `Marked ${STATUS_META[to].label.toLowerCase()}`);

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        Track every application through your pipeline. Statuses change only when <b>you</b> set them
        (or from real evidence you add); nothing is auto-assumed.
      </p>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
        {PIPELINE.map(({ id, label }) => {
          const items = byStatus.get(id) ?? [];
          return (
            <Card key={id} className="flex max-h-[640px] flex-col overflow-hidden">
              <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2.5">
                <span className="text-sm font-semibold text-slate-800">{label}</span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{items.length}</span>
              </div>
              <div className="flex-1 space-y-2 overflow-auto p-2.5">
                {items.map((j) => (
                  <div key={j.id} className="rounded-lg border border-slate-200 p-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-slate-900">{j.title}</div>
                        <div className="truncate text-xs text-slate-500">{j.company}</div>
                      </div>
                      <ScoreBadge score={j.score} />
                    </div>
                    <div className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-400">
                      {j.applied_at ? `applied ${j.applied_at.slice(0, 10)}` : j.remote_type || ""}
                      <a href={j.url} target="_blank" rel="noreferrer" title="Open posting"
                        className="ml-auto text-slate-400 hover:text-blue-600">
                        <ExternalLink className="size-3.5" />
                      </a>
                    </div>
                    {(NEXT[j.status] ?? []).length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {(NEXT[j.status] ?? []).map((to) => (
                          <button key={to} disabled={busy === "move" + j.id + to}
                            onClick={() => move(j.id, to)}
                            className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-600
                              hover:border-blue-400 hover:text-blue-700 disabled:opacity-50">
                            → {STATUS_META[to].label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {items.length === 0 && (
                  <div className="py-6 text-center text-xs text-slate-300">Empty</div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
      <Card className="p-4 text-sm text-slate-600">
        <b>Tip:</b> after submitting an application (the dashboard's apply session leaves the final
        Submit click to you), mark the job <i>Submitted</i> from the job details page or here - the
        duplicate-application guard then blocks a second apply.
      </Card>
    </div>
  );
}
