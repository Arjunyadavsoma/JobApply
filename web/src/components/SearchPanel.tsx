import { useEffect, useState } from "react";
import { Building2, Briefcase, Globe, Rocket, Search, Users, Sparkles } from "lucide-react";
import { api, type Config } from "../api";
import { Button, Card, Chip, Label, TagInput, useAction } from "./ui";

const SOURCES = [
  { id: "companies", label: "Company career sites", icon: <Building2 className="size-4" /> },
  { id: "linkedin", label: "LinkedIn", icon: <Users className="size-4" /> },
  { id: "naukri", label: "Naukri", icon: <Briefcase className="size-4" /> },
  { id: "instahyre", label: "Instahyre", icon: <Rocket className="size-4" /> },
  { id: "remotive", label: "Remotive (remote)", icon: <Globe className="size-4" /> },
  { id: "arbeitnow", label: "Arbeitnow (EU/remote)", icon: <Globe className="size-4" /> },
];

export function SearchPanel({ config, running, onStarted }: {
  config: Config | null; running: boolean; onStarted: () => void;
}) {
  const [keywords, setKeywords] = useState<string[]>([]);
  const [locations, setLocations] = useState<string[]>([]);
  const [sources, setSources] = useState<string[]>(["companies", "linkedin", "naukri", "instahyre"]);
  const [maxAge, setMaxAge] = useState(14);
  const [doScore, setDoScore] = useState(true);
  const [doTailor, setDoTailor] = useState(false);
  const [scoreLimit, setScoreLimit] = useState(30);
  const { busy, run } = useAction();

  useEffect(() => {
    if (!config) return;
    setKeywords(config.search.keywords ?? []);
    setLocations(config.search.locations ?? []);
    setMaxAge(config.search.max_age_days ?? 14);
  }, [config]);

  const toggle = (id: string) =>
    setSources((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const companyCount = config
    ? Object.values(config.sources.companies ?? {}).reduce((n, l) => n + (l?.length ?? 0), 0)
    : 0;

  const submit = () =>
    run(
      "search",
      async () => {
        await api.search({
          keywords, locations, sources, max_age_days: maxAge,
          score_limit: doScore ? scoreLimit : 0,
          tailor_limit: 0, // resumes are tailored per job, from the job's panel
        });
        onStarted();
      },
      "Search started - results appear below as they come in",
    );

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center gap-2">
        <Search className="size-5 text-blue-600" />
        <h2 className="text-lg font-semibold">Search jobs</h2>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <Label hint="press Enter after each">Job titles / keywords</Label>
          <TagInput value={keywords} onChange={setKeywords} placeholder="e.g. Java Developer" />
        </div>
        <div>
          <Label hint="'India' = nationwide · 'Remote' = remote only">Locations</Label>
          <TagInput value={locations} onChange={setLocations} placeholder="e.g. Bengaluru, Remote" />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-6">
        <div>
          <Label>Search on</Label>
          <div className="flex flex-wrap gap-2">
            {SOURCES.map((s) => (
              <Chip key={s.id} active={sources.includes(s.id)} onClick={() => toggle(s.id)} icon={s.icon}>
                {s.label}
                {s.id === "companies" && companyCount > 0 && <span className="opacity-70">({companyCount})</span>}
              </Chip>
            ))}
          </div>
        </div>
        <div>
          <Label>Posted within</Label>
          <select
            value={maxAge}
            onChange={(e) => setMaxAge(Number(e.target.value))}
            className="h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm"
          >
            {[1, 3, 7, 14, 30].map((d) => (
              <option key={d} value={d}>{d === 1 ? "24 hours" : `${d} days`}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg bg-slate-50 px-4 py-3 text-sm">
        <span className="font-medium text-slate-600">After finding:</span>
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={doScore} onChange={(e) => setDoScore(e.target.checked)} className="size-4 accent-blue-600" />
          <Sparkles className="size-4 text-violet-500" /> Score up to
          <input type="number" min={1} max={500} value={scoreLimit} disabled={!doScore}
            onChange={(e) => setScoreLimit(Number(e.target.value))}
            className="w-16 rounded border border-slate-300 px-1.5 py-0.5 disabled:opacity-50" />
          new jobs <span className="text-slate-400">(~10 s each)</span>
        </label>
        <span className="text-slate-400">Resumes are tailored per job - open a job and press <b>Tailor resume</b>.</span>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Button variant="primary" icon={<Search className="size-4" />} onClick={submit}
          loading={busy === "search"} disabled={running || sources.length === 0} className="px-6">
          Search
        </Button>
        {running && <span className="text-sm text-slate-500">A task is running - wait for it or press Stop.</span>}
        {!running && sources.includes("naukri") && (
          <span className="text-sm text-slate-500">Naukri opens a Chrome window while searching - leave it open.</span>
        )}
      </div>
    </Card>
  );
}
