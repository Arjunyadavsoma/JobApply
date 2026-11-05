import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { Loader2, X } from "lucide-react";

// ---------------------------------------------------------------- Button

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-300",
  secondary: "bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 disabled:text-slate-400",
  ghost: "text-slate-600 hover:bg-slate-100 disabled:text-slate-300",
  danger: "bg-white text-red-600 border border-red-200 hover:bg-red-50",
};

export function Button({
  children, onClick, variant = "secondary", disabled, loading, icon, title, type = "button", className = "",
}: {
  children?: ReactNode; onClick?: () => void; variant?: Variant; disabled?: boolean; loading?: boolean;
  icon?: ReactNode; title?: string; type?: "button" | "submit"; className?: string;
}) {
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium
        transition-colors disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
    >
      {loading ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>{children}</div>;
}

export function Label({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-1.5 text-sm font-medium text-slate-700">
      {children}
      {hint && <span className="ml-1.5 font-normal text-slate-400">{hint}</span>}
    </div>
  );
}

// ---------------------------------------------------------------- Tag input

export function TagInput({ value, onChange, placeholder }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const items = raw.split(",").map((s) => s.trim()).filter(Boolean);
    const next = [...value];
    for (const it of items) if (!next.some((v) => v.toLowerCase() === it.toLowerCase())) next.push(it);
    onChange(next);
    setDraft("");
  };
  return (
    <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2 py-1.5
      focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
      {value.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded-md bg-blue-50 px-2 py-0.5 text-sm text-blue-700">
          {v}
          <button type="button" onClick={() => onChange(value.filter((x) => x !== v))} className="hover:text-blue-900">
            <X className="size-3.5" />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === ",") && draft.trim()) {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => draft.trim() && add(draft)}
        placeholder={value.length ? "" : placeholder}
        className="min-w-32 flex-1 bg-transparent py-0.5 text-sm outline-none placeholder:text-slate-400"
      />
    </div>
  );
}

// ---------------------------------------------------------------- Chip toggle

export function Chip({ active, onClick, children, icon }: {
  active: boolean; onClick: () => void; children: ReactNode; icon?: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors ${
        active ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- Score badge

export function ScoreBadge({ score, estimate, size = "sm" }: {
  score: number | null; estimate?: number | null; size?: "sm" | "lg";
}) {
  if (score === null || score === undefined) {
    if (estimate === null || estimate === undefined) return <span className="text-sm text-slate-300">—</span>;
    const dim = size === "lg" ? "size-14 text-lg" : "size-9 text-xs";
    return (
      <span title="Quick estimate from your skills, title and experience - press Score for Claude's rating"
        className={`inline-flex ${dim} items-center justify-center rounded-full border border-dashed border-slate-300 font-medium text-slate-500`}>
        ~{estimate}
      </span>
    );
  }
  const cls =
    score >= 70 ? "bg-emerald-100 text-emerald-700" : score >= 50 ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500";
  const dim = size === "lg" ? "size-14 text-xl" : "size-9 text-sm";
  return <span className={`inline-flex ${dim} items-center justify-center rounded-full font-semibold ${cls}`}>{score}</span>;
}

// ---------------------------------------------------------------- Toasts

type Toast = { id: number; text: string; kind: "ok" | "error" };
const ToastCtx = createContext<(text: string, kind?: "ok" | "error") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded-lg px-4 py-2.5 text-sm shadow-lg ${
              t.kind === "error" ? "bg-red-600 text-white" : "bg-slate-900 text-white"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/** Wrap an async action: toast on error, optional toast on success. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(
    async (key: string, fn: () => Promise<unknown>, success?: string) => {
      setBusy(key);
      try {
        await fn();
        if (success) toast(success);
        return true;
      } catch (e) {
        toast((e as Error).message, "error");
        return false;
      } finally {
        setBusy(null);
      }
    },
    [toast],
  );
  return { busy, run };
}
