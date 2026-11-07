"""Black Box - a flight recorder for your PC.

It quietly records what your computer is doing and explains, in plain English,
why it was slow. Everything stays on your PC.
"""
import ctypes
import json
import math
import os
import sqlite3
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
from datetime import datetime

import psutil

try:  # crisp text on high-DPI screens
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

VERSION = "1.0.0"
FONT = "Segoe UI"
W, H, PAD = 760, 640, 28
ACCENT, WARN, CRIT, OK = "#6ea8fe", "#f6c177", "#eb6f92", "#6fd9a0"
MEM_COLOR, DISK_COLOR = "#b79cff", "#ffb86b"
SELF = "blackbox"
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "BlackBox")
DB_PATH = os.path.join(DATA_DIR, "history.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

THEMES = {
    "Dark": dict(bg="#0f1115", track="#1c2028", calm="#2a3140", text="#e8eaed", muted="#6f7685"),
    "Light": dict(bg="#f6f7f9", track="#e3e6eb", calm="#cfd6e2", text="#1b1e24", muted="#7a8190"),
}
DEFAULTS = {
    "record_enabled": True, "interval": 5, "keep_days": 3,
    "th_cpu": 90, "th_mem": 90, "th_disk": 90,
    "theme": "Dark", "close_minimizes": True, "start_with_windows": False, "view_hours": 24,
}
RANGES = [(1, "LAST HOUR"), (6, "LAST 6 HOURS"), (24, "LAST 24 HOURS"), (72, "LAST 3 DAYS")]
TOGGLE, CHOICE, ACTION = "toggle", "choice", "action"
PCT = [(80, "80%"), (90, "90%"), (95, "95%")]
SETTING_ROWS = [
    ("Recording", "record_enabled", TOGGLE, None),
    ("Record every", "interval", CHOICE, [(2, "2 s"), (5, "5 s"), (10, "10 s"), (30, "30 s")]),
    ("Keep history for", "keep_days", CHOICE, [(1, "1 day"), (3, "3 days"), (7, "7 days"), (14, "14 days")]),
    ("Count CPU as overloaded above", "th_cpu", CHOICE, PCT),
    ("Count memory as overloaded above", "th_mem", CHOICE, PCT),
    ("Count disk as overloaded above", "th_disk", CHOICE, PCT),
    ("Theme", "theme", CHOICE, [(k, k) for k in THEMES]),
    ("Close button minimizes instead of quitting", "close_minimizes", TOGGLE, None),
    ("Start with Windows (minimized)", "start_with_windows", TOGGLE, None),
    ("Open data folder", "open_folder", ACTION, None),
    ("Clear recorded history", "clear", ACTION, None),
]

# What common programs are doing, in plain English: (description, what helps)
_AV = "Scheduled scans can be moved to a time you're away."
_BROWSER = "Closing unused tabs, or the browser's own task manager, finds the heavy ones."
_SYNC = "Pausing sync from its tray icon helps while you work."
_CHAT = "It uses a lot of memory; fully quitting it when you're not using it frees that."
_UPDATE = "Let it finish, then restart your PC to complete the update."
KNOWN = {
    "msmpeng": ("Windows Defender scanning your files", "This is normal and usually finishes on its own. " + _AV),
    "tiworker": ("Windows Update installing updates", _UPDATE),
    "trustedinstaller": ("Windows Update installing updates", _UPDATE),
    "searchindexer": ("Windows Search indexing your files", "Indexing settles down by itself after big file changes."),
    "searchprotocolhost": ("Windows Search indexing your files", "Indexing settles down by itself after big file changes."),
    "onedrive": ("OneDrive syncing files", _SYNC),
    "dropbox": ("Dropbox syncing files", _SYNC),
    "chrome": ("Chrome with all its tabs and extensions", _BROWSER),
    "msedge": ("Edge with all its tabs and extensions", _BROWSER),
    "firefox": ("Firefox with all its tabs and extensions", _BROWSER),
    "brave": ("Brave with all its tabs and extensions", _BROWSER),
    "opera": ("Opera with all its tabs and extensions", _BROWSER),
    "msedgewebview2": ("an app's built-in web view (used by Teams, Widgets and others)",
                       "Restarting the app that uses it usually clears it."),
    "memcompression": ("Windows compressing memory because RAM is nearly full",
                       "Closing big programs frees memory and stops this."),
    "system": ("the Windows kernel: drivers plus disk and network activity",
               "If this stays high, updating drivers is a good first step."),
    "svchost": ("Windows background services",
                "Task Manager's Services tab shows which one; Windows Update and Search are the usual ones."),
    "dwm": ("the Windows desktop and graphics compositor",
            "High use here often points to graphics drivers or lots of animations and monitors."),
    "explorer": ("Windows File Explorer and the desktop", "Restarting Explorer from Task Manager can clear a stuck one."),
    "steam": ("Steam", "Pause downloads and game updates while you work."),
    "steamwebhelper": ("Steam's built-in browser", "Pause downloads and game updates while you work."),
    "discord": ("Discord", "Closing it, or turning off hardware acceleration in its settings, helps."),
    "vmmem": ("a virtual machine or WSL holding memory", "Running 'wsl --shutdown' releases the memory."),
    "vmmemwsl": ("WSL holding memory", "Running 'wsl --shutdown' releases the memory."),
    "bdservicehost": ("Bitdefender scanning", _AV),
    "vsserv": ("Bitdefender scanning", _AV),
    "avgsvc": ("AVG antivirus scanning", _AV),
    "avastsvc": ("Avast antivirus scanning", _AV),
    "mbamservice": ("Malwarebytes scanning", _AV),
    "wmiprvse": ("Windows management queries, often from monitoring tools",
                 "Closing monitoring or hardware tools usually stops it."),
    "compattelrunner": ("Windows compatibility and telemetry scan", "It finishes by itself."),
    "msiexec": ("a program being installed or updated", "Let the installer finish."),
    "googleupdate": ("Google updating itself", "It finishes by itself."),
    "microsoftedgeupdate": ("Edge updating itself", "It finishes by itself."),
    "ms-teams": ("Microsoft Teams", _CHAT),
    "teams": ("Microsoft Teams", _CHAT),
    "slack": ("Slack", _CHAT),
    "code": ("Visual Studio Code", "Closing unused windows and extensions helps."),
}


# ---- small helpers ---------------------------------------------------------
def label(name):
    known = KNOWN.get(name.lower())
    return f"{name} ({known[0]})" if known else name


def fmt_mb(mb):
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def fmt_dur(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec} s"
    if sec < 3600:
        return f"{round(sec / 60)} min"
    return f"{sec // 3600} h {round(sec % 3600 / 60)} min"


def fmt_time(ts):
    d, today = datetime.fromtimestamp(ts), datetime.now().date()
    if d.date() == today:
        day = "Today"
    elif (today - d.date()).days == 1:
        day = "Yesterday"
    else:
        day = d.strftime("%b %d")
    return f"{day} {d:%H:%M}"


def fmt_clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def clip(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def est_height(text, size, width=W - 2 * PAD):
    """Rough height of wrapped canvas text (the canvas can't tell us before drawing)."""
    per_line = max(20, int(width / (size * 0.62) * 0.92))
    return math.ceil(max(len(text), 1) / per_line) * int(size * 1.75)


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for k in DEFAULTS:
            if k in data:
                s[k] = data[k]
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


def set_startup(enable):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        if enable:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}" --minimized'
            else:
                cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}" --minimized'
            winreg.SetValueEx(key, "BlackBox", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "BlackBox")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass


