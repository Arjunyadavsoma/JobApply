import { useEffect, useState } from "react";
import { CircleAlert, Save } from "lucide-react";
import { api, type Config } from "../api";
import { Button, Card, Label, TagInput, useAction } from "./ui";

const APPLICANT_FIELDS: [string, string, string?][] = [
  ["work_authorization", "Work authorization", "e.g. Indian citizen; needs visa sponsorship abroad"],
  ["notice_period", "Notice period", "e.g. 60 days"],
  ["current_ctc", "Current CTC", "e.g. 12 LPA"],
  ["expected_ctc", "Expected CTC", "e.g. 18 LPA or Negotiable"],
  ["willing_to_relocate", "Willing to relocate"],
  ["years_of_experience", "Years of experience"],
  ["current_location", "Current location"],
  ["phone", "Phone"],
  ["email", "Email"],
  ["linkedin_url", "LinkedIn URL"],
  ["github_url", "GitHub URL"],
  ["portfolio_url", "Portfolio URL"],
  ["gender", "Gender (optional)", "Leave blank to skip voluntary questions"],
  ["requires_sponsorship_to_work_in_india", "Need visa sponsorship to work in India?", "e.g. No"],
  ["employment_restrictions_or_non_compete", "Non-compete / post-employment restrictions?", "e.g. No"],
  ["date_of_birth", "Date of birth", "e.g. 05/03/2002"],
  ["address_line", "Address"],
  ["pincode", "PIN code"],
  ["languages_known", "Languages known", "e.g. English, Hindi, Marathi"],
  ["open_to_shifts_or_hybrid", "Open to shifts / hybrid / office?", "e.g. Yes - hybrid or office"],
];

const humanize = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="text-base font-semibold">{title}</h2>
      {hint && <p className="mt-0.5 text-sm text-slate-500">{hint}</p>}
      <div className="mt-4">{children}</div>
    </Card>
  );
}

const input = "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100";

