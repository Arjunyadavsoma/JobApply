import { useEffect, useRef, useState } from "react";
import { ExternalLink, Pencil, Upload } from "lucide-react";
import { api } from "../api";
import { Button, Card, useAction } from "./ui";

export function ResumePage({ running, version, onStarted }: { running: boolean; version: number; onStarted: () => void }) {
  const [data, setData] = useState<{ resume: Record<string, any> | null; has_preview?: boolean; has_original?: boolean } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction();

  useEffect(() => { api.resume().then(setData); }, [version]);

  const upload = (f: File) =>
    run("upload", async () => { await api.uploadResume(f); onStarted(); }, "Reading your resume (~30 s)...");

  const r = data?.resume;
  const skills = r ? r.skills.reduce((n: number, g: { items: string[] }) => n + g.items.length, 0) : 0;

  return (
    <div className="space-y-5">
      <Card className="flex flex-wrap items-center gap-4 p-5">
        <div className="flex-1">
          <h2 className="text-base font-semibold">Master resume</h2>
          <p className="mt-0.5 text-sm text-slate-500">
            Your base resume. When you press Tailor resume on a job, Claude rewrites it for that job description
            (and may add job-relevant skills, which it lists for you). Employers, titles, dates and numbers never change.
          </p>
        </div>
        <input ref={file} type="file" accept="application/pdf" className="hidden"
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <Button variant="primary" icon={<Upload className="size-4" />} loading={busy === "upload"} disabled={running}
          onClick={() => file.current?.click()}>
          {r ? "Upload updated resume" : "Upload resume (PDF)"}
        </Button>
        {r && (
          <Button icon={<Pencil className="size-4" />} onClick={() => run("edit", api.openResumeJson)}
            title="Opens master_resume.json in your editor to fix anything the parser got wrong">
            Edit data
          </Button>
        )}
      </Card>

      {r && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {[["Name", r.name], ["Roles", r.experience.length], ["Projects", r.projects.length], ["Skills", skills]].map(([k, v]) => (
              <Card key={k} className="p-4">
                <div className="text-sm text-slate-500">{k}</div>
                <div className="mt-1 truncate text-xl font-semibold">{v}</div>
              </Card>
            ))}
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            {data?.has_original && (
              <Card className="overflow-hidden">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 text-sm">
                  <span className="font-medium">Your original resume (used when a job isn't tailored)</span>
                  <a href={`/api/resume/original.pdf?v=${version}`} target="_blank" rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-blue-700 hover:underline">
                    <ExternalLink className="size-4" /> Open
                  </a>
                </div>
                <iframe title="Original resume" src={`/api/resume/original.pdf?v=${version}#view=FitH`} className="h-[1000px] w-full" />
              </Card>
            )}
            {data?.has_preview && (
              <Card className="overflow-hidden">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 text-sm">
                  <span className="font-medium">Tailoring template (same content, before tailoring)</span>
                  <a href={`/api/resume/preview.pdf?v=${version}`} target="_blank" rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-blue-700 hover:underline">
                    <ExternalLink className="size-4" /> Open
                  </a>
                </div>
                <iframe title="Template preview" src={`/api/resume/preview.pdf?v=${version}#view=FitH`} className="h-[1000px] w-full" />
              </Card>
            )}
          </div>
        </>
      )}
    </div>
  );
}
