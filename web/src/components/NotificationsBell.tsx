import { useEffect, useState } from "react";
import { Bell, BellRing } from "lucide-react";
import { api, type Notification } from "../api";

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);

  const load = () => api.notifications().then(setItems).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);

  const unread = items.filter((n) => !n.read).length;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        title="Notifications"
        className="relative inline-flex items-center rounded-lg p-2 text-slate-600 hover:bg-slate-100"
      >
        {unread ? <BellRing className="size-4" /> : <Bell className="size-4" />}
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex size-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-semibold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-2 w-96 max-w-[90vw] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
              <span className="text-sm font-semibold text-slate-800">Notifications</span>
              {unread > 0 && (
                <button className="text-xs font-medium text-blue-600 hover:underline"
                  onClick={() => api.readAllNotifications().then(load)}>
                  Mark all read
                </button>
              )}
            </div>
            <div className="max-h-96 overflow-auto">
              {items.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-slate-500">No notifications yet.</div>
              ) : (
                items.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => { api.readNotification(n.id).then(load); }}
                    className={`block w-full border-b border-slate-50 px-4 py-3 text-left hover:bg-slate-50 ${n.read ? "opacity-60" : ""}`}
                  >
                    <div className="flex items-start gap-2">
                      <span className={`mt-1.5 size-2 shrink-0 rounded-full ${
                        n.kind === "deadline" ? "bg-red-500" : n.kind === "status" ? "bg-violet-500" : "bg-blue-500"
                      }`} />
                      <div>
                        <div className="text-sm font-medium text-slate-800">{n.title}</div>
                        {n.body && <div className="mt-0.5 text-xs text-slate-500">{n.body}</div>}
                        <div className="mt-1 text-[11px] text-slate-400">{n.created_at.slice(0, 16).replace("T", " ")}</div>
                      </div>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
