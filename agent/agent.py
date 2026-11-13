#!/usr/bin/env python3
"""JarvisOS agent core v0.5 — zero-dependency (Python stdlib only).

Jarvis's whole computer in one small process:
  GET  /healthz      ok check (no auth)
  GET  /stats        cpu/mem/disk/load/uptime/net as JSON
  POST /exec         run a shell command in Jarvis's workspace —
                     bubblewrap-sandboxed when bwrap exists, RSS
                     watchdog kills the tree past the cap
  POST /task         create a journalled task {title, steps[]}
  GET  /tasks        list tasks + step states
  POST /task/step    mark a step {id, step, status, note}
  POST /task/status  set task status {id, status}
  GET  /files        list workspace files (?path=relative)
  GET  /desktop.png  the drawn Nebula-style face frame
  GET  /wallpaper.jpg, /face, /   the interactive face
  POST /sync         commit + push the workspace now
  POST /watch        {url} start Moli (visual browser) / navigate
  GET  /watch        live-view session status (+ viewport/frame)
  GET  /watch.png    latest screencast frame (JPEG)
  POST /watch/stop   stop the live-view session
  POST /watch/input  {kind: click|type|key|scroll, ...} — eres
                     takes the wheel: input forwarded into the
                     page over the same CDP session
  GET  /download     ?path= workspace file, with HTTP Range
                     (seekable media, phone downloads)
  GET  /music/search ?q= YouTube candidates for the music panel
  POST /music/play   {query|url|videoId, hints...} resolve a song
                     (Spotify links matched SpotDL-style), start
                     its cache download, return stream/play URLs,
                     palette and synced lyrics
  GET  /stream       ?id= progressive play of a song still caching
  GET  /music/status ?id= cache state for one song
  GET  /music/list   the cached library
  GET  /music/lyrics ?videoId=&title=&artist=&duration= LRCLIB

v0.4 adds the watch layer: Moli (Apache-2.0, Rust headless
browser with on-demand layout) runs `serve --layout` on demand;
the agent drives it over CDP with a stdlib WebSocket client and
relays its screencast frames (emitted on change) to the face.
The session auto-stops after 15 min without a viewer.
v0.4.2 adds take-control (the pattern open-source cockpit
projects use: screencast out, CDP Input events back in) and
shortens the session socket timeout so commands land faster.

Workspace persistence: if JARVIS_GIT_REPO is set, the workspace is
cloned/pulled at boot and auto-committed+pushed (debounced) after
work — the Render disk is disposable, the git repo is home.
Auth: X-Jarvis-Token header must equal JARVIS_PC_TOKEN.

v0.5 is the music release ("a perfect one to enjoy music"):
songs resolve through the tools already proven on this box —
Invidious for the catalogue, Cobalt for the audio bytes, a
SpotDL-grade matcher (fuzzy title/artist, exp duration curve,
forbidden-words penalty) so a pasted Spotify link lands on the
right version — then stream progressively while a background
thread caches the MP3; replays are instant and seekable via
/download's Range support. Lyrics come from LRCLIB (line-synced;
the face interpolates word timing Verci-style) and the player
glow palette is extracted from the album art with Pillow (the
one non-stdlib piece, already in the image for the shell).
"""
import base64
import difflib
import json
import math
import os
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

PORT = int(os.environ.get("PORT", "10000"))
TOKEN = os.environ.get("JARVIS_PC_TOKEN", "")
WORKSPACE = os.path.abspath(os.environ.get("JARVIS_WORKSPACE", os.path.expanduser("~/workspace")))
GIT_REPO = os.environ.get("JARVIS_GIT_REPO", "")
GIT_TOKEN = os.environ.get("JARVIS_GIT_TOKEN", "")
EXEC_CAP_MB = int(os.environ.get("JARVIS_EXEC_CAP_MB", "250"))
MOLI_BIN = os.environ.get("JARVIS_MOLI_BIN", "moli")
WATCH_PORT = int(os.environ.get("JARVIS_WATCH_PORT", "9223"))
WATCH_IDLE_S = 900
HERE = os.path.dirname(os.path.abspath(__file__))
SHELL_DIR = os.path.join(HERE, "..", "shell")
sys.path.insert(0, SHELL_DIR)

os.makedirs(WORKSPACE, exist_ok=True)
TASKS_FILE = os.path.join(WORKSPACE, ".jarvis", "tasks.json")

# ---------------------------------------------------------------- git sync
_sync_timer = None
_sync_lock = threading.Lock()


def _git(args, timeout=120):
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Jarvis", "GIT_AUTHOR_EMAIL": "jarvis@localhost",
        "GIT_COMMITTER_NAME": "Jarvis", "GIT_COMMITTER_EMAIL": "jarvis@localhost",
    })
    cmd = ["git"]
    if GIT_TOKEN:
        b64 = base64.b64encode(f"x-access-token:{GIT_TOKEN}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=AUTHORIZATION: basic {b64}"]
    cmd += args
    return subprocess.run(cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=timeout, env=env)


def boot_sync():
    if not GIT_REPO:
        print("git sync: no JARVIS_GIT_REPO set, workspace is local-only", flush=True)
        return
    try:
        if os.path.isdir(os.path.join(WORKSPACE, ".git")):
            r = _git(["pull", "--ff-only"])
            print(f"git sync: pull -> {r.returncode} {r.stderr.strip()[:120]}", flush=True)
        elif not os.listdir(WORKSPACE):
            r = _git(["clone", GIT_REPO, "."])
            print(f"git sync: clone -> {r.returncode} {r.stderr.strip()[:120]}", flush=True)
        else:
            _git(["init"])
            _git(["remote", "add", "origin", GIT_REPO])
            r = _git(["fetch", "origin"])
            branches = _git(["branch", "-r"]).stdout
            if branches.strip():
                _git(["checkout", "-f", "--track", "origin/HEAD"])
                print("git sync: adopted remote workspace", flush=True)
            else:
                _git(["add", "-A"])
                _git(["commit", "-m", "jarvis: boot snapshot"])
                r = _git(["push", "-u", "origin", "HEAD"])
                print(f"git sync: first push -> {r.returncode}", flush=True)
    except Exception as e:
        print(f"git sync: boot failed ({e}); continuing local-only", flush=True)


