import { useMemo, useState } from "react";
import { AlertTriangle, FileText, MapPin, Search, Sparkles } from "lucide-react";
import { api, salaryLabel, SOURCE_LABEL, STATUS_META, type JobSummary, type Stats, type Status } from "../api";
import { Button, Card, ScoreBadge, useAction } from "./ui";

const TABS: { id: string; label: string; statuses: Status[] }[] = [
  { id: "matches", label: "Matches", statuses: ["tailored", "scored"] },
  { id: "applied", label: "Applied", statuses: ["applied", "assessment", "interview", "offer"] },
  { id: "new", label: "Not scored yet", statuses: ["new"] },
  { id: "low", label: "Low match", statuses: ["skipped"] },
  { id: "all", label: "All", statuses: ["new", "scored", "tailored", "applied", "assessment", "interview", "offer", "rejected", "withdrawn", "expired", "skipped", "dismissed", "error"] },
];

// India-first quick views, driven by the deterministic enrichment fields.
const VIEWS: { id: string; label: string; filter: (j: JobSummary) => boolean }[] = [
  { id: "fresher", label: "Fresher", filter: (j) => !!j.fresher_flag },
  { id: "batch", label: "2027 batch", filter: (j) => (j.batch_years ?? []).includes(2027) },
  { id: "internship", label: "Internships", filter: (j) => j.employment_type === "internship" },
  { id: "hyderabad", label: "Hyderabad", filter: (j) => (j.location ?? "").toLowerCase().includes("hyderabad") || (j.location ?? "").toLowerCase().includes("secunderabad") },
  { id: "remote", label: "Remote India", filter: (j) => j.remote_type === "remote" },
  { id: "deadlines", label: "Deadlines ≤14d", filter: (j) => {
    if (!j.deadline) return false;
    const d = new Date(j.deadline).getTime() - Date.now();
    return d >= -86400000 && d <= 14 * 86400000;
  } },
];

// AI score where there is one; the quick estimate runs high, so it's scaled down when ranked
// alongside AI-scored jobs.
const fit = (j: JobSummary) => j.score ?? (j.prefit === null ? -1 : j.prefit * 0.75);

function ago(iso: string) {
  const d = (Date.now() - new Date(iso).getTime()) / 86400000;
  return d < 1 ? "today" : d < 2 ? "yesterday" : `${Math.floor(d)}d ago`;
}

