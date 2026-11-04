import { useEffect, useState } from "react";
import { api, type CandidateProfile } from "../api";
import { Button, Card, Label, TagInput, useAction } from "./ui";

/** Editable structured candidate profile - drives eligibility checks and form answers. */
export function ProfilePage() {
  const [p, setP] = useState<CandidateProfile | null>(null);
  const { busy, run } = useAction();

  useEffect(() => {
    api.profile().then((r) => setP(r.profile)).catch(() => {});
  }, []);

  if (!p) return <div className="py-20 text-center text-sm text-slate-500">Loading profile…</div>;
  const set = <K extends keyof CandidateProfile>(k: K, v: CandidateProfile[K]) =>
    setP({ ...p, [k]: v } as CandidateProfile);

  const save = () => run("save", async () => { await api.saveProfile(p); }, "Profile saved");
  const num = (v: number | null) => (v === null ? "" : String(v));
  const toNum = (v: string) => (v.trim() === "" ? null : Number(v));

  const Field = ({ label, value, onChange, placeholder, type = "text", hint }: {
    label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string; hint?: string;
  }) => (
    <div>
      <Label hint={hint}>{label}</Label>
      <input type={type} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
    </div>
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="max-w-2xl text-sm text-slate-600">
          This profile powers the eligibility engine (batch, CGPA, backlogs) and every form answer.
          Empty fields stay <b>unknown</b> - the agent never guesses them.
        </p>
        <Button variant="primary" onClick={save} loading={busy === "save"}>Save profile</Button>
      </div>

      <Card className="grid gap-4 p-5 md:grid-cols-2 lg:grid-cols-3">
        <h3 className="col-span-full text-sm font-semibold text-slate-800">Personal & contact</h3>
        <Field label="Full name" value={p.full_name} onChange={(v) => set("full_name", v)} />
        <Field label="Email" value={p.email} onChange={(v) => set("email", v)} type="email" />
        <Field label="Phone" value={p.phone} onChange={(v) => set("phone", v)} placeholder="+91 …" />
        <Field label="Current location" value={p.current_location} onChange={(v) => set("current_location", v)}
          placeholder="Hyderabad, India" />
        <Field label="LinkedIn" value={p.linkedin_url} onChange={(v) => set("linkedin_url", v)} />
        <Field label="GitHub" value={p.github_url} onChange={(v) => set("github_url", v)} />
        <Field label="Portfolio" value={p.portfolio_url} onChange={(v) => set("portfolio_url", v)} />
        <Field label="Work authorization" value={p.work_authorization} onChange={(v) => set("work_authorization", v)}
          placeholder="Indian citizen; authorized to work in India" />
      </Card>

      <Card className="grid gap-4 p-5 md:grid-cols-2 lg:grid-cols-3">
        <h3 className="col-span-full text-sm font-semibold text-slate-800">Academics (India eligibility engine)</h3>
        <div>
          <Label hint="drives batch eligibility">Graduation year</Label>
          <input type="number" value={num(p.graduation_year)} onChange={(e) => set("graduation_year", toNum(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="2027" />
        </div>
        <Field label="Degree" value={p.degree} onChange={(v) => set("degree", v)} placeholder="B.Tech" />
        <Field label="Branch" value={p.branch} onChange={(v) => set("branch", v)}
          placeholder="CSE (AI & ML)" />
        <Field label="College" value={p.college} onChange={(v) => set("college", v)} />
        <Field label="University" value={p.university} onChange={(v) => set("university", v)} />
        <div>
          <Label>CGPA</Label>
          <input type="number" step="0.01" value={num(p.cgpa)} onChange={(e) => set("cgpa", toNum(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="8.2" />
        </div>
        <div>
          <Label hint="leave empty if unknown">Class 10 (%)</Label>
          <input type="number" step="0.1" value={num(p.tenth_percentage)}
            onChange={(e) => set("tenth_percentage", toNum(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="89" />
        </div>
        <div>
          <Label hint="leave empty if unknown">Class 12 / Diploma (%)</Label>
          <input type="number" step="0.1" value={num(p.twelfth_percentage)}
            onChange={(e) => set("twelfth_percentage", toNum(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="92" />
        </div>
        <div>
          <Label hint="0 unless you have them">Active backlogs</Label>
          <input type="number" value={p.active_backlogs} onChange={(e) => set("active_backlogs", Number(e.target.value) || 0)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </div>
        <div>
          <Label hint="cleared ones">Previous backlogs</Label>
          <input type="number" value={p.previous_backlogs}
            onChange={(e) => set("previous_backlogs", Number(e.target.value) || 0)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </div>
      </Card>

      <Card className="grid gap-4 p-5 md:grid-cols-2 lg:grid-cols-3">
        <h3 className="col-span-full text-sm font-semibold text-slate-800">Career stage & preferences</h3>
        <div>
          <Label>Candidate type</Label>
          <select value={p.candidate_type} onChange={(e) => set("candidate_type", e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="fresher">Fresher</option>
            <option value="entry_level">Entry level (0-2 yrs)</option>
            <option value="experienced">Experienced</option>
          </select>
        </div>
        <div>
          <Label hint="used by eligibility + local fit estimate">Total experience (years)</Label>
          <input type="number" step="0.5" value={p.total_experience_years}
            onChange={(e) => set("total_experience_years", Number(e.target.value) || 0)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </div>
        <div>
          <Label hint="₹ lakhs per annum; empty = no preference">Expected CTC (LPA)</Label>
          <input type="number" step="0.5" value={num(p.expected_ctc_lpa)}
            onChange={(e) => set("expected_ctc_lpa", toNum(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="6" />
        </div>
        <div>
          <Label hint="₹ per month for internships">Expected stipend (₹/month)</Label>
          <input type="number" value={num(p.expected_stipend_inr)}
            onChange={(e) => set("expected_stipend_inr", toNum(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="15000" />
        </div>
        <Field label="Notice period" value={p.notice_period} onChange={(v) => set("notice_period", v)}
          placeholder="Immediate / 30 days" />
        <div>
          <Label hint="₹ lakhs per annum; empty = skip">Current CTC (LPA)</Label>
          <input type="number" step="0.5" value={num(p.current_ctc_lpa)}
            onChange={(e) => set("current_ctc_lpa", toNum(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </div>
        <div className="md:col-span-2 lg:col-span-3">
          <Label>Preferred roles</Label>
          <TagInput value={p.preferred_roles} onChange={(v) => set("preferred_roles", v)}
            placeholder="AI Engineer, ML Engineer…" />
        </div>
        <div className="md:col-span-2 lg:col-span-3">
          <Label>Secondary roles</Label>
          <TagInput value={p.secondary_roles} onChange={(v) => set("secondary_roles", v)}
            placeholder="Data Analyst, Backend Developer…" />
        </div>
        <div className="md:col-span-2 lg:col-span-3">
          <Label hint="Hyderabad, Bengaluru, Remote India…">Preferred locations</Label>
          <TagInput value={p.preferred_locations} onChange={(v) => set("preferred_locations", v)} />
        </div>
        <div className="flex items-center gap-6 text-sm text-slate-700">
          <label className="inline-flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={p.open_to_relocation} onChange={(e) => set("open_to_relocation", e.target.checked)}
              className="size-4 accent-blue-600" />
            Open to relocation
          </label>
          <label className="inline-flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={p.open_to_remote} onChange={(e) => set("open_to_remote", e.target.checked)}
              className="size-4 accent-blue-600" />
            Open to remote
          </label>
        </div>
      </Card>

      <div className="flex justify-end">
        <Button variant="primary" onClick={save} loading={busy === "save"}>Save profile</Button>
      </div>
    </div>
  );
}