def dark_title_bar(root, dark=True):
    try:
        root.update()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


# ---- storage ----------------------------------------------------------------
def open_db(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS samples (
        ts REAL PRIMARY KEY, cpu REAL, mem REAL, swap REAL, disk REAL,
        rd REAL, wr REAL, dn REAL, up REAL, procs TEXT)""")
    return db


def load(db, t0, t1, procs=False):
    cols = "ts,cpu,mem,swap,disk,rd,wr,dn,up" + (",procs" if procs else "")
    names = cols.split(",")
    out = []
    for row in db.execute(f"SELECT {cols} FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts", (t0, t1)):
        r = dict(zip(names, row))
        if procs:
            try:
                r["procs"] = json.loads(r["procs"] or "[]")
            except ValueError:
                r["procs"] = []
        out.append(r)
    return out


# ---- recording ----------------------------------------------------------------
class _Fmt(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_uint32), ("value", ctypes.c_double)]


class DiskBusy:
    """How busy the disk is (like Task Manager's 'Active time'), via Windows performance counters."""
    PATH = r"\PhysicalDisk(_Total)\% Idle Time"

    def __init__(self):
        self.ok, self.query, self.handle = os.name == "nt", None, None

    def read(self):
        if not self.ok:
            return None
        try:
            from ctypes import POINTER, byref, c_size_t, c_uint32, c_void_p, c_wchar_p
            pdh = ctypes.windll.pdh
            if self.query is None:
                pdh.PdhOpenQueryW.argtypes = [c_wchar_p, c_size_t, POINTER(c_void_p)]
                pdh.PdhAddEnglishCounterW.argtypes = [c_void_p, c_wchar_p, c_size_t, POINTER(c_void_p)]
                pdh.PdhCollectQueryData.argtypes = [c_void_p]
                pdh.PdhGetFormattedCounterValue.argtypes = [c_void_p, c_uint32, POINTER(c_uint32), POINTER(_Fmt)]
                q, h = c_void_p(), c_void_p()
                if pdh.PdhOpenQueryW(None, 0, byref(q)) != 0:
                    raise OSError("PDH unavailable")
                if pdh.PdhAddEnglishCounterW(q, self.PATH, 0, byref(h)) != 0:
                    raise OSError("no disk counter")
                self.query, self.handle = q, h
                pdh.PdhCollectQueryData(q)  # first sample; values start with the next read
                return None
            pdh.PdhCollectQueryData(self.query)
            val = _Fmt()
            if pdh.PdhGetFormattedCounterValue(self.handle, 0x200, None, byref(val)) != 0 or val.CStatus not in (0, 1):
                return None
            return max(0.0, min(100.0, 100.0 - val.value))
        except Exception:
            self.ok = False
            return None


class Recorder(threading.Thread):
    """Samples the PC every few seconds and stores it, until the app quits."""

    def __init__(self, get_settings, db_path=None):
        super().__init__(daemon=True)
        self.get, self.db_path = get_settings, db_path
        self.stop_flag = threading.Event()
        self.cores = psutil.cpu_count() or 1
        self.last_io, self.last_dio, self.last_net, self.last_t, self.n = {}, None, None, None, 0
        self.disk_busy = DiskBusy()

    def run(self):
        db = open_db(self.db_path)
        psutil.cpu_percent()
        while not self.stop_flag.is_set():
            s = self.get()
            if s["record_enabled"]:
                try:
                    self.sample(db, s)
                except Exception:
                    pass
            self.stop_flag.wait(max(1, s["interval"]))
        db.close()

    def scan(self, dt):
        agg, seen = {}, set()
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "io_counters"]):
            i = p.info
            name, pid = i.get("name"), i.get("pid")
            if not name or pid == 0:
                continue
            if name.lower().endswith(".exe"):
                name = name[:-4]
            io, rate = i.get("io_counters"), 0.0
            if io:
                total = io.read_bytes + io.write_bytes
                seen.add(pid)
                prev = self.last_io.get(pid)
                if prev is not None and total >= prev:
                    rate = (total - prev) / dt / 1048576
                self.last_io[pid] = total
            mi = i.get("memory_info")
            a = agg.setdefault(name, [0.0, 0.0, 0.0])
            a[0] += (i.get("cpu_percent") or 0.0) / self.cores
            a[1] += (mi.rss if mi else 0) / 1048576
            a[2] += rate
        self.last_io = {k: v for k, v in self.last_io.items() if k in seen}
        rows = [[n, round(a[0], 1), int(a[1]), round(a[2], 2)] for n, a in agg.items()]
        chosen = {}
        for idx, minimum in ((1, 1.0), (2, 1), (3, 0.5)):  # busiest by CPU, memory, I/O
            for r in sorted(rows, key=lambda r: r[idx], reverse=True)[:4]:
                if r[idx] >= minimum:
                    chosen[r[0]] = r
        return list(chosen.values())

    def sample(self, db, s):
        now = time.time()
        dt = max(now - self.last_t, 0.1) if self.last_t else 1.0
        cpu = psutil.cpu_percent()
        mem, swap = psutil.virtual_memory().percent, psutil.swap_memory().percent
        dio, net = psutil.disk_io_counters(), psutil.net_io_counters()
        rd = wr = dn = up = 0.0
        if self.last_t:
            if dio and self.last_dio:
                rd, wr = (dio.read_bytes - self.last_dio.read_bytes) / dt, (dio.write_bytes - self.last_dio.write_bytes) / dt
            if self.last_net:
                dn, up = (net.bytes_recv - self.last_net.bytes_recv) / dt, (net.bytes_sent - self.last_net.bytes_sent) / dt
        self.last_dio, self.last_net, self.last_t = dio, net, now
        procs = self.scan(dt)
        db.execute("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (now, cpu, mem, swap, self.disk_busy.read(), rd, wr, dn, up,
                    json.dumps(procs, separators=(",", ":"))))
        db.commit()
        self.n += 1
        if self.n % 360 == 1:  # roughly hourly
            db.execute("DELETE FROM samples WHERE ts < ?", (now - s["keep_days"] * 86400,))
            db.commit()