def schedule_sync(msg="jarvis: work step"):
    if not GIT_REPO or not os.path.isdir(os.path.join(WORKSPACE, ".git")):
        return

    def _run():
        with _sync_lock:
            try:
                _git(["add", "-A"])
                if _git(["status", "--porcelain"]).stdout.strip():
                    _git(["commit", "-m", msg])
                    _git(["push"])
            except Exception:
                pass

    global _sync_timer
    if _sync_timer:
        _sync_timer.cancel()
    _sync_timer = threading.Timer(5.0, _run)
    _sync_timer.daemon = True
    _sync_timer.start()


# ---------------------------------------------------------------- stats
def read_cpu_times():
    with open("/proc/stat") as f:
        vals = [int(x) for x in f.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return idle, sum(vals)


def read_net_bytes():
    total = 0
    with open("/proc/net/dev") as f:
        for line in f:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = rest.split()
            total += int(fields[0]) + int(fields[8])
    return total


def get_stats():
    idle1, tot1 = read_cpu_times()
    net1, t1 = read_net_bytes(), time.time()
    time.sleep(0.15)
    idle2, tot2 = read_cpu_times()
    cpu_pct = round(100.0 * (1 - (idle2 - idle1) / (tot2 - tot1)), 1) if tot2 > tot1 else 0.0
    net_kbps = round((read_net_bytes() - net1) / max(time.time() - t1, 0.01) / 1024, 1)
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, rest = line.partition(":")
            if k in ("MemTotal", "MemAvailable"):
                mem[k] = int(rest.split()[0])
    mem_used_mb = round((mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)) / 1024)
    mem_total_mb = round(mem.get("MemTotal", 0) / 1024)
    disk = os.statvfs(WORKSPACE)
    with open("/proc/uptime") as f:
        uptime_s = int(float(f.read().split()[0]))
    return {
        "cpu_pct": cpu_pct, "mem_used_mb": mem_used_mb, "mem_total_mb": mem_total_mb,
        "mem_pct": round(100.0 * mem_used_mb / mem_total_mb, 1) if mem_total_mb else 0.0,
        "disk_used_pct": round(100.0 * (1 - disk.f_bavail / disk.f_blocks), 1) if disk.f_blocks else 0.0,
        "load1": round(os.getloadavg()[0], 2), "uptime_min": uptime_s // 60, "net_kbps": net_kbps,
    }


