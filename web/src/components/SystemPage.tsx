import { useCallback, useEffect, useState } from "react";
import {
  Activity, DatabaseBackup, HardDrive, KeyRound, Plus, RefreshCw, RotateCw,
  ShieldCheck, Table2,
} from "lucide-react";
import {
  api, type AtsMatrixRow, type BackupInfo, type CredentialItem, type Diagnostics,
  type GroqKeyPoolStatus, type GroqKeyStatus, type SystemStatus,
} from "../api";
import { Button, Card, useAction } from "./ui";

type Tab = "health" | "ats" | "credentials" | "backup";

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "health", label: "System Health", icon: <Activity className="size-4" /> },
  { id: "ats", label: "ATS Compatibility", icon: <Table2 className="size-4" /> },
  { id: "credentials", label: "Credential Manager", icon: <KeyRound className="size-4" /> },
  { id: "backup", label: "Backup & Restore", icon: <DatabaseBackup className="size-4" /> },
];

const SUPPORT_META: Record<string, { label: string; cls: string; hint: string }> = {
  SANDBOX_VERIFIED: {
    label: "Sandbox verified", cls: "bg-emerald-100 text-emerald-800",
    hint: "Passed the full Playwright E2E suite against local sandbox fixtures. Not a live-platform claim.",
  },
  DETECTION_ONLY: {
    label: "Detection only", cls: "bg-amber-100 text-amber-800",
    hint: "Detected by URL/DOM markers; the semantic engine drives best-effort but no sandbox evidence exists.",
  },
  MANUAL_HANDOFF: {
    label: "Manual hand-off", cls: "bg-slate-200 text-slate-700",
    hint: "Login-walled platform: the agent prepares everything, you review and submit.",
  },
  VERIFIED: {
    label: "Verified (live)", cls: "bg-blue-100 text-blue-800",
    hint: "Verified against the real platform with an authorized account.",
  },
};

function CheckRow({ name, check }: { name: string; check: { ok: boolean; detail?: string } }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-slate-100 py-2.5 last:border-0">
      <div className="flex items-center gap-2 text-sm">
        <span className={`size-2.5 rounded-full ${check.ok ? "bg-emerald-500" : "bg-red-500"}`} />
        <span className="font-medium text-slate-700">{name}</span>
        {check.detail && <span className="text-slate-500">— {check.detail}</span>}
      </div>
    </div>
  );
}

function HealthTab() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const { run, busy } = useAction();

  const refresh = useCallback(() => {
    run("action", async () => {
      const [s, d] = await Promise.all([api.systemStatus(), api.diagnostics()]);
      setStatus(s);
      setDiag(d);
    });
  }, [run]);

  useEffect(refresh, [refresh]);

  if (!status || !diag) {
    return <Card className="p-8 text-center text-slate-500">
      {busy ? "Running diagnostics…" : "Diagnostics unavailable — is the server running?"}
    </Card>;
  }
  const uptime = status.uptime_s > 3600
    ? `${(status.uptime_s / 3600).toFixed(1)} h`
    : `${Math.max(1, Math.round(status.uptime_s / 60))} min`;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Service status</h2>
          <Button variant="ghost" onClick={refresh}>
            <RefreshCw className={`size-4 ${busy ? "animate-spin" : ""}`} /> Refresh
          </Button>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div><span className="text-slate-500">Version</span><div className="font-semibold">{status.version}</div></div>
          <div><span className="text-slate-500">Uptime</span><div className="font-semibold">{uptime}</div></div>
          <div><span className="text-slate-500">Jobs tracked</span><div className="font-semibold">{status.jobs_total}</div></div>
          <div><span className="text-slate-500">Submissions</span><div className="font-semibold">{status.submissions}</div></div>
          <div><span className="text-slate-500">AI calls</span><div className="font-semibold">{status.ai_calls}</div></div>
          <div>
            <span className="text-slate-500">Free disk</span>
            <div className={`font-semibold ${status.storage.ok ? "" : "text-red-600"}`}>
              {status.storage.free_gb ?? "?"} GB
            </div>
          </div>
        </div>
      </Card>
      <Card className="p-5">
        <h2 className="text-base font-semibold">Diagnostics</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          Overall: <span className={diag.status === "ok" ? "font-semibold text-emerald-700" : "font-semibold text-amber-700"}>
            {diag.status}
          </span>
        </p>
        <div className="mt-3">
          {Object.entries(diag.checks).map(([name, check]) => (
            <CheckRow key={name} name={name} check={check} />
          ))}
        </div>
      </Card>
    </div>
  );
}