# ---- analysis (plain functions, easy to test) ---------------------------------------
def stress(r, th):
    s = max(r["cpu"] / th["cpu"], r["mem"] / th["mem"])
    if r["disk"] is not None:
        s = max(s, r["disk"] / th["disk"])
    return s


def bucketize(rows, th, t0, t1, n=96):
    size, levels = (t1 - t0) / n, [None] * n
    for r in rows:
        i = int((r["ts"] - t0) / size)
        if 0 <= i < n:
            s = stress(r, th)
            levels[i] = s if levels[i] is None else max(levels[i], s)
    return levels


def find_incidents(rows, th, interval):
    gap, found, cur = max(30, 3 * interval), [], None
    for r in rows:
        s = stress(r, th)
        if s < 1:
            continue
        if cur and r["ts"] - cur["end"] <= gap:
            cur["end"], cur["n"] = r["ts"], cur["n"] + 1
        else:
            if cur:
                found.append(cur)
            cur = {"start": r["ts"], "end": r["ts"], "n": 1}
    if cur:
        found.append(cur)
    return [i for i in found if i["n"] >= 2]  # a single spike isn't a slow moment


def top_by(table, idx, minimum):
    best = max(table, key=lambda t: t[idx], default=None)
    return best if best and best[idx] >= minimum else None


