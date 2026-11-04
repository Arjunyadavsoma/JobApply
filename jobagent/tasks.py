"""Run `python -m jobagent ...` commands in the background for the dashboard.

One task at a time (they share the LLM usage and the browser profile). Output goes to
data/logs/<timestamp>_<name>.log; data/task.json records the current task so the dashboard
can show it even after a page reload or a dashboard restart.

Cross-platform: Windows uses tasklist/taskkill (as before); Linux/macOS use os.kill/pgrep
via signal to the process group.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from jobagent.config import DATA_DIR, ROOT

LOG_DIR = DATA_DIR / "logs"
STATE = DATA_DIR / "task.json"
CONTROL = DATA_DIR / "apply_control.txt"   # dashboard -> running `apply-one` browser session

WINDOWS = sys.platform.startswith("win")


def _spawn_flags():
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}     # type: ignore[attr-defined]
    return {"start_new_session": True}                            # own process group -> killable


def _alive(pid: int) -> bool:
    try:
        if WINDOWS:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                                 text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return str(pid) in out.stdout
        os.kill(pid, 0)          # signal 0 = existence check
        return True
    except (OSError, ValueError):
        return False


def current() -> dict | None:
    """The running task, or the last finished one (with running=False)."""
    if not STATE.exists():
        return None
    try:
        t = json.loads(STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    was_running = bool(t.get("running"))
    t["running"] = _alive(t["pid"]) if was_running else False
    if not t["running"] and t.get("ended") is None:
        t["ended"] = datetime.now().isoformat(timespec="seconds")
        STATE.write_text(json.dumps(t), encoding="utf-8")
        if was_running and t.get("history_id"):
            try:
                from jobagent import db as _db
                _db.record_task_end(t["history_id"])
            except Exception:                                 # noqa: BLE001
                pass
    return t


def start(label: str, *args: str) -> dict:
    t = current()
    if t and t["running"]:
        raise RuntimeError(f"'{t['label']}' is still running - wait for it or stop it first.")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = LOG_DIR / f"{stamp}_{args[0]}.log"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
           "NO_COLOR": "1", "COLUMNS": "140", "TERM": "dumb"}
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "jobagent", *args], cwd=ROOT, env=env,
            stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            **_spawn_flags(),
        )
    t = {"label": label, "args": list(args), "pid": proc.pid, "log": str(log), "running": True,
         "started": datetime.now().isoformat(timespec="seconds"), "ended": None}
    STATE.write_text(json.dumps(t), encoding="utf-8")
    # Phase 17.11: persistent task history (survives restarts, feeds /api/tasks)
    try:
        from jobagent import db as _db
        t["history_id"] = _db.record_task_start(label, list(args), proc.pid, str(log))
    except Exception:                                         # noqa: BLE001 - history is best-effort
        t["history_id"] = None
    return t


def stop() -> None:
    t = current()
    if t and t["running"]:
        if WINDOWS:
            # /T also ends child processes (browser, LLM CLI).
            subprocess.run(["taskkill", "/PID", str(t["pid"]), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(os.getpgid(t["pid"]), signal.SIGTERM)   # children too
            except OSError:
                try:
                    os.kill(t["pid"], signal.SIGTERM)
                except OSError:
                    pass
        with open(t["log"], "a", encoding="utf-8") as fh:
            fh.write("\n[stopped from dashboard]\n")


def read_log(path: str, max_chars: int = 30000) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def send_apply_command(cmd: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTROL.write_text(cmd, encoding="utf-8")


# ---------------------------------------------------------------- optional auto-discovery loop

_loop_started = False


def start_auto_discovery() -> None:
    """Background thread that runs discover+score every N hours (search.autodiscover_hours,
    0/off = disabled). Single-worker by design; guarded by the same one-task-at-a-time rule."""
    global _loop_started
    hours = 0
    try:
        from jobagent.config import cfg
        hours = float(cfg("search.autodiscover_hours", 0) or 0)
    except Exception:
        return
    if hours <= 0 or _loop_started:
        return
    _loop_started = True

    import threading

    def loop() -> None:
        import time as _time
        while True:
            _time.sleep(hours * 3600)
            t = None
            try:
                t = start(f"Auto-discovery (every {hours:g}h)", "run")
            except RuntimeError:
                continue        # something else is running; skip this round
            if t:
                db_add_note("auto_discovery", "Scheduled discovery started")

    threading.Thread(target=loop, name="autodiscovery", daemon=True).start()


def db_add_note(kind: str, text: str) -> None:
    try:
        from jobagent import db
        db.add_notification("system", "Auto-discovery", text)
    except Exception:
        pass
