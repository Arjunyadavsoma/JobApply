import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronUp, Loader2, Square, Terminal } from "lucide-react";
import { api, type Task } from "../api";
import { Button, Card, useAction } from "./ui";

function elapsed(t: Task) {
  const end = t.ended ? new Date(t.ended) : new Date();
  const s = Math.max(0, Math.round((end.getTime() - new Date(t.started).getTime()) / 1000));
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/** Live progress of the running (or last) background task. */
export function TaskPanel({ task, log }: { task: Task | null; log: string }) {
  const [open, setOpen] = useState(false);
  const pre = useRef<HTMLPreElement>(null);
  const { busy, run } = useAction();

  useEffect(() => {
    if (task?.running) setOpen(true);
  }, [task?.running, task?.started]);

  useEffect(() => {
    const el = pre.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log, open]);

  if (!task) return null;
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        {task.running ? (
          <Loader2 className="size-5 animate-spin text-blue-600" />
        ) : (
          <CheckCircle2 className="size-5 text-emerald-600" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{task.label}</div>
          <div className="text-xs text-slate-500">
            {task.running ? "Running" : "Finished"} · {elapsed(task)}
          </div>
        </div>
        {task.running && (
          <Button variant="danger" icon={<Square className="size-3.5" />} loading={busy === "stop"}
            onClick={() => run("stop", api.stopTask, "Stopped")}>
            Stop
          </Button>
        )}
        <Button variant="ghost" onClick={() => setOpen(!open)} icon={<Terminal className="size-4" />}>
          {open ? "Hide log" : "Show log"} {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        </Button>
      </div>
      {open && (
        <pre
          ref={pre}
          className="max-h-72 overflow-auto whitespace-pre-wrap border-t border-slate-800 bg-slate-900 px-4 py-3 font-mono text-xs leading-relaxed text-slate-200"
        >
          {log || "Waiting for output..."}
        </pre>
      )}
    </Card>
  );
}