def explain(rows, th):
    """Plain-English verdict for a stretch of recorded samples."""
    if not rows:
        return {"headline": "No recording for this time.", "table": [], "stressed": [], "chip": None,
                "short": "No recording", "lines": ["Black Box only records while it's running. "
                                                    "Minimizing it is fine, quitting it stops recording."]}
    n = len(rows)
    peak = {"cpu": max(r["cpu"] for r in rows), "mem": max(r["mem"] for r in rows)}
    disks = [r["disk"] for r in rows if r["disk"] is not None]
    peak["disk"] = max(disks) if disks else None
    swap = max((r["swap"] or 0) for r in rows)
    agg = {}
    for r in rows:
        for name, cpu, rss, io in r.get("procs", []):
            if name.lower() == SELF:
                continue
            a = agg.setdefault(name, [0.0, 0, 0.0])
            a[0] += cpu
            a[1] = max(a[1], rss)
            a[2] += io
    table = [(nm, a[0] / n, a[1], a[2] / n) for nm, a in agg.items()]
    table.sort(key=lambda t: t[1] + t[3] * 0.5 + t[2] / 512, reverse=True)
    stressed = sorted((k for k in ("disk", "cpu", "mem") if peak[k] is not None and peak[k] >= th[k]),
                      key=lambda k: peak[k] / th[k], reverse=True)
    lines, culprits = [], []
    if not stressed:
        avg = lambda key: sum(r[key] for r in rows) / n
        text = f"Average CPU {avg('cpu'):.0f}%, memory {avg('mem'):.0f}%"
        if disks:
            text += f", disk {sum(disks) / len(disks):.0f}% busy"
        lines.append(text + ".")
        if table:
            t = table[0]
            lines.append(f"The busiest program was {label(t[0])} (CPU {t[1]:.0f}%, memory {fmt_mb(t[2])}).")
        return {"headline": "Nothing stands out.", "lines": lines, "table": table, "stressed": [],
                "chip": None, "short": "Nothing stands out"}
    if "disk" in stressed:
        t = top_by(table, 3, 1.0)
        if t:
            lines.append(f"{label(t[0])} was doing most of the disk activity (about {t[3]:.0f} MB/s).")
            culprits.append(t[0])
        else:
            lines.append("Your disk was busy but no single program stood out. That usually means Windows itself "
                         "(updates, indexing) or a drive that is nearly full or failing.")
    if "mem" in stressed:
        t = top_by(table, 2, 1)
        if t:
            lines.append(f"{label(t[0])} was holding {fmt_mb(t[2])} of memory.")
            if t[0] not in culprits:
                culprits.append(t[0])
        if swap >= 40 or "disk" in stressed:
            lines.append("When memory fills up, Windows pushes data out to the disk, which is far slower, "
                         "so the two problems feed each other and everything feels stuck.")
    if "cpu" in stressed:
        t = top_by(table, 1, 20)
        if t:
            lines.append(f"{label(t[0])} was using about {t[1]:.0f}% of your CPU.")
            if t[0] not in culprits:
                culprits.append(t[0])
        else:
            lines.append("Your CPU was maxed out but no single program stood out. Many small tasks added up.")
    for name in culprits:
        known = KNOWN.get(name.lower())
        if known:
            lines.append("What helps: " + known[1])
            break
    else:
        if culprits:
            lines.append(f"What helps: if this keeps happening, closing {culprits[0]} while you work is the quickest test.")
    words = {"disk": f"disk was up to {peak['disk'] or 0:.0f}% busy", "cpu": f"CPU reached {peak['cpu']:.0f}%",
             "mem": f"memory reached {peak['mem']:.0f}%"}
    parts = [words[k] for k in stressed]
    headline = (", ".join(parts[:-1]) + " and " if len(parts) > 1 else "") + parts[-1]
    headline = headline[0].upper() + headline[1:] + "."
    main = stressed[0]
    verb = {"disk": "hammering the disk", "mem": "filling up memory", "cpu": "maxing out the CPU"}[main]
    t = top_by(table, {"disk": 3, "mem": 2, "cpu": 1}[main], 0.5)
    short = f"{label(t[0])} was {verb}" if t else f"Heavy {'disk' if main == 'disk' else 'CPU' if main == 'cpu' else 'memory'} use, no single cause"
    return {"headline": headline, "lines": lines, "table": table, "stressed": stressed,
            "chip": (main, peak[main]), "short": short}