export function SettingsPage() {
  const [cfg, setCfg] = useState<Config | null>(null);
  const { busy, run } = useAction();

  useEffect(() => { api.config().then(setCfg); }, []);
  if (!cfg) return <div className="text-slate-500">Loading...</div>;

  const set = (fn: (c: Config) => void) => setCfg((c) => { const n = structuredClone(c!); fn(n); return n; });
  const companies = cfg.sources.companies ?? {};
  const missing = ["work_authorization", "notice_period", "expected_ctc"].filter((k) => !cfg.applicant[k]);

  const save = () => run("save", () => api.saveConfig({
    search: cfg.search, sources: cfg.sources, applicant: cfg.applicant, llm: cfg.llm,
  }), "Settings saved");

  return (
    <div className="space-y-5">
      {missing.length > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <CircleAlert className="mt-0.5 size-4 shrink-0" />
          Fill in {missing.map((m) => APPLICANT_FIELDS.find((f) => f[0] === m)![1]).join(", ")} below - until then,
          application forms asking for them are left for you to answer.
        </div>
      )}

      <Section title="Matching" hint="Which jobs count as a good match.">
        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <Label hint={`${cfg.search.min_match_score} / 100`}>Minimum match score</Label>
            <input type="range" min={0} max={100} value={cfg.search.min_match_score}
              onChange={(e) => set((c) => { c.search.min_match_score = Number(e.target.value); })}
              className="w-full accent-blue-600" />
            <p className="mt-1 text-xs text-slate-500">70 = solid fits only · 60 = include long shots</p>
          </div>
          <div>
            <Label hint="one per entry">Skip job titles containing</Label>
            <TagInput value={cfg.search.title_exclude ?? []}
              onChange={(v) => set((c) => { c.search.title_exclude = v; })} />
          </div>
        </div>
      </Section>

      <Section title="Company career sites" hint="Companies scanned directly on their own career sites. Workday and SmartRecruiters companies are searched with your keywords; the others list all open roles.">
        <div className="grid gap-4">
          {([
            ["portals", "Career portals (any company)",
              "paste the company's careers URL, e.g. Amazon | https://www.amazon.jobs - the agent detects the site type"],
            ["greenhouse", "Greenhouse", "board name from job-boards.greenhouse.io/NAME"],
            ["lever", "Lever", "board name from jobs.lever.co/NAME"],
            ["ashby", "Ashby", "board name from jobs.ashbyhq.com/NAME"],
            ["workday", "Workday", "careers URL, e.g. Mastercard | https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"],
            ["smartrecruiters", "SmartRecruiters", "company id from jobs.smartrecruiters.com/ID"],
            ["workable", "Workable", "slug from apply.workable.com/SLUG"],
          ] as const).map(([ats, label, hint]) => (
            <div key={ats}>
              <Label hint={hint}>{label}</Label>
              <TagInput value={companies[ats] ?? []} placeholder={ats === "workday" || ats === "portals" ? "Name | careers URL" : "name"}
                onChange={(v) => set((c) => { c.sources.companies = { ...c.sources.companies, [ats]: v }; })} />
            </div>
          ))}
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <Label hint="~10 jobs per page, per keyword x location">LinkedIn pages per search</Label>
              <input type="number" min={1} max={10} className={input} value={cfg.sources.linkedin.pages}
                onChange={(e) => set((c) => { c.sources.linkedin.pages = Number(e.target.value); })} />
            </div>
            <div>
              <Label hint="~20 jobs per page">Naukri pages per search</Label>
              <input type="number" min={1} max={10} className={input} value={cfg.sources.naukri.pages}
                onChange={(e) => set((c) => { c.sources.naukri.pages = Number(e.target.value); })} />
            </div>
          </div>
        </div>
      </Section>

      <Section title="Answers for application forms" hint="Used only to fill forms. The agent never guesses - blank fields are left for you on each form.">
        <div className="grid gap-4 md:grid-cols-2">
          {[
            ...APPLICANT_FIELDS,
            ...Object.keys(cfg.applicant)
              .filter((k) => !APPLICANT_FIELDS.some((f) => f[0] === k))
              .map((k) => [k, humanize(k)] as [string, string]),
          ].map(([key, label, ph]) => (
            <div key={key}>
              <Label>{label}</Label>
              <input className={input} value={cfg.applicant[key] ?? ""} placeholder={ph}
                onChange={(e) => set((c) => { c.applicant[key] = e.target.value; })} />
            </div>
          ))}
        </div>
      </Section>

      <Section title="AI model" hint="Which LLM runs the scoring, tailoring and form filling. API keys live in .env.">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <Label hint="subscription = Claude Code CLI login · api = ANTHROPIC_API_KEY · groq = GROQ_API_KEY">Backend</Label>
            <select className={input} value={String(cfg.llm.backend ?? "subscription")}
              onChange={(e) => set((c) => { c.llm.backend = e.target.value; })}>
              <option value="subscription">Claude subscription (Claude Code CLI)</option>
              <option value="api">Anthropic API key</option>
              <option value="groq">Groq API key</option>
            </select>
          </div>
          <div>
            <Label hint={(cfg.llm.backend ?? "subscription") === "groq"
              ? "any Groq model, e.g. openai/gpt-oss-120b, qwen/qwen3.8-27b"
              : "e.g. claude-opus-5"}>Model</Label>
            <input className={input}
              value={(cfg.llm.backend ?? "subscription") === "groq"
                ? String(cfg.llm.groq_model
                    ?? (cfg.llm.model && !String(cfg.llm.model).startsWith("claude")
                      ? cfg.llm.model : "openai/gpt-oss-120b"))
                : String(cfg.llm.model ?? "")}
              placeholder={(cfg.llm.backend ?? "subscription") === "groq" ? "openai/gpt-oss-120b" : "claude-opus-5"}
              onChange={(e) => set((c) => {
                if ((c.llm.backend ?? "subscription") === "groq") c.llm.groq_model = e.target.value;
                else c.llm.model = e.target.value;
              })} />
          </div>
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-3">
          {([["score_effort", "Scoring"], ["tailor_effort", "Resume tailoring"], ["form_effort", "Form filling"]] as const).map(([k, label]) => (
            <div key={k}>
              <Label>{label}</Label>
              <select className={input} value={cfg.llm[k] ?? "medium"}
                onChange={(e) => set((c) => { c.llm[k] = e.target.value; })}>
                {EFFORTS.map((x) => <option key={x}>{x}</option>)}
              </select>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Lower effort = faster and uses less of your Claude plan. On Groq, these map to the
          model's reasoning effort (gpt-oss models only).
        </p>
      </Section>

      <div className="sticky bottom-4 flex justify-end">
        <Button variant="primary" icon={<Save className="size-4" />} loading={busy === "save"} onClick={save}
          className="px-6 shadow-lg">
          Save settings
        </Button>
      </div>
    </div>
  );
}
