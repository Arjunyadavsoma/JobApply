import { useEffect, useState } from "react";
import { api, type Analytics } from "../api";
import { Card } from "./ui";

function Metric({ label, value, tone = "text-slate-900" }: { label: string; value: number; tone?: string }) {
  return (
    <Card className="px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${tone}`}>{value}</div>
    </Card>
  );
}

function Bars({ data, labelSingular }: { data: { label: string; value: number }[]; labelSingular: string }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div className="space-y-1.5">
      {data.map((d) => (
        <div key={d.label} className="flex items-center gap-3">
          <span className="w-24 shrink-0 truncate text-right text-xs text-slate-500">{d.label}</span>
          <div className="h-5 flex-1 rounded bg-slate-100">
            <div className="flex h-5 items-center justify-end rounded bg-blue-500/80 pr-1.5 text-[10px] font-medium text-white"
              style={{ width: `${Math.max(d.value > 0 ? 8 : 0, (d.value / max) * 100)}%` }}>
              {d.value > 0 ? d.value : ""}
            </div>
          </div>
        </div>
      ))}
      {data.every((d) => d.value === 0) && (
        <div className="py-2 text-center text-xs text-slate-400">No {labelSingular} data yet.</div>
      )}
    </div>
  );
}

export function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);
  useEffect(() => {
    api.analytics().then(setData).catch(() => {});
  }, []);

  if (!data) return <div className="py-20 text-center text-sm text-slate-500">Loading analytics…</div>;
  const m = data.metrics;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
        <Metric label="Discovered" value={m.jobs_discovered ?? 0} />
        <Metric label="Needs scoring" value={m.needs_scoring ?? 0} tone="text-slate-600" />
        <Metric label="Good matches" value={m.good_matches ?? 0} tone="text-blue-700" />
        <Metric label="Ready to apply" value={m.resumes_ready ?? 0} tone="text-indigo-700" />
        <Metric label="Applied" value={m.applied ?? 0} tone="text-emerald-700" />
        <Metric label="In progress" value={m.in_progress ?? 0} tone="text-violet-700" />
        <Metric label="Offers" value={m.offers ?? 0} tone="text-green-700" />
        <Metric label="Deadlines ≤14d" value={m.upcoming_deadlines ?? 0} tone="text-red-600" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-800">Applications per week</h3>
          <Bars data={data.applications_over_time.map((w) => ({ label: w.week.slice(5), value: w.applications }))}
            labelSingular="application" />
        </Card>
        <Card className="p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-800">Match score distribution</h3>
          <Bars data={data.match_distribution.map((b) => ({ label: b.bucket, value: b.jobs }))} labelSingular="scored" />
        </Card>
        <Card className="p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-800">Eligibility (deterministic checks)</h3>
          <Bars data={data.eligibility.map((e) => ({
            label: e.status.replace("_", " "), value: e.jobs,
          }))} labelSingular="eligibility" />
        </Card>
        <Card className="p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-800">Most common missing skills</h3>
          <Bars data={data.missing_skills.slice(0, 8).map((s) => ({ label: s.skill, value: s.jobs }))}
            labelSingular="gap" />
        </Card>
      </div>

      <Card className="overflow-hidden">
        <h3 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-800">
          Source quality (your own data)
        </h3>
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2 font-medium">Source</th>
              <th className="px-2 py-2 font-medium">Jobs</th>
              <th className="px-2 py-2 font-medium">Avg match</th>
              <th className="px-2 py-2 font-medium">Applied</th>
              <th className="px-2 py-2 font-medium">In progress</th>
              <th className="px-4 py-2 font-medium">Response rate</th>
            </tr>
          </thead>
          <tbody>
            {data.source_quality.map((s) => (
              <tr key={s.source} className="border-t border-slate-50">
                <td className="px-4 py-2.5 font-medium text-slate-800">{s.source}</td>
                <td className="px-2 py-2.5 text-slate-600">{s.jobs}</td>
                <td className="px-2 py-2.5 text-slate-600">{s.avg_score ?? "—"}</td>
                <td className="px-2 py-2.5 text-slate-600">{s.applied}</td>
                <td className="px-2 py-2.5 text-slate-600">{s.in_progress}</td>
                <td className="px-4 py-2.5 text-slate-600">{s.applied ? `${s.response_rate}%` : "—"}</td>
              </tr>
            ))}
            {data.source_quality.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">No jobs discovered yet.</td></tr>
            )}
          </tbody>
        </table>
      </Card>

      {data.upcoming_deadlines.length > 0 && (
        <Card className="overflow-hidden">
          <h3 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-800">
            Upcoming application deadlines
          </h3>
          <div className="divide-y divide-slate-50">
            {data.upcoming_deadlines.map((d) => (
              <div key={d.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <span className="rounded bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">{d.deadline}</span>
                <span className="font-medium text-slate-800">{d.company}</span>
                <span className="truncate text-slate-500">{d.title}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