# ---------------------------------------------------------------- exec
def tree_rss_mb(root_pid):
    """Sum VmRSS (MB) of a process and all its descendants via /proc."""
    ppid, rss = {}, {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            ppid[int(name)] = int(parts[1])
            with open(f"/proc/{name}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss[int(name)] = int(line.split()[1]) / 1024
        except Exception:
            pass
    total, stack = 0.0, [root_pid]
    seen = set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        total += rss.get(p, 0.0)
        stack.extend(c for c, parent in ppid.items() if parent == p)
    return total


def _run_once(argv, timeout):
    p = subprocess.Popen(argv, cwd=WORKSPACE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    killed = {"why": None}

    def watchdog():
        while p.poll() is None:
            if tree_rss_mb(p.pid) > EXEC_CAP_MB:
                killed["why"] = f"memory cap {EXEC_CAP_MB} MB exceeded"
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
                return
            time.sleep(0.2)

    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()
    try:
        out, err = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass
        out, err = p.communicate()
        code, killed["why"] = -1, f"timeout {timeout}s"
    return code, (out or "")[-65536:], (err or "")[-16384:], killed["why"]


def run_exec(cmd, timeout, sandbox_mode):
    plain = ["/bin/sh", "-c", cmd]
    argv = plain
    sandbox = "off"
    if sandbox_mode != "off" and shutil.which("bwrap"):
        argv = ["bwrap", "--ro-bind", "/", "/", "--bind", WORKSPACE, WORKSPACE,
                "--dev", "/dev", "--proc", "/proc", "--chdir", WORKSPACE]
        if sandbox_mode == "nonet":
            argv.append("--unshare-net")
        argv += ["--", "/bin/sh", "-c", cmd]
        sandbox = "bwrap" + ("+nonet" if sandbox_mode == "nonet" else "")
    elif sandbox_mode != "off":
        sandbox = "unavailable-fallback"
    code, out, err, killed = _run_once(argv, timeout)
    if sandbox.startswith("bwrap") and code != 0 and "bwrap:" in err:
        # The host forbids bwrap's mount operations (Render does) —
        # heal by running unsandboxed instead of failing every job.
        code, out, err, killed = _run_once(plain, timeout)
        sandbox = "bwrap-unavailable-fallback"
    return {"code": code, "stdout": out, "stderr": err,
            "sandbox": sandbox, "killed": killed}


# ---------------------------------------------------------------- tasks
def load_tasks():
    try:
        with open(TASKS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"tasks": []}


def save_tasks(data):
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, TASKS_FILE)


# ---------------------------------------------------------------- watch
class _WS:
    """Minimal WebSocket client (stdlib) for the local CDP endpoint."""

    def __init__(self, host, port, path, timeout=5.0):
        self.s = socket.create_connection((host, port), timeout=30)
        self.s.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise RuntimeError("ws handshake closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0] + b" ":
            raise RuntimeError("ws handshake failed")
        self.buf = rest

    def _frame(self, opcode, data):
        mask = os.urandom(4)
        hdr = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        self.s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text):
        self._frame(1, text.encode())

    def _readn(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise RuntimeError("ws closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv_msg(self):
        payload = b""
        while True:
            h = self._readn(2)
            fin, op = h[0] & 0x80, h[0] & 0x0F
            ln = h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._readn(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._readn(8))[0]
            if h[1] & 0x80:
                self._readn(4)
            data = self._readn(ln) if ln else b""
            if op == 9:
                self._frame(0xA, data)
                continue
            if op in (0, 1, 2):
                payload += data
                if fin:
                    return payload.decode(errors="replace")

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def _jpeg_size(data):
    """(width, height) from a JPEG's SOF segment, or None."""
    try:
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h = (data[i + 5] << 8) | data[i + 6]
                w = (data[i + 7] << 8) | data[i + 8]
                return (w, h)
            if marker == 0xD9 or 0xD0 <= marker <= 0xD8:
                i += 2
                continue
            ln = (data[i + 2] << 8) | data[i + 3]
            if ln < 2:
                return None
            i += 2 + ln
    except Exception:
        pass
    return None


class WatchSession:
    """One Moli serve process + CDP screencast, owned by one thread.

    The session thread is the only thread touching the CDP socket;
    HTTP handlers hand it commands through a queue and wait on an
    event. Frames land in memory (latest only) — GET /watch.png
    serves them to the face.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.cmds = queue.Queue()
        self.thread = None
        self.active = False
        self.url = ""
        self.title = ""
        self.frame = None
        self.frame_ts = 0.0
        self.frames = 0
        self.error = ""
        self.last_access = 0.0
        self._mid = [0]
        self._cdp_session = None
        self.viewport = None    # {"w":..,"h":..} CSS pixels of the page
        self.frame_size = None  # (w, h) of the latest screencast JPEG

    # -- HTTP-side API ------------------------------------------------
    def _ensure_thread(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return True
            if not shutil.which(MOLI_BIN):
                self.error = "moli binary not found on this system"
                return False
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return True

    def navigate(self, url):
        if not self._ensure_thread():
            return {"ok": False, "error": self.error}
        done = threading.Event()
        box = {}
        self.cmds.put(("navigate", url, done, box))
        self.last_access = time.time()
        if not done.wait(timeout=120):
            return {"ok": False, "error": "watch start/navigate timed out"}
        return box.get("result", {"ok": False, "error": "no result"})

    def stop(self):
        self.cmds.put(("stop", None, None, None))
        return {"ok": True}

    def input(self, spec):
        """Forward one human input (click/type/key/scroll) to the page."""
        if not self._ensure_thread():
            return {"ok": False, "error": self.error}
        done = threading.Event()
        box = {}
        self.cmds.put(("input", spec, done, box))
        self.last_access = time.time()
        if not done.wait(timeout=30):
            return {"ok": False, "error": "input timed out"}
        return box.get("result", {"ok": False, "error": "no result"})

    def status(self):
        self.last_access = time.time()
        return {"active": self.active, "url": self.url, "title": self.title,
                "frames": self.frames,
                "frame_age_s": round(time.time() - self.frame_ts, 1) if self.frame_ts else None,
                "viewport": self.viewport,
                "frame": ({"w": self.frame_size[0], "h": self.frame_size[1]}
                          if self.frame_size else None),
                "error": self.error}

    def latest_frame(self):
        self.last_access = time.time()
        return self.frame

    # -- session thread ------------------------------------------------
    def _run(self):
        proc = None
        ws = None
        session = None
        mid = self._mid
        try:
            proc = subprocess.Popen(
                [MOLI_BIN, "serve", "--layout", "--host", "127.0.0.1",
                 "--port", str(WATCH_PORT), "--screencast-interval-ms", "1000"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ver = None
            for _ in range(90):
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{WATCH_PORT}/json/version", timeout=3) as r:
                        ver = json.load(r)
                    break
                except Exception:
                    if proc.poll() is not None:
                        raise RuntimeError("moli serve exited during startup")
                    time.sleep(0.5)
            if not ver:
                raise RuntimeError("moli serve did not come up")
            wsurl = ver["webSocketDebuggerUrl"]
            path = wsurl[wsurl.index("/", wsurl.index("://") + 3):]
            ws = _WS("127.0.0.1", WATCH_PORT, path)
            ws.s.settimeout(1.5)  # commands shouldn't wait on a 5s drain

            def call(method, params=None, sess=None, timeout=45):
                mid[0] += 1
                my = mid[0]
                msg = {"id": my, "method": method, "params": params or {}}
                if sess:
                    msg["sessionId"] = sess
                ws.send(json.dumps(msg))
                end = time.time() + timeout
                while time.time() < end:
                    try:
                        m = json.loads(ws.recv_msg())
                    except socket.timeout:
                        continue
                    if m.get("method") == "Page.screencastFrame":
                        self._on_frame(ws, m["params"])
                        continue
                    if m.get("id") == my:
                        if "error" in m:
                            raise RuntimeError(method + ": " + json.dumps(m["error"])[:160])
                        return m.get("result", {})
                raise RuntimeError("cdp timeout: " + method)

            t = call("Target.createTarget", {"url": "about:blank"})
            a = call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})
            session = a["sessionId"]
            self._cdp_session = session
            call("Page.enable", sess=session)
            call("Page.startScreencast",
                 {"format": "jpeg", "quality": 55, "maxWidth": 1100, "maxHeight": 700},
                 sess=session)
            with self.lock:
                self.active = True
                self.error = ""
            self._read_viewport(call, session)

            while True:
                try:
                    kind, arg, done, box = self.cmds.get(timeout=0.4)
                except queue.Empty:
                    kind = None
                if kind == "stop":
                    break
                if kind == "input":
                    try:
                        self._do_input(call, session, arg)
                        time.sleep(0.6)
                        self._read_title_url(call, session)
                        if box is not None:
                            box["result"] = {"ok": True, "url": self.url, "title": self.title}
                    except Exception as e:
                        if box is not None:
                            box["result"] = {"ok": False, "error": str(e)[:200]}
                    if done:
                        done.set()
                if kind == "navigate":
                    try:
                        call("Page.navigate", {"url": arg}, sess=session)
                        self.url = arg
                        for _ in range(8):
                            time.sleep(0.7)
                            try:
                                r = call("Runtime.evaluate",
                                         {"expression": "document.title", "returnByValue": True},
                                         sess=session, timeout=10)
                                title = (r.get("result") or {}).get("value") or ""
                                if title:
                                    self.title = title
                                    break
                            except Exception:
                                pass
                        if box is not None:
                            box["result"] = {"ok": True, "url": self.url, "title": self.title}
                    except Exception as e:
                        if box is not None:
                            box["result"] = {"ok": False, "error": str(e)[:200]}
                    if done:
                        done.set()
                # drain any frames that arrived meanwhile
                try:
                    while True:
                        m = json.loads(ws.recv_msg())
                        if m.get("method") == "Page.screencastFrame":
                            self._on_frame(ws, m["params"])
                except socket.timeout:
                    pass
                if self.frame_ts and time.time() - self.last_access > WATCH_IDLE_S:
                    break  # nobody is watching; free the memory
        except Exception as e:
            with self.lock:
                self.error = str(e)[:200]
            # wake any waiter with the failure
            try:
                while True:
                    kind, arg, done, box = self.cmds.get_nowait()
                    if box is not None:
                        box["result"] = {"ok": False, "error": self.error}
                    if done:
                        done.set()
            except queue.Empty:
                pass
        finally:
            with self.lock:
                self.active = False
                self.frame = None
                self.url = ""
                self.title = ""
            if ws:
                ws.close()
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def _read_viewport(self, call, session):
        try:
            r = call("Runtime.evaluate",
                     {"expression": "[window.innerWidth, window.innerHeight]",
                      "returnByValue": True}, sess=session, timeout=10)
            v = (r.get("result") or {}).get("value") or []
            if len(v) == 2 and v[0]:
                self.viewport = {"w": int(v[0]), "h": int(v[1])}
        except Exception:
            pass

    def _read_title_url(self, call, session):
        try:
            r = call("Runtime.evaluate",
                     {"expression": "[document.title, location.href]",
                      "returnByValue": True}, sess=session, timeout=10)
            v = (r.get("result") or {}).get("value") or []
            if len(v) == 2:
                if v[0]:
                    self.title = v[0]
                if v[1]:
                    self.url = v[1]
        except Exception:
            pass

    # key name -> (code, key, windows virtual key code)
    _KEYMAP = {
        "Enter": ("Enter", "Enter", 13), "Tab": ("Tab", "Tab", 9),
        "Escape": ("Escape", "Escape", 27), "Backspace": ("Backspace", "Backspace", 8),
        "Delete": ("Delete", "Delete", 46), " ": ("Space", " ", 32),
        "ArrowUp": ("ArrowUp", "ArrowUp", 38), "ArrowDown": ("ArrowDown", "ArrowDown", 40),
        "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37), "ArrowRight": ("ArrowRight", "ArrowRight", 39),
    }

    def _do_input(self, call, session, spec):
        """Translate one face input into CDP Input calls (viewport coords)."""
        kind = (spec or {}).get("kind")
        if kind == "click":
            x, y = float(spec["x"]), float(spec["y"])
            call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, sess=session)
            call("Input.dispatchMouseEvent",
                 {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
                 sess=session)
            call("Input.dispatchMouseEvent",
                 {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
                 sess=session)
        elif kind == "type":
            text = str(spec.get("text", ""))[:2000]
            if text:
                call("Input.insertText", {"text": text}, sess=session)
        elif kind == "key":
            k = str(spec.get("key", ""))
            code, keyname, vkey = self._KEYMAP.get(k, (k, k, 0))
            ev = {"key": keyname, "code": code}
            if vkey:
                ev["windowsVirtualKeyCode"] = vkey
                ev["nativeVirtualKeyCode"] = vkey
            if len(k) == 1:
                ev["text"] = k
            call("Input.dispatchKeyEvent", dict(ev, type="keyDown"), sess=session)
            call("Input.dispatchKeyEvent", dict(ev, type="keyUp"), sess=session)
        elif kind == "scroll":
            dy = int(spec.get("dy", 600))
            call("Runtime.evaluate",
                 {"expression": f"window.scrollBy(0, {dy})", "returnByValue": True},
                 sess=session)
        else:
            raise RuntimeError("unknown input kind: " + str(kind))

    def _on_frame(self, ws, params):
        try:
            self.frame = base64.b64decode(params.get("data", ""))
            size = _jpeg_size(self.frame)
            if size:
                self.frame_size = size
            self.frame_ts = time.time()
            self.frames += 1
            self._mid[0] += 1
            ack = {"id": self._mid[0], "method": "Page.screencastFrameAck",
                   "params": {"sessionId": params.get("sessionId", 1)}}
            if self._cdp_session:
                ack["sessionId"] = self._cdp_session
            ws.send(json.dumps(ack))
        except Exception:
            pass


WATCH = WatchSession()


# ---------------------------------------------------------------- music
MUSIC_DIR = os.path.join(WORKSPACE, "music")
MUSIC_INDEX = os.path.join(MUSIC_DIR, "index.json")
UA = "JarvisOS/0.5 music (personal use)"
COBALT_SEEDS = [s for s in [os.environ.get("GETMEDIA_API", ""),
                            "https://api.cobalt.liubquanti.click",
                            "https://cobalt-api.kwiatekmiki.com",
                            "https://api.cobalt.canine.tools"] if s]
FORBIDDEN_WORDS = ["remix", "remaster", "remastered", "live", "acoustic",
                   "cover", "karaoke", "instrumental", "slowed", "reverb",
                   "bassboost", "bassboosted", "8daudio", "concert", "acapella",
                   "lyrics", "lyric", "nightcore", "sped"]
_index_lock = threading.Lock()
_dl_state = {}  # videoId -> {"done": bool, "error": str|None, "bytes": int}
_inv_cache = {"hosts": [], "ts": 0.0}


def _http(url, timeout=20, data=None, ctype=None):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", UA)
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _http_json(url, timeout=20):
    _, body = _http(url, timeout=timeout)
    return json.loads(body)


def _inv_hosts():
    if _inv_cache["hosts"] and time.time() - _inv_cache["ts"] < 3600:
        return _inv_cache["hosts"]
    hosts = ["yt.chocolatemoo53.com"]
    try:
        data = _http_json("https://api.invidious.io/instances.json", timeout=15)
        for name, info in data:
            if info.get("api") and info.get("type") == "https" and name not in hosts:
                hosts.append(name)
            if len(hosts) >= 6:
                break
    except Exception:
        pass
    _inv_cache.update(hosts=hosts, ts=time.time())
    return hosts


def _inv_get(path):
    last = None
    for host in _inv_hosts():
        try:
            return _http_json(f"https://{host}{path}", timeout=18), host
        except Exception as e:
            last = e
    raise RuntimeError(f"all invidious instances failed: {last}")


def _thumb(item, host):
    thumbs = item.get("videoThumbnails") or []
    best = None
    for t in thumbs:
        url = t.get("url", "")
        if url.startswith("/"):
            url = f"https://{host}{url}"
        if not best or (t.get("width", 0) <= 480 and t.get("width", 0) > best[0]):
            best = (t.get("width", 0), url)
    if best:
        return best[1]
    vid = item.get("videoId", "")
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""


def yt_search(query, limit=12):
    data, host = _inv_get("/api/v1/search?type=video&q=" + quote(query))
    out = []
    for it in data[:limit]:
        if it.get("type") not in (None, "video") or not it.get("videoId"):
            continue
        out.append({"videoId": it["videoId"], "title": it.get("title", ""),
                    "author": it.get("author", ""),
                    "duration": it.get("lengthSeconds", 0),
                    "art": _thumb(it, host)})
    return out


def yt_info(video_id):
    data, _ = _inv_get(f"/api/v1/videos/{video_id}")
    return {"videoId": video_id, "title": data.get("title", ""),
            "author": data.get("author", ""),
            "duration": data.get("lengthSeconds", 0),
            "art": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _ratio(a, b):
    return difflib.SequenceMatcher(None, _slug(a), _slug(b)).ratio() * 100


def clean_title(title):
    t = re.sub(r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|lyrics|mv|hd|4k|visualizer)[^\)\]]*[\)\]]",
               "", title or "", flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -|")
    return t or (title or "")


def primary_artist(artist):
    a = re.split(r"\s*(?:,|&|feat\.?|ft\.?|featuring)\s*", artist or "", flags=re.I)[0]
    return a.strip() or (artist or "")


def score_candidate(song, cand):
    """SpotDL-grade match score (0..~110) for one YouTube candidate."""
    stitle, sartist, sdur = song["title"], song.get("artist", ""), song.get("duration", 0)
    name = _ratio(stitle, cand["title"])
    author = max(_ratio(sartist, cand.get("author", "")),
                 60.0 if _slug(sartist) and _slug(sartist) in _slug(cand["title"]) else 0.0,
                 90.0 if _slug(sartist) and _slug(sartist) in _slug(cand.get("author", "")) else 0.0)
    cdur = cand.get("duration", 0)
    dur = math.exp(-0.1 * abs(sdur - cdur)) * 100 if sdur and cdur else 55.0
    cslug, sslug = _slug(cand["title"]), _slug(stitle)
    for w in FORBIDDEN_WORDS:
        if w in cslug.split() and w not in sslug.split():
            name -= 15
    bonus = 0.0
    if "official audio" in cslug:
        bonus += 8
    if cand.get("author", "").strip().lower().endswith("- topic"):
        bonus += 8
    if "official video" in cslug:
        bonus += 4
    total = 0.45 * name + 0.25 * author + 0.30 * dur + bonus
    return {"score": round(total, 1), "name": round(name, 1),
            "artist": round(author, 1), "duration": round(dur, 1)}


def best_match(song, candidates):
    scored = []
    for c in candidates:
        s = score_candidate(song, c)
        if s["name"] <= 40 or s["duration"] < 20:
            continue  # SpotDL's hard gates, loosened a notch
        scored.append((s["score"], c, s))
    scored.sort(key=lambda x: -x[0])
    return scored[0] if scored else (None, None, None)


def spotify_meta(url):
    """Track metadata from Spotify's public embed/oEmbed — no login."""
    m = re.search(r"/track/([A-Za-z0-9]+)", url)
    if not m:
        raise RuntimeError("only open.spotify.com/track links are supported")
    tid = m.group(1)
    out = {"title": "", "artist": "", "duration": 0, "art": ""}
    try:
        oe = _http_json("https://open.spotify.com/oembed?url=" + quote(url, safe=""), timeout=15)
        out["title"] = oe.get("title", "")
        out["art"] = oe.get("thumbnail_url", "")
    except Exception:
        pass
    try:
        _, html = _http(f"https://open.spotify.com/embed/track/{tid}", timeout=15)
        h = html.decode(errors="replace")
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
        if nd:
            ent = json.loads(nd.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
            out["title"] = ent.get("name") or ent.get("title") or out["title"]
            arts = [a.get("name", "") for a in (ent.get("artists") or []) if a.get("name")]
            if arts:
                out["artist"] = ", ".join(arts)
            if ent.get("duration"):
                out["duration"] = int(ent["duration"]) // 1000
        if not out["duration"]:
            dm = re.search(r'"duration":\s*(\d+)', h)
            if dm:
                out["duration"] = int(dm.group(1)) // 1000
        if not out["artist"]:
            am = (re.search(r'"artists":\[\{"name":"([^"]+)"', h)
                  or re.search(r'"subtitle":"([^"]+)"', h))
            if am:
                out["artist"] = am.group(1)
        if not out["title"]:
            tm = re.search(r'"name":"([^"]{2,120})"', h)
            if tm:
                out["title"] = tm.group(1)
    except Exception:
        pass
    if not out["title"]:
        raise RuntimeError("could not read that Spotify track")
    return out


def load_index():
    try:
        with open(MUSIC_INDEX) as f:
            return json.load(f)
    except Exception:
        return {"songs": {}}


def save_index(idx):
    os.makedirs(MUSIC_DIR, exist_ok=True)
    tmp = MUSIC_INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f, indent=1)
    os.replace(tmp, MUSIC_INDEX)


def song_file(video_id):
    return os.path.join(MUSIC_DIR, video_id + ".mp3")


def is_cached(video_id):
    p = song_file(video_id)
    return os.path.isfile(p) and os.path.getsize(p) > 20000


def _cobalt_audio_url_from(api, video_id):
    payload = json.dumps({"url": f"https://www.youtube.com/watch?v={video_id}",
                          "videoQuality": "720", "downloadMode": "audio",
                          "audioFormat": "mp3", "filenameStyle": "basic"}).encode()
    _, body = _http(api + "/", timeout=30, data=payload, ctype="application/json")
    res = json.loads(body)
    status = res.get("status")
    if status in ("tunnel", "redirect", "local-processing") and res.get("url"):
        return res["url"]
    if status == "picker":
        for item in res.get("picker", []):
            if item.get("url"):
                return item["url"]
        if res.get("audio"):
            return res["audio"]
    raise RuntimeError(f"cobalt {api}: {status} {res.get('error', '')}")


def _fetch_audio(video_id, part):
    """Fetch the MP3, failing over across Cobalt instances.

    Community instances occasionally hand out tunnels that deliver
    an empty body (seen live 2026-11-13), and the first seed being
    able to MINT a tunnel says nothing about whether its tunnel
    host will actually serve bytes — so an attempt only counts
    when >=20 KB really land, else the next instance is tried.
    """
    last = None
    seeds = list(COBALT_SEEDS)
    if len(seeds) > 1:
        seeds.append(seeds[0])
    for api in seeds:
        try:
            src = _cobalt_audio_url_from(api, video_id)
            req = urllib.request.Request(src)
            req.add_header("User-Agent", UA)
            total = 0
            t0 = time.time()
            # A tunnel can also fail by DRIPPING (seen live: one
            # chunk, then nothing for minutes). With small reads,
            # a dead tunnel trips the 25s socket timeout quickly,
            # while a slow-but-alive one keeps completing reads
            # and is allowed to finish — a 150s total deadline
            # was tried first and killed a healthy 20 KB/s fetch
            # at 84%, so the cap here is only a 900s safety net.
            with urllib.request.urlopen(req, timeout=25) as r, open(part, "wb") as f:
                while True:
                    chunk = r.read(16384)  # small reads: progress stays visible
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    if total > 60 * 1024 * 1024:
                        raise RuntimeError("file too large for a song")
                    if time.time() - t0 > 900:
                        raise RuntimeError(f"tunnel via {api} still busy after 900s ({total} bytes)")
            if total < 20000:
                raise RuntimeError(f"tunnel via {api} delivered only {total} bytes")
            return total
        except Exception as e:
            last = e
            try:
                os.remove(part)
            except Exception:
                pass
            time.sleep(2)
    raise RuntimeError(f"audio fetch failed on all instances: {last}")


def _download(video_id):
    st = _dl_state.setdefault(video_id, {"done": False, "error": None, "bytes": 0})
    part = song_file(video_id) + ".part"
    try:
        os.makedirs(MUSIC_DIR, exist_ok=True)
        st["bytes"] = _fetch_audio(video_id, part)
        os.replace(part, song_file(video_id))
        with _index_lock:
            idx = load_index()
            if video_id in idx["songs"]:
                idx["songs"][video_id]["cached"] = True
                save_index(idx)
        st["done"] = True
    except Exception as e:
        st["error"] = str(e)[:200]
        try:
            os.remove(part)
        except Exception:
            pass


def ensure_download(video_id):
    if is_cached(video_id):
        return "cached"
    st = _dl_state.get(video_id)
    if st and not st["done"] and not st["error"]:
        return "streaming"
    if st and st["error"]:
        _dl_state.pop(video_id, None)
    threading.Thread(target=_download, args=(video_id,), daemon=True).start()
    return "streaming"


PALETTE_VER = 2  # bump when the picker changes; cached palettes recompute


def palette_for(art_url, video_id):
    """Two glow colours from the album art (Pillow, cached in index).

    Vibrant-style picking: a plain by-area palette is mud (hair and
    backdrop win on pixels while the poster's red sits in a corner),
    so the lead colour is the most vivid prominent swatch and the
    second is the dominant area colour that is not its near-twin.
    Raw cover swatches are usually shadow-toned, so each pick keeps
    its hue but is lifted to glow strength (saturation/lightness
    floors) — the rim and the lyric block need neon, not mud.
    """
    if not art_url:
        return None
    try:
        import colorsys
        import io
        from PIL import Image
        _, data = _http(art_url, timeout=12)
        im = Image.open(io.BytesIO(data[:800_000])).convert("RGB")
        im.thumbnail((96, 96))
        q = im.quantize(colors=16)
        pal = q.getpalette()
        counts = q.getcolors() or []
        total = sum(c for c, _ in counts) or 1
        entries = []
        for cnt, ci in counts:
            r, g, b = pal[ci * 3:ci * 3 + 3]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 30:
                continue  # near-black carries no glow
            s = (mx - mn) / mx if mx else 0.0
            entries.append((cnt / total, s, (r, g, b)))
        if not entries:
            return None

        def close(a, b):
            return (abs(a[0] - b[0]) < 40 and abs(a[1] - b[1]) < 40
                    and abs(a[2] - b[2]) < 40)

        def lift(rgb, s_floor, l_fix):
            h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
            if s < 0.04:  # monochrome art: hue is meaningless (0 = red) — stay neutral
                r, g, b = colorsys.hls_to_rgb(h, min(max(l, 0.35), 0.65), s)
            else:
                r, g, b = colorsys.hls_to_rgb(h, l_fix, max(s, s_floor))
            return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

        picks = []
        vivid = [e for e in entries if e[1] >= 0.40 and max(e[2]) >= 60]
        if vivid:
            picks.append(max(vivid, key=lambda e: e[0] * e[1])[2])
        for _frac, _s, rgb in sorted(entries, reverse=True):
            if all(not close(rgb, c) for c in picks):
                picks.append(rgb)
            if len(picks) == 2:
                break
        if not picks:
            picks = [entries[0][2]]
        out = [lift(picks[0], 0.72, 0.52)]
        if len(picks) > 1:
            out.append(lift(picks[1], 0.45, 0.58))
        return out
    except Exception:
        return None


def parse_lrc(text):
    lines = []
    for raw in (text or "").splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)
        lyric = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", raw).strip()
        for mm, ss in stamps:
            lines.append({"t": int(mm) * 60 + float(ss), "text": lyric})
    lines.sort(key=lambda x: x["t"])
    return lines


def fetch_lyrics(title, artist, duration):
    """LRCLIB: line-synced lyrics for the song, or plain, or None."""
    q = (f"https://lrclib.net/api/search?track_name={quote(clean_title(title))}"
         f"&artist_name={quote(primary_artist(artist))}")
    try:
        results = _http_json(q, timeout=15)
    except Exception:
        return None
    best_synced, best_plain, best_gap = None, None, 1e9
    for r in results:
        gap = abs((r.get("duration") or 0) - (duration or 0)) if duration else 0
        if r.get("syncedLyrics") and gap < best_gap:
            best_synced, best_gap = r, gap
        if r.get("plainLyrics") and (best_plain is None or gap < abs((best_plain.get("duration") or 0) - (duration or 0))):
            best_plain = r
    if best_synced:
        return {"lines": parse_lrc(best_synced["syncedLyrics"]), "synced": True}
    if best_plain:
        return {"lines": [{"t": None, "text": ln} for ln in best_plain["plainLyrics"].splitlines()],
                "synced": False}
    return None


def lyrics_for(song):
    cache = os.path.join(MUSIC_DIR, song["videoId"] + ".lyrics.json")
    try:
        with open(cache) as f:
            return json.load(f)
    except Exception:
        pass
    lyr = fetch_lyrics(song["title"], song.get("artist", ""), song.get("duration", 0))
    if lyr:
        try:
            os.makedirs(MUSIC_DIR, exist_ok=True)
            with open(cache, "w") as f:
                json.dump(lyr, f)
        except Exception:
            pass
    return lyr


def resolve_play(req):
    """Turn {query|url|videoId} into a playable song record."""
    url = (req.get("url") or "").strip()
    query = (req.get("query") or "").strip()
    video_id = (req.get("videoId") or "").strip()
    song = None
    if url and "spotify.com" in url:
        meta = spotify_meta(url)
        song = {"title": meta["title"], "artist": meta["artist"],
                "duration": meta["duration"], "art": meta["art"]}
        cands = yt_search(f"{meta['title']} {meta['artist']}".strip())
        _, best, sc = best_match(song, cands)
        if not best:
            raise RuntimeError("no matching version found on YouTube")
        video_id = best["videoId"]
        song["videoId"] = video_id
        song["match"] = sc
        if not song["duration"]:
            song["duration"] = best["duration"]
    elif url or (video_id and not req.get("title")):
        m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url or "")
        video_id = video_id or (m.group(1) if m else "")
        if not video_id:
            raise RuntimeError("could not read a YouTube video id from that link")
        info = yt_info(video_id)
        song = {"videoId": video_id, "title": clean_title(info["title"]),
                "artist": info["author"].replace(" - Topic", ""),
                "duration": info["duration"], "art": info["art"]}
    elif video_id:
        song = {"videoId": video_id, "title": clean_title(req.get("title", "")),
                "artist": (req.get("artist") or "").replace(" - Topic", ""),
                "duration": int(req.get("duration") or 0),
                "art": req.get("art") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
    elif query:
        cands = yt_search(query)
        if not cands:
            raise RuntimeError("no results for that search")
        top = cands[0]
        probe = {"title": clean_title(query), "artist": "", "duration": top["duration"]}
        _, best, _ = best_match(probe, cands)
        best = best or top
        video_id = best["videoId"]
        song = {"videoId": video_id, "title": clean_title(best["title"]),
                "artist": best["author"].replace(" - Topic", ""),
                "duration": best["duration"], "art": best["art"]}
    else:
        raise RuntimeError("give a query, a Spotify/YouTube link, or a videoId")
    state = ensure_download(video_id)
    with _index_lock:
        idx = load_index()
        rec = idx["songs"].get(video_id, {})
        rec.update({k: song[k] for k in ("title", "artist", "duration", "art") if k in song})
        rec["videoId"] = video_id
        rec["cached"] = is_cached(video_id)
        idx["songs"][video_id] = rec
        save_index(idx)
    if not rec.get("palette") or rec.get("pal_v") != PALETTE_VER:
        pal = palette_for(rec.get("art", ""), video_id)
        if pal:
            with _index_lock:
                idx = load_index()
                idx["songs"][video_id]["palette"] = pal
                idx["songs"][video_id]["pal_v"] = PALETTE_VER
                save_index(idx)
                rec["palette"] = pal
    return {"videoId": video_id, "title": rec.get("title", song["title"]),
            "artist": rec.get("artist", ""), "duration": rec.get("duration", 0),
            "art": rec.get("art", ""), "palette": rec.get("palette"),
            "state": state,
            "url": ("/download?path=" + quote("music/" + video_id + ".mp3")
                    if state == "cached" else f"/stream?id={video_id}"),
            "match": song.get("match"), "lyrics": lyrics_for(rec)}


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisOS/0.5"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self):
        return bool(TOKEN) and self.headers.get("X-Jarvis-Token") == TOKEN

    _CTYPES = {".mp3": "audio/mpeg", ".jpg": "image/jpeg", ".png": "image/png",
               ".mp4": "video/mp4", ".json": "application/json",
               ".md": "text/plain", ".txt": "text/plain"}

    def _send_file(self, rel):
        """Workspace file with HTTP Range — seekable media + downloads."""
        target = os.path.abspath(os.path.join(WORKSPACE, rel))
        if not target.startswith(WORKSPACE) or not os.path.isfile(target):
            return self._send(404, {"error": "no such file"})
        size = os.path.getsize(target)
        ctype = self._CTYPES.get(os.path.splitext(target)[1].lower(),
                                 "application/octet-stream")
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "") or "")
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            code = 206
        length = max(end - start + 1, 0)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(target, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_stream(self, video_id):
        """Progressive play: tail-follow the growing cache file."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id or ""):
            return self._send(400, {"error": "bad id"})
        final, part = song_file(video_id), song_file(video_id) + ".part"
        for _ in range(80):  # the download thread may need a few s to open the file
            if os.path.exists(final) or os.path.exists(part):
                break
            if video_id not in _dl_state:
                return self._send(404, {"error": "song not started"})
            time.sleep(0.25)
        path = final if os.path.exists(final) else part
        if not os.path.exists(path):
            return self._send(404, {"error": "no audio yet"})
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        try:
            fd = os.open(path, os.O_RDONLY)
            idle = 0
            while True:
                chunk = os.read(fd, 65536)
                if chunk:
                    idle = 0
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    continue
                st = _dl_state.get(video_id)
                if os.path.exists(final) and (st is None or st.get("done") or st.get("error")):
                    break
                idle += 1
                if idle > 240:
                    break
                time.sleep(0.25)
            os.close(fd)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _json_body(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except Exception:
            return None

    def do_HEAD(self):
        # Uptime monitors (e.g. UptimeRobot) check with HEAD; route
        # exactly like GET — _send keeps the headers, drops the body.
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._send(200, {"ok": True, "os": "JarvisOS 0.5"})
        if u.path in ("/", "/face"):
            try:
                with open(os.path.join(SHELL_DIR, "face.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            except Exception:
                return self._send(500, {"error": "face missing"})
        if u.path in ("/download", "/stream"):
            # Media elements (<audio>, downloads) cannot send headers,
            # so these two routes also accept the token as ?t=.
            qs = parse_qs(u.query)
            if not self._authed() and not (TOKEN and qs.get("t", [""])[0] == TOKEN):
                return self._send(401, {"error": "bad or missing token"})
            if u.path == "/download":
                return self._send_file(qs.get("path", [""])[0])
            return self._send_stream(qs.get("id", [""])[0])
        if not self._authed():
            return self._send(401, {"error": "bad or missing token"})
        if u.path == "/stats":
            return self._send(200, get_stats())
        if u.path == "/desktop.png":
            import desktop
            return self._send(200, desktop.render_frame(get_stats()), "image/png")
        if u.path == "/wallpaper.jpg":
            try:
                with open(os.path.join(SHELL_DIR, "wallpaper.jpg"), "rb") as f:
                    return self._send(200, f.read(), "image/jpeg")
            except Exception:
                return self._send(404, {"error": "no wallpaper"})
        if u.path == "/watch":
            return self._send(200, WATCH.status())
        if u.path == "/watch.png":
            frame = WATCH.latest_frame()
            if frame:
                return self._send(200, frame, "image/jpeg")
            return self._send(404, {"error": "no frame yet — POST /watch with a url first"})
        if u.path == "/tasks":
            return self._send(200, load_tasks())
        if u.path == "/files":
            rel = parse_qs(u.query).get("path", [""])[0]
            target = os.path.abspath(os.path.join(WORKSPACE, rel))
            if not target.startswith(WORKSPACE) or not os.path.isdir(target):
                return self._send(400, {"error": "bad path"})
            items = []
            for name in sorted(os.listdir(target)):
                p = os.path.join(target, name)
                items.append({"name": name, "dir": os.path.isdir(p),
                              "size": os.path.getsize(p) if os.path.isfile(p) else None})
            return self._send(200, {"path": rel, "items": items})
        if u.path == "/music/search":
            q = parse_qs(u.query).get("q", [""])[0]
            if not q:
                return self._send(400, {"error": "q required"})
            try:
                return self._send(200, {"results": yt_search(q)})
            except Exception as e:
                return self._send(502, {"error": str(e)[:200]})
        if u.path == "/music/list":
            idx = load_index()
            songs = []
            for vid, rec in idx["songs"].items():
                st = _dl_state.get(vid)
                songs.append({**rec, "cached": is_cached(vid),
                              "state": "cached" if is_cached(vid) else
                                       ("error" if st and st.get("error") else
                                        "streaming" if st else "gone")})
            songs.sort(key=lambda s: (not s["cached"], s.get("title", "")))
            return self._send(200, {"songs": songs})
        if u.path == "/music/status":
            vid = parse_qs(u.query).get("id", [""])[0]
            st = _dl_state.get(vid, {})
            return self._send(200, {"videoId": vid, "cached": is_cached(vid),
                                    "done": bool(st.get("done")), "bytes": st.get("bytes", 0),
                                    "error": st.get("error")})
        if u.path == "/music/lyrics":
            qs = parse_qs(u.query)
            song = {"videoId": qs.get("videoId", [""])[0],
                    "title": qs.get("title", [""])[0],
                    "artist": qs.get("artist", [""])[0],
                    "duration": int(qs.get("duration", ["0"])[0] or 0)}
            return self._send(200, {"lyrics": lyrics_for(song) if song["videoId"] else
                                    fetch_lyrics(song["title"], song["artist"], song["duration"])})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"error": "bad or missing token"})
        req = self._json_body()
        if req is None:
            return self._send(400, {"error": "bad json"})
        if self.path == "/exec":
            cmd = req.get("cmd", "")
            if not cmd:
                return self._send(400, {"error": "cmd required"})
            t0 = time.time()
            res = run_exec(cmd, min(int(req.get("timeout", 30)), 600), req.get("sandbox", "on"))
            res["secs"] = round(time.time() - t0, 2)
            if res["code"] == 0:
                schedule_sync()
            return self._send(200, res)
        if self.path == "/watch":
            url = (req.get("url") or "").strip()
            if not url:
                return self._send(400, {"error": "url required"})
            if not re.match(r"^https?://", url):
                url = "https://" + url
            return self._send(200, WATCH.navigate(url))
        if self.path == "/watch/stop":
            return self._send(200, WATCH.stop())
        if self.path == "/watch/input":
            if not req.get("kind"):
                return self._send(400, {"error": "kind required: click|type|key|scroll"})
            return self._send(200, WATCH.input(req))
        if self.path == "/music/play":
            try:
                return self._send(200, resolve_play(req))
            except Exception as e:
                return self._send(502, {"error": str(e)[:240]})
        if self.path == "/sync":
            with _sync_lock:
                _git(["add", "-A"])
                changed = bool(_git(["status", "--porcelain"]).stdout.strip())
                if changed:
                    _git(["commit", "-m", req.get("msg", "jarvis: manual sync")])
                push = _git(["push"]) if GIT_REPO else None
            return self._send(200, {"committed": changed, "pushed": bool(push and push.returncode == 0)})
        if self.path == "/task":
            data = load_tasks()
            tid = f"t{int(time.time())}"
            task = {"id": tid, "title": req.get("title", "untitled"), "status": "open",
                    "created": int(time.time()),
                    "steps": [{"text": s, "status": "pending", "note": ""} for s in req.get("steps", [])]}
            data["tasks"].append(task)
            save_tasks(data)
            schedule_sync(f"jarvis: task {tid} created")
            return self._send(200, task)
        if self.path == "/task/step":
            data = load_tasks()
            for t in data["tasks"]:
                if t["id"] == req.get("id") and 0 <= int(req.get("step", -1)) < len(t["steps"]):
                    st = t["steps"][int(req["step"])]
                    st["status"] = req.get("status", st["status"])
                    st["note"] = req.get("note", st["note"])
                    save_tasks(data)
                    schedule_sync(f"jarvis: {t['id']} step {req['step']} {st['status']}")
                    return self._send(200, t)
            return self._send(404, {"error": "task/step not found"})
        if self.path == "/task/status":
            data = load_tasks()
            for t in data["tasks"]:
                if t["id"] == req.get("id"):
                    t["status"] = req.get("status", t["status"])
                    save_tasks(data)
                    schedule_sync(f"jarvis: {t['id']} {t['status']}")
                    return self._send(200, t)
            return self._send(404, {"error": "task not found"})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    boot_sync()
    print(f"JarvisOS 0.5 agent on :{PORT} workspace={WORKSPACE} token={'set' if TOKEN else 'UNSET'} "
          f"git={'on' if GIT_REPO else 'off'} cap={EXEC_CAP_MB}MB "
          f"watch={'moli' if shutil.which(MOLI_BIN) else 'unavailable'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
