#!/usr/bin/env python3
"""JarvisOS agent core v0.4 — zero-dependency (Python stdlib only).

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
  GET  /watch        live-view session status
  GET  /watch.png    latest screencast frame (JPEG)
  POST /watch/stop   stop the live-view session

v0.4 adds the watch layer: Moli (Apache-2.0, Rust headless
browser with on-demand layout) runs `serve --layout` on demand;
the agent drives it over CDP with a stdlib WebSocket client and
relays its screencast frames (emitted on change) to the face.
The session auto-stops after 15 min without a viewer.

Workspace persistence: if JARVIS_GIT_REPO is set, the workspace is
cloned/pulled at boot and auto-committed+pushed (debounced) after
work — the Render disk is disposable, the git repo is home.
Auth: X-Jarvis-Token header must equal JARVIS_PC_TOKEN.
"""
import base64
import json
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
from urllib.parse import urlparse, parse_qs

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

    def status(self):
        self.last_access = time.time()
        return {"active": self.active, "url": self.url, "title": self.title,
                "frames": self.frames,
                "frame_age_s": round(time.time() - self.frame_ts, 1) if self.frame_ts else None,
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

            while True:
                try:
                    kind, arg, done, box = self.cmds.get(timeout=0.4)
                except queue.Empty:
                    kind = None
                if kind == "stop":
                    break
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

    def _on_frame(self, ws, params):
        try:
            self.frame = base64.b64decode(params.get("data", ""))
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


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisOS/0.4"

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
            return self._send(200, {"ok": True, "os": "JarvisOS 0.4"})
        if u.path in ("/", "/face"):
            try:
                with open(os.path.join(SHELL_DIR, "face.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            except Exception:
                return self._send(500, {"error": "face missing"})
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
    print(f"JarvisOS 0.4 agent on :{PORT} workspace={WORKSPACE} token={'set' if TOKEN else 'UNSET'} "
          f"git={'on' if GIT_REPO else 'off'} cap={EXEC_CAP_MB}MB "
          f"watch={'moli' if shutil.which(MOLI_BIN) else 'unavailable'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