# ---- app ----------------------------------------------------------------------------
class App:
    def __init__(self, root, minimized=False, db_path=None):
        self.root = root
        self.s = load_settings()
        self.view, self.hits, self.detail = "main", [], None
        self.rows, self.levels, self.incidents, self.inc_cache = [], [], [], {}
        self.t0 = self.t1 = 0.0
        root.title("Black Box")
        root.geometry(f"{W}x{H}")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.hfont = tkfont.Font(root=root, family=FONT, size=9, weight="bold")
        self.c = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0)
        self.c.pack()
        self.c.bind("<Button-1>", self.on_click)
        self.c.bind("<Button-3>", lambda e: self.on_click(e, -1))
        self.db = open_db(db_path)
        self.rec = Recorder(lambda: self.s, db_path)
        self.rec.start()
        self.apply_window()
        dark_title_bar(root, self.s["theme"] != "Light")
        if minimized:
            root.iconify()
        self.tick()

    @property
    def t(self):
        return THEMES.get(self.s["theme"], THEMES["Dark"])

    @property
    def th(self):
        return {"cpu": self.s["th_cpu"], "mem": self.s["th_mem"], "disk": self.s["th_disk"]}

    def apply_window(self):
        self.root.configure(bg=self.t["bg"])
        self.c.configure(bg=self.t["bg"])

    # -- data --
    def refresh(self):
        now = time.time()
        self.t0, self.t1 = now - self.s["view_hours"] * 3600, now
        self.rows = load(self.db, self.t0, self.t1)
        self.levels = bucketize(self.rows, self.th, self.t0, self.t1)
        found = find_incidents(self.rows, self.th, self.s["interval"])[::-1][:5]
        iv = self.s["interval"]
        for inc in found:
            key = (inc["start"], inc["end"], tuple(self.th.values()))
            if key not in self.inc_cache:
                window = load(self.db, inc["start"] - iv, inc["end"] + iv, procs=True)
                self.inc_cache[key] = explain(window, self.th)
            inc["info"] = self.inc_cache[key]
        self.incidents = found
        if len(self.inc_cache) > 200:
            self.inc_cache.clear()

    def tick(self):
        try:
            if self.root.state() != "iconic":
                self.refresh()
                if self.view == "main":
                    self.draw()
        except sqlite3.Error:
            pass
        self.root.after(5000, self.tick)

    def open_range(self, c0, c1, title, focus=None):
        rows = load(self.db, c0, c1, procs=True)
        inside = [r for r in rows if focus[0] <= r["ts"] <= focus[1]] if focus else rows
        self.detail = {"title": title, "c0": c0, "c1": c1, "rows": rows, "focus": focus,
                       "info": explain(inside, self.th)}
        self.view = "detail"
        self.draw()

    def ask_now(self, *_):
        now = time.time()
        self.open_range(now - 120, now, "Right now (the last 2 minutes)")

    def open_incident(self, inc):
        iv = self.s["interval"]
        title = f"{fmt_time(inc['start'])}, {fmt_dur(inc['end'] - inc['start'] + iv)}"
        self.open_range(inc["start"] - 120, inc["end"] + 120, title, (inc["start"] - iv, inc["end"] + iv))

    def open_bucket(self, ev, _d=1):
        size = (self.t1 - self.t0) / 96
        i = max(0, min(95, int((ev.x - PAD) / ((W - 2 * PAD) / 96))))
        mid = self.t0 + (i + 0.5) * size
        half = max(size / 2, 60)
        self.open_range(mid - half, mid + half, f"{fmt_time(mid - half)} to {fmt_clock(mid + half)}")

    # -- interaction --
    def on_click(self, event, direction=1):
        for x0, y0, x1, y1, cb in reversed(self.hits):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                cb(event, direction)
                return

    def on_close(self):
        if self.s["close_minimizes"]:
            self.root.iconify()
        else:
            self.quit()

    def quit(self, *_):
        self.rec.stop_flag.set()
        save_settings(self.s)
        try:
            self.db.close()
        except sqlite3.Error:
            pass
        self.root.destroy()

    def go(self, view):
        self.view = view
        self.draw()

    def set_value(self, key, val):
        self.s[key] = val
        save_settings(self.s)
        if key == "start_with_windows":
            set_startup(val)
        elif key == "theme":
            self.apply_window()
            dark_title_bar(self.root, val != "Light")
        self.refresh()
        self.draw()

    def setting_action(self, key, kind, opts, direction):
        if kind == TOGGLE:
            self.set_value(key, not self.s[key])
        elif kind == CHOICE:
            values = [v for v, _ in opts]
            i = values.index(self.s[key]) if self.s[key] in values else -1
            self.set_value(key, values[(i + direction) % len(values)])
        elif key == "open_folder":
            os.makedirs(DATA_DIR, exist_ok=True)
            try:
                os.startfile(DATA_DIR)
            except Exception:
                pass
        elif key == "clear":
            if messagebox.askyesno("Clear history", "Delete everything Black Box has recorded?"):
                self.db.execute("DELETE FROM samples")
                self.db.commit()
                self.inc_cache.clear()
                self.refresh()
                self.draw()

    # -- drawing helpers --
    def header(self, buttons, status=True):
        c, t = self.c, self.t
        c.create_text(PAD, 30, text="BLACK BOX", anchor="w", fill=t["muted"], font=self.hfont)
        if status:
            on = self.s["record_enabled"]
            c.create_text(PAD + self.hfont.measure("BLACK BOX") + 16, 30, anchor="w",
                          text="● RECORDING" if on else "○ PAUSED", fill=OK if on else t["muted"], font=self.hfont)
        x = W - PAD
        for text, cb, color in buttons:
            w = self.hfont.measure(text)
            c.create_text(x, 30, text=text, anchor="e", fill=color, font=self.hfont)
            self.hits.append((x - w - 6, 8, x + 6, 52, cb))
            x -= w + 20

    def section(self, y, text, right=None, right_cb=None):
        c, t = self.c, self.t
        c.create_text(PAD, y, text=text, anchor="w", fill=t["muted"], font=self.hfont)
        if right:
            w = self.hfont.measure(right)
            c.create_text(W - PAD, y, text=right, anchor="e", fill=ACCENT if right_cb else t["muted"], font=self.hfont)
            if right_cb:
                self.hits.append((W - PAD - w - 8, y - 12, W - PAD + 8, y + 12, right_cb))

    def level_color(self, lv):
        t = self.t
        if lv is None:
            return t["track"]
        return t["calm"] if lv < 0.5 else ACCENT if lv < 0.8 else WARN if lv < 1 else CRIT

    def cycle_range(self, *_):
        hours = [h for h, _ in RANGES]
        cur = self.s["view_hours"]
        self.set_value("view_hours", hours[(hours.index(cur) + 1) % len(hours)] if cur in hours else 24)

    def toggle_recording(self, *_):
        self.set_value("record_enabled", not self.s["record_enabled"])

    # -- views --
    def draw(self):
        self.c.delete("all")
        self.hits = []
        {"main": self.draw_main, "detail": self.draw_detail, "settings": self.draw_settings}[self.view]()

    def draw_main(self):
        c, t = self.c, self.t
        self.header([("SETTINGS", lambda e, d: self.go("settings"), t["muted"]),
                     ("PAUSE" if self.s["record_enabled"] else "RESUME", lambda e, d: self.toggle_recording(), ACCENT),
                     ("QUIT", lambda e, d: self.quit(), t["muted"])])
        # the big question
        c.create_rectangle(PAD, 58, W - PAD, 106, fill=ACCENT, outline=ACCENT)
        c.create_text(W // 2, 82, text="Why is my PC slow right now?", fill="#0b0d12", font=(FONT, 13, "bold"))
        self.hits.append((PAD, 58, W - PAD, 106, lambda e, d: self.ask_now()))
        c.create_text(PAD, 124, anchor="w", fill=t["muted"], font=(FONT, 9),
                      text="Looks at the last 2 minutes of recorded activity and tells you what's using your PC.")
        # timeline
        label_ = dict(RANGES).get(self.s["view_hours"], "LAST 24 HOURS")
        self.section(158, "TIMELINE", label_ + "  ▾", lambda e, d: self.cycle_range())
        sw = (W - 2 * PAD) / 96
        for i, lv in enumerate(self.levels or [None] * 96):
            c.create_rectangle(PAD + i * sw, 178, PAD + (i + 1) * sw - 1, 224, fill=self.level_color(lv), outline="")
        self.hits.append((PAD, 178, W - PAD, 224, self.open_bucket))
        c.create_text(PAD, 238, text=fmt_time(self.t0) if self.t0 else "", anchor="w", fill=t["muted"], font=(FONT, 9))
        c.create_text(W - PAD, 238, text="now", anchor="e", fill=t["muted"], font=(FONT, 9))
        c.create_text(W // 2, 238, text="click any part of the timeline to see what happened", fill=t["muted"], font=(FONT, 9))
        # slow moments
        self.section(278, "SLOW MOMENTS", f"{len(self.incidents)} found" if self.incidents else None)
        y = 306
        if not self.incidents:
            msg = ("No slow moments in this period." if self.rows
                   else "Recording has just started. Slow moments will show up here.")
            c.create_text(PAD, y + 8, text=msg, anchor="w", fill=t["muted"], font=(FONT, 10))
        for inc in self.incidents:
            info = inc["info"]
            dur = inc["end"] - inc["start"] + self.s["interval"]
            c.create_text(PAD, y, text=fmt_time(inc["start"]), anchor="w", fill=t["text"], font=(FONT, 10, "bold"))
            c.create_text(PAD + 130, y, text=fmt_dur(dur), anchor="w", fill=t["muted"], font=(FONT, 10))
            if info["chip"]:
                key, pk = info["chip"]
                c.create_text(W - PAD, y, anchor="e", font=self.hfont, fill=CRIT if pk >= 97 else WARN,
                              text=f"{ {'disk': 'DISK', 'cpu': 'CPU', 'mem': 'MEMORY'}[key] } {pk:.0f}%")
            c.create_text(PAD, y + 20, text=clip(info["short"], 105), anchor="w", fill=t["muted"], font=(FONT, 10))
            self.hits.append((PAD, y - 14, W - PAD, y + 32, lambda e, d, i=inc: self.open_incident(i)))
            y += 56
        # footer tip
        path = (os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else "/"
        try:
            used = psutil.disk_usage(path).percent
        except OSError:
            used = 0
        name = path.rstrip("\\/") or path
        if used >= 85:
            c.create_text(PAD, H - 28, anchor="w", fill=WARN, font=(FONT, 10),
                          text=f"Drive {name} is {used:.0f}% full. A nearly full system drive slows Windows down on its own.")
        else:
            c.create_text(PAD, H - 28, anchor="w", fill=t["muted"], font=(FONT, 9),
                          text=f"{len(self.rows):,} samples in this view. Recording works while the window is minimized.")

    def draw_detail(self):
        c, t, d = self.c, self.t, self.detail
        self.header([("BACK", lambda e, k: self.go("main"), ACCENT)], status=False)
        info, y = d["info"], 62
        c.create_text(PAD, y, text=d["title"], anchor="w", fill=t["text"], font=(FONT, 14, "bold"))
        y += 28
        c.create_text(PAD, y, text=info["headline"], anchor="nw", width=W - 2 * PAD, font=(FONT, 11, "bold"),
                      fill=CRIT if info["stressed"] else t["text"])
        y += est_height(info["headline"], 11) + 6
        for line in info["lines"]:
            c.create_text(PAD, y, text=line, anchor="nw", width=W - 2 * PAD, fill=t["text"], font=(FONT, 10))
            y += est_height(line, 10) + 6
        y = self.chart(y + 8, d)
        self.table(y + 14, info["table"])

    def chart(self, y, d):
        c, t, h = self.c, self.t, 120
        x0, x1 = PAD, W - PAD
        span = max(d["c1"] - d["c0"], 1)
        c.create_rectangle(x0, y, x1, y + h, fill=t["track"], outline="")
        if d["focus"]:
            fa = x0 + (d["focus"][0] - d["c0"]) / span * (x1 - x0)
            fb = x0 + (d["focus"][1] - d["c0"]) / span * (x1 - x0)
            c.create_rectangle(max(x0, fa), y, min(x1, max(fb, fa + 3)), y + h, fill=t["calm"], outline="")
        for key, color in (("cpu", ACCENT), ("mem", MEM_COLOR), ("disk", DISK_COLOR)):
            pts = []
            for r in d["rows"]:
                if r[key] is not None:
                    pts += [x0 + (r["ts"] - d["c0"]) / span * (x1 - x0), y + h - min(r[key], 100) / 100 * h]
            if len(pts) >= 4:
                c.create_line(*pts, width=2, fill=color)
        lx = x1
        for text, color in (("DISK", DISK_COLOR), ("MEMORY", MEM_COLOR), ("CPU", ACCENT)):
            w = self.hfont.measure(text)
            c.create_text(lx - 6, y + 12, text=text, anchor="e", fill=color, font=self.hfont)
            lx -= w + 18
        c.create_text(x0, y + h + 12, text=fmt_clock(d["c0"]), anchor="w", fill=t["muted"], font=(FONT, 9))
        c.create_text(x1, y + h + 12, text=fmt_clock(d["c1"]), anchor="e", fill=t["muted"], font=(FONT, 9))
        return y + h + 12

    def table(self, y, table):
        c, t = self.c, self.t
        room = int((H - 24 - y - 26) // 22)
        if not table or room < 1:
            return
        for text, x, anchor in (("PROGRAM", PAD, "w"), ("WHAT IT IS", 200, "w"), ("CPU", 560, "e"),
                                ("MEMORY", 650, "e"), ("I/O", W - PAD, "e")):
            c.create_text(x, y, text=text, anchor=anchor, fill=t["muted"], font=self.hfont)
        y += 24
        for name, cpu, rss, io in table[:min(6, room)]:
            known = KNOWN.get(name.lower())
            c.create_text(PAD, y, text=clip(name, 20), anchor="w", fill=t["text"], font=(FONT, 10))
            c.create_text(200, y, text=clip(known[0], 44) if known else "", anchor="w", fill=t["muted"], font=(FONT, 9))
            c.create_text(560, y, text=f"{cpu:.0f}%", anchor="e", fill=t["text"], font=(FONT, 10))
            c.create_text(650, y, text=fmt_mb(rss), anchor="e", fill=t["text"], font=(FONT, 10))
            c.create_text(W - PAD, y, text=f"{io:.1f} MB/s", anchor="e", fill=t["text"], font=(FONT, 10))
            y += 22

    def draw_settings(self):
        c, t = self.c, self.t
        self.header([("BACK", lambda e, d: self.go("main"), ACCENT)], status=False)
        c.create_text(PAD, 62, text="Settings", anchor="w", fill=t["text"], font=(FONT, 14, "bold"))
        y = 112
        for text, key, kind, opts in SETTING_ROWS:
            c.create_text(PAD, y, text=text, anchor="w", fill=ACCENT if kind == ACTION else t["text"], font=(FONT, 10))
            if kind == TOGGLE:
                on, cx = self.s[key], W - PAD - 16
                c.create_line(cx - 14, y, cx + 14, y, width=18, capstyle="round", fill=ACCENT if on else t["calm"])
                kx = cx + 7 if on else cx - 7
                c.create_oval(kx - 6, y - 6, kx + 6, y + 6, fill="#ffffff" if on else t["muted"], outline="")
            elif kind == CHOICE:
                shown = next((lab for v, lab in opts if v == self.s[key]), str(self.s[key]))
                c.create_text(W - PAD, y, text=shown, anchor="e", fill=ACCENT, font=(FONT, 10))
            self.hits.append((0, y - 17, W, y + 17,
                              lambda e, d, k=key, kd=kind, o=opts: self.setting_action(k, kd, o, d)))
            y += 38
        c.create_text(PAD, H - 28, anchor="w", fill=t["muted"], font=(FONT, 9),
                      text=f"Black Box v{VERSION}. Click a value to change it, right-click to step back. "
                           "Everything is stored only on this PC.")


def main():
    root = tk.Tk()
    App(root, minimized="--minimized" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