export function JobsTable({ jobs, stats, selected, onSelect, running, onStarted }: {
  jobs: JobSummary[]; stats: Stats | null; selected: string | null; onSelect: (id: string) => void;
  running: boolean; onStarted: () => void;
}) {
  const [tab, setTab] = useState("matches");
  const [view, setView] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"match" | "newest" | "company">("match");
  const [scoreN, setScoreN] = useState(30);
  const { busy, run } = useAction();

  const current = TABS.find((t) => t.id === tab)!;
  const activeView = VIEWS.find((v) => v.id === view);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return jobs
      .filter((j) => current.statuses.includes(j.status))
      .filter((j) => !activeView || activeView.filter(j))
      .filter((j) => !q || `${j.company} ${j.title} ${j.location ?? ""}`.toLowerCase().includes(q))
      .sort((a, b) => {
        if (sort === "newest") return b.discovered_at.localeCompare(a.discovered_at);
        if (sort === "company") return a.company.localeCompare(b.company) || a.title.localeCompare(b.title);
        return (fit(b) - fit(a)) || b.discovered_at.localeCompare(a.discovered_at);
      });
  }, [jobs, current, activeView, query, sort]);

  const count = (t: (typeof TABS)[number]) => jobs.filter((j) => t.statuses.includes(j.status)).length;

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 pt-3">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 px-3 pb-2.5 text-sm font-medium transition-colors ${
              tab === t.id ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {t.label} <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{count(t)}</span>
          </button>
        ))}
        <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} title="Sort order"
          className="ml-auto mb-2 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-700">
          <option value="match">Best match first</option>
          <option value="newest">Newest first</option>
          <option value="company">Company A-Z</option>
        </select>
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5">
          <Search className="size-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by company, role, location"
            className="w-60 bg-transparent text-sm outline-none"
          />
        </div>
      </div>

      {/* India-first quick views over the current tab */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 px-4 py-2">
        <span className="mr-1 text-xs font-medium uppercase tracking-wide text-slate-400">India views</span>
        {VIEWS.map((v) => {
          const n = jobs.filter((j) => current.statuses.includes(j.status) && v.filter(j)).length;
          return (
            <button key={v.id} onClick={() => setView(view === v.id ? "" : v.id)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                view === v.id ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}>
              {v.label} <span className={view === v.id ? "text-blue-100" : "text-slate-400"}>{n}</span>
            </button>
          );
        })}
      </div>

      {/* Bulk actions relevant to the current tab */}
      {tab === "new" && (stats?.new ?? 0) > 0 && (
        <div className="flex items-center gap-2 border-b border-slate-100 bg-violet-50/50 px-4 py-2.5 text-sm">
          <Sparkles className="size-4 text-violet-500" /> Score the next
          <input type="number" min={1} max={500} value={scoreN} onChange={(e) => setScoreN(Number(e.target.value))}
            className="w-16 rounded border border-slate-300 bg-white px-1.5 py-0.5" />
          best-estimated jobs
          <Button variant="primary" disabled={running} loading={busy === "score"} className="ml-2 py-1.5"
            onClick={() => run("score", async () => { await api.score(scoreN); onStarted(); }, "Scoring started")}>
            Score
          </Button>
        </div>
      )}

      {rows.length === 0 ? (
        <div className="px-4 py-14 text-center text-sm text-slate-500">
          {jobs.length === 0 ? "No jobs yet - run a search above." : activeView ? `Nothing in this view yet.` : "Nothing here yet."}
        </div>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-16 px-4 py-2.5 font-medium">Match</th>
                <th className="px-2 py-2.5 font-medium">Role</th>
                <th className="px-2 py-2.5 font-medium">Location</th>
                <th className="px-2 py-2.5 font-medium">Facts</th>
                <th className="px-2 py-2.5 font-medium">Source</th>
                <th className="px-2 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 text-right font-medium">Found</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((j) => {
                const salary = salaryLabel(j);
                const bad = (j.fraud_flags ?? []).length > 0;
                return (
                <tr
                  key={j.id}
                  onClick={() => onSelect(j.id)}
                  className={`cursor-pointer border-t border-slate-100 transition-colors hover:bg-blue-50/60 ${
                    selected === j.id ? "bg-blue-50" : ""
                  }`}
                >
                  <td className="px-4 py-2.5"><ScoreBadge score={j.score} estimate={j.prefit} /></td>
                  <td className="px-2 py-2.5">
                    <div className="font-medium text-slate-900">{j.title}</div>
                    <div className="text-slate-500">{j.company}</div>
                  </td>
                  <td className="max-w-56 px-2 py-2.5 text-slate-600">
                    <span className="line-clamp-2 inline-flex items-start gap-1">
                      <MapPin className="mt-0.5 size-3.5 shrink-0 text-slate-400" />
                      {j.location || "—"}
                    </span>
                  </td>
                  <td className="px-2 py-2.5">
                    <div className="flex flex-wrap items-center gap-1">
                      {salary && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">{salary}</span>}
                      {j.employment_type === "internship" && <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[11px] text-violet-700">Intern</span>}
                      {j.deadline && <span className="rounded bg-red-50 px-1.5 py-0.5 text-[11px] text-red-700">⏳ {j.deadline.slice(5)}</span>}
                      {(j.batch_years ?? []).length > 0 && (
                        <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] text-blue-700">
                          {j.batch_years!.join("/")} batch
                        </span>
                      )}
                      {bad && <span title={(j.fraud_flags ?? []).join("; ")}
                        className="inline-flex items-center gap-0.5 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700">
                          <AlertTriangle className="size-3" /> verify
                        </span>}
                    </div>
                  </td>
                  <td className="px-2 py-2.5 text-slate-600">{SOURCE_LABEL[j.source] ?? j.source}</td>
                  <td className="px-2 py-2.5">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_META[j.status].cls}`}>
                      {j.has_resume && j.status !== "applied" && <FileText className="size-3" />}
                      {STATUS_META[j.status].label}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-slate-500">{ago(j.discovered_at)}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