function AtsMatrixTab() {
  const [rows, setRows] = useState<AtsMatrixRow[] | null>(null);
  useEffect(() => {
    api.atsMatrix().then(setRows).catch(() => setRows(null));
  }, []);
  if (!rows) return <Card className="p-8 text-center text-slate-500">Matrix unavailable.</Card>;
  return (
    <Card className="overflow-x-auto p-5">
      <h2 className="text-base font-semibold">ATS compatibility matrix</h2>
      <p className="mt-0.5 text-sm text-slate-500">
        Support levels are evidence-based: a platform is <b>sandbox-verified</b> only after its local
        fixture passed the full Playwright E2E suite. No live-platform automation is claimed.
      </p>
      <table className="mt-4 w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="py-2">Platform</th>
            <th className="py-2">Support level</th>
            <th className="py-2">Multi-step</th>
            <th className="py-2">Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const meta = SUPPORT_META[r.support_level] ?? {
              label: r.support_level, cls: "bg-slate-100 text-slate-600", hint: "",
            };
            return (
              <tr key={r.platform} className="border-b border-slate-100 last:border-0">
                <td className="py-2.5 font-medium">{r.name}</td>
                <td className="py-2.5">
                  <span title={meta.hint}
                    className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${meta.cls}`}>
                    {meta.label}
                  </span>
                </td>
                <td className="py-2.5 text-slate-600">{r.multi_step ? "Yes" : "—"}</td>
                <td className="py-2.5 text-slate-500">{r.notes}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

const PROVIDERS = ["groq", "openai", "anthropic", "gmail", "other"];

const KEY_STATE_META: Record<GroqKeyStatus["state"], { label: string; cls: string }> = {
  active: { label: "Active", cls: "bg-emerald-100 text-emerald-800" },
  cooling: { label: "Cooling", cls: "bg-amber-100 text-amber-800" },
  disabled: { label: "Disabled", cls: "bg-red-100 text-red-800" },
};

function KeyRotationCard({ onChanged }: { onChanged?: () => void }) {
  const [pool, setPool] = useState<GroqKeyPoolStatus | null>(null);
  const { run, busy } = useAction();

  const refresh = useCallback(() => {
    api.llmKeys().then(setPool).catch(() => setPool(null));
  }, []);
  useEffect(refresh, [refresh]);

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <RotateCw className="size-4 text-blue-600" />
          <h2 className="text-base font-semibold">Groq key rotation</h2>
          {pool && (
            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">
              {pool.active}/{pool.total} active
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={refresh}>
            <RefreshCw className={`size-4 ${busy ? "animate-spin" : ""}`} /> Refresh
          </Button>
          <Button variant="ghost" disabled={!pool || (pool.cooling === 0 && pool.disabled === 0)}
            onClick={() => run("action", async () => {
              await api.resetLlmKeys();
              refresh();
              onChanged?.();
            })}>
            Reset cooldowns
          </Button>
        </div>
      </div>
      <p className="mt-0.5 text-sm text-slate-500">
        Every AI call goes to the next key (round-robin). A rate-limited (429) key rests for the
        server's <code className="rounded bg-slate-100 px-1">retry-after</code> window while the others keep working; a rejected (401/403)
        key is disabled until you reset it. Add keys below (provider: groq) or via{" "}
        <code className="rounded bg-slate-100 px-1">GROQ_API_KEYS=key1,key2</code> in .env.
      </p>
      {!pool ? (
        <p className="mt-3 text-sm text-slate-500">Rotation status unavailable — is the server running?</p>
      ) : pool.total === 0 ? (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          No Groq keys configured. Add one below (provider: groq) or set GROQ_API_KEY in .env —
          several keys rotate automatically and dodge rate limits.
        </p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="py-2">Key</th><th className="py-2">Source</th>
              <th className="py-2">State</th><th className="py-2">Requests</th>
              <th className="py-2">429s</th><th className="py-2">Last used</th>
            </tr>
          </thead>
          <tbody>
            {pool.keys.map((k) => {
              const meta = KEY_STATE_META[k.state] ?? KEY_STATE_META.active;
              return (
                <tr key={k.id} className="border-b border-slate-100 last:border-0">
                  <td className="py-2.5 font-mono text-xs">{k.masked}</td>
                  <td className="py-2.5 text-slate-600">{k.source}</td>
                  <td className="py-2.5">
                    <span title={k.failure_reason}
                      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${meta.cls}`}>
                      {meta.label}{k.state === "cooling" && k.cooldown_s_remaining > 0
                        ? ` ${Math.ceil(k.cooldown_s_remaining)}s` : ""}
                    </span>
                  </td>
                  <td className="py-2.5 text-slate-600">{k.requests}
                    <span className="text-xs text-emerald-700"> ✓{k.successes}</span></td>
                  <td className={`py-2.5 ${k.rate_limited > 0 ? "text-amber-700" : "text-slate-600"}`}>
                    {k.rate_limited}{k.auth_failures > 0 &&
                      <span className="text-xs text-red-700"> · {k.auth_failures} auth</span>}
                  </td>
                  <td className="py-2.5 text-xs text-slate-500">
                    {k.last_used_at ? k.last_used_at.slice(11, 19) + " UTC" : "never"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function CredentialsTab() {
  const [items, setItems] = useState<CredentialItem[] | null>(null);
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("groq");
  const [secret, setSecret] = useState("");
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const { run, busy } = useAction();

  const refresh = useCallback(() => {
    api.credentials().then(setItems).catch(() => setItems(null));
  }, []);
  useEffect(refresh, [refresh]);

  return (
    <div className="space-y-4">
      <KeyRotationCard onChanged={refresh} />
      <Card className="p-5">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-emerald-600" />
          <h2 className="text-base font-semibold">Add a credential</h2>
        </div>
        <p className="mt-0.5 text-sm text-slate-500">
          Secrets are encrypted with AES-256-GCM before they touch the database. The master key lives
          outside the DB (<code className="rounded bg-slate-100 px-1">data/.credentials.key</code>).
          Values are never shown again — only masked previews. Groq keys added here join the
          rotation pool immediately — no restart needed.
        </p>
        <form
          className="mt-4 flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            run("action", async () => {
              await api.addCredential({ name: name.trim(), provider, secret: secret.trim() });
              setName(""); setSecret("");
              refresh();
            });
          }}>
          <label className="text-sm">
            <span className="mb-1 block font-medium text-slate-600">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required
              placeholder="groq-main"
              className="w-40 rounded-lg border border-slate-300 px-3 py-2" />
          </label>
          <label className="text-sm">
            <span className="mb-1 block font-medium text-slate-600">Provider</span>
            <select value={provider} onChange={(e) => setProvider(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2">
              {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block font-medium text-slate-600">Secret value</span>
            <input value={secret} onChange={(e) => setSecret(e.target.value)} required type="password"
              placeholder="gsk_…"
              className="w-72 rounded-lg border border-slate-300 px-3 py-2" />
          </label>
          <Button type="submit" disabled={!!busy || !name || !secret}>
            <Plus className="size-4" /> Add
          </Button>
        </form>
      </Card>

      <Card className="p-5">
        <h2 className="text-base font-semibold">Stored credentials</h2>
        {!items ? (
          <p className="mt-2 text-sm text-slate-500">Vault unavailable (is a master key configured?).</p>
        ) : items.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No credentials stored. The .env file still works — the vault is an additional, safer store.
          </p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2">Name</th><th className="py-2">Provider</th>
                <th className="py-2">Value</th><th className="py-2">Last test</th>
                <th className="py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} className={`border-b border-slate-100 last:border-0 ${c.revoked ? "opacity-60" : ""}`}>
                  <td className="py-2.5 font-medium">{c.name}{c.revoked && " (revoked)"}</td>
                  <td className="py-2.5">{c.provider_label}</td>
                  <td className="py-2.5 font-mono text-xs">{c.masked}</td>
                  <td className="py-2.5 text-xs">
                    {c.last_test_result ?? "never tested"}
                    {testResult[c.name] && <div className="text-slate-500">{testResult[c.name]}</div>}
                  </td>
                  <td className="py-2.5">
                    <div className="flex gap-2">
                      <Button variant="ghost"
                        onClick={() => run("action", async () => {
                          const r = await api.testCredential(c.name);
                          setTestResult((t) => ({ ...t, [c.name]: r.detail }));
                          refresh();
                        })}>Test</Button>
                      {!c.revoked && (
                        <Button variant="ghost"
                          onClick={() => run("action", async () => {
                            await api.revokeCredential(c.name); refresh();
                          })}>Revoke</Button>
                      )}
                      <Button variant="ghost"
                        onClick={() => run("action", async () => {
                          await api.deleteCredential(c.name); refresh();
                        })}>Delete</Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function bytes(n: number): string {
  return n > 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, n >> 10)} KB`;
}

function BackupTab() {
  const [backups, setBackups] = useState<BackupInfo[] | null>(null);
  const [label, setLabel] = useState("");
  const [confirmRestore, setConfirmRestore] = useState<string | null>(null);
  const { run, busy } = useAction();

  const refresh = useCallback(() => {
    api.backups().then((b) => setBackups(b.backups)).catch(() => setBackups(null));
  }, []);
  useEffect(refresh, [refresh]);

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <h2 className="text-base font-semibold">Create a backup</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          Backups use the SQLite online-backup API (safe while the app runs) and are integrity-checked
          and hashed before being reported as good.
        </p>
        <div className="mt-4 flex items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block font-medium text-slate-600">Label (optional)</span>
            <input value={label} onChange={(e) => setLabel(e.target.value)}
              placeholder="pre-migration"
              className="w-56 rounded-lg border border-slate-300 px-3 py-2" />
          </label>
          <Button disabled={!!busy} onClick={() => run("action", async () => {
            await api.createBackup(label); setLabel(""); refresh();
          })}>
            <DatabaseBackup className="size-4" /> Back up now
          </Button>
        </div>
      </Card>

      <Card className="p-5">
        <h2 className="text-base font-semibold">Existing backups</h2>
        {!backups ? (
          <p className="mt-2 text-sm text-slate-500">Backup list unavailable.</p>
        ) : backups.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No backups yet — create one above.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2">Backup</th><th className="py-2">Created</th>
                <th className="py-2">Size</th><th className="py-2">Integrity</th>
                <th className="py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => (
                <tr key={b.name} className="border-b border-slate-100 last:border-0">
                  <td className="py-2.5 font-mono text-xs">{b.name}</td>
                  <td className="py-2.5 text-slate-600">{b.created_at.slice(0, 19).replace("T", " ")}</td>
                  <td className="py-2.5">{bytes(b.bytes)}</td>
                  <td className="py-2.5">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      b.integrity === "ok" ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"}`}>
                      {b.integrity === "ok" ? "ok" : b.integrity}
                    </span>
                  </td>
                  <td className="py-2.5">
                    {confirmRestore === b.name ? (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-red-700">Overwrite the live DB with this backup?</span>
                        <Button onClick={() => run("action", async () => {
                          await api.restoreBackup(b.name); setConfirmRestore(null); refresh();
                        })}>Yes, restore</Button>
                        <Button variant="ghost" onClick={() => setConfirmRestore(null)}>Cancel</Button>
                      </div>
                    ) : (
                      <Button variant="ghost" onClick={() => setConfirmRestore(b.name)}>
                        <HardDrive className="size-4" /> Restore…
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-xs text-slate-500">
          Restoring first saves a pre-restore safety copy of the current database. Restart the app
          after a restore.
        </p>
      </Card>
    </div>
  );
}

export function SystemPage() {
  const [tab, setTab] = useState<Tab>("health");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ${
              tab === t.id ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-100"
            }`}>
            {t.icon}{t.label}
          </button>
        ))}
      </div>
      {tab === "health" && <HealthTab />}
      {tab === "ats" && <AtsMatrixTab />}
      {tab === "credentials" && <CredentialsTab />}
      {tab === "backup" && <BackupTab />}
    </div>
  );
}
