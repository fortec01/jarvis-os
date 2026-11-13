# JarvisOS v0.1 — built and measured (2026-11-13)

eres's ask: Jarvis's own OS + "VM" for low-end Render-type servers,
very low RAM, with visuals like the Nebula OS reel (Hyprland +
Quickshell, Material 3 widgets on a sunset wallpaper).

## What "OS" honestly means here
Render cannot run a VM or a custom kernel — it runs containers.
So JarvisOS = a from-scratch container OS image: Alpine Linux base
+ Jarvis's own agent runtime + Jarvis's own visual shell, nothing
else. One image = his whole computer. (Correction, 2026-11-13
deep dig: a true hardware VM is not possible on her A1 either —
Oracle Ampere shapes expose no /dev/kvm, per Oracle's own blog;
QEMU there is TCG emulation at ~1/20 speed. See
~/workspace/jarvis/research-os-vm-repos.md §3.0.)

## Measured on this sandbox (Python 3.12, Pillow 10.2, shared CPU)
| Piece | RAM | Notes |
|---|---|---|
| Agent core idle (stdlib-only HTTP server) | **20.2 MB RSS** | /healthz /stats /exec /desktop.png, token auth |
| Agent after serving a desktop frame | 35.9 MB RSS (peak 64.1 MB) | Pillow loads lazily, only when a frame is asked |
| Visual shell render (standalone) | 64.3 MB peak, **0.39 s** per 1600×900 frame | ~0 MB while idle — the frame is drawn on demand, not displayed live |
| App payload on disk | 36 KB total | agent.py 5.5 KB + desktop.py 8 KB (+ tests) |
| Whole JarvisOS idle | **≈20 MB of the 512 MB Render budget** | leaves ~490 MB for Jarvis's actual jobs |

Live proof from the test run: /exec ran a real command in Jarvis's
own workspace (`6*7 = 42`, wrote note.txt), /stats returned real
/proc numbers, a bad token was rejected with 401, and /desktop.png
served the frame below in 0.63 s end to end.

## The face (test-out/desktop-final.png)
Sunset-gradient wallpaper + sun glow + hills; orange music card
(the reel's song, as a nod); scalloped red clock blob; weather /
status circles and pills; live stat cards (CPU / RAM / DISK / LOAD /
UPTIME — real numbers from /proc); "jarvis is awake" pill.
Same layout language as the reel; none of its code or assets.

## Why not the real Nebula stack
| Stack | Idle RAM |
|---|---|
| Reel setup (creator/comments) | ~4.7 GB total system |
| Hyprland + Quickshell, tuned minimal (Omarchy X220) | ~890 MB desktop idle |
| Quickshell shell process alone (community measurements) | ~124–276 MB RSS |
| **JarvisOS (this build)** | **~20 MB idle, 64 MB peak with frame** |

A real compositor also needs a GPU/display path a headless
container doesn't have. Drawn-on-demand frames give the same look
for ~0 idle cost, viewable any time via the / page or /desktop.png.

## Open-source parts (all licence-clean)
- Alpine Linux — container base, ~7 MB image (musl + BusyBox)
- Python 3 stdlib (PSF) — agent core, zero pip dependencies
- Pillow (HPND) — visual shell renderer
- DejaVu fonts (permissive) — widget text
- Planned in the image's next iteration: s6-overlay (MIT wrapper
  over the ISC s6 suite) for
  supervision per the Render-PC research; matugen (GPL-2.0) to derive
  the Material-You palette from the wallpaper automatically,
  like the real Nebula setups do.

## Estimated image size (Docker unavailable in this sandbox —
estimate from apk package sizes, to verify at first real build)
Alpine ~8 MB + python3 ~55 MB + Pillow ~15 MB + git ~15 MB +
fonts ~3 MB ≈ **~95–110 MB** total image. Disk is not the scarce
resource on Render (RAM is); the number that matters is the
20 MB idle above.

## Visual shell v2 — the full Nebula layout (2026-11-13, eres's ask)
eres: "use same visual from pulling that same screenshot… I want
same for everything it has." The screenshot itself can't be lifted
(it is a photo of a laptop screen — angle, glare, Instagram UI
burned in — and the wallpaper art is its creator's), so:
- Wallpaper: ORIGINAL anime-sunset art generated for JarvisOS
  (shell/wallpaper.jpg, 2048×1152) in the same spirit — blonde
  girl, warm sunset, glowing sky.
- Every element of the reel's lock screen rebuilt on top of it,
  in the same positions: top-centre circle trio (weather,
  J ring, awake ring), orange music card (album art = a crop
  of the wallpaper, the reel's song title/artist, controls,
  Bass/Vocal/Rock toggles, seek dots, EQ bars), scalloped red
  clock blob, right-side notification pills, the centre pill
  (password pill in the reel → "ask jarvis anything…" here),
  and the 3×2 stat cards CPU/RAM/GPU/TEMP/DISK/NET.
- Values stay real: CPU/RAM/DISK/NET live from /proc (agent now
  also reports net_kbps); GPU/TEMP show "— / n/a on this server"
  because a Render box has no GPU or temp sensor.
- Cost unchanged in kind: v2 frame renders in 0.78 s at 53 MB
  peak (PNG is bigger, 1.27 MB, because of the photo wallpaper).

## v0.2 completion pass (2026-11-13, eres: "complete the os")
Built and tested locally (agent idle still **20.1 MB RSS**):
- **Git workspace sync** — boot clone/pull, debounced auto
  commit+push after successful work and task updates, /sync.
  Proven end-to-end against a local bare repo: the remote log
  shows Jarvis's own commits ("jarvis: … step 0 done").
- **RSS watchdog** — every /exec runs under a 250 MB tree-RSS
  cap (JARVIS_EXEC_CAP_MB). Proven: a 600 MB hog was killed in
  0.43 s with the reason reported back.
- **Sandboxed exec** — bubblewrap profile (system read-only,
  workspace read-write, optional no-net mode) wired into /exec;
  bwrap ships in the image via apk. This sandbox has no bwrap,
  so locally it runs the labelled "unavailable-fallback" path;
  the wrapped path is verified at first image build on Render.
- **Task journal** — /task, /task/step, /task/status, /tasks:
  planned steps with done/doing/pending states and notes,
  stored in the workspace (so git sync carries them). Proven.
- **Interactive face** — shell/face.html served at / and /face:
  the Nebula face as a live web desktop (live clock blob, stat
  cards polling /stats, workspace + task panels, an ask box
  that runs on his computer), ~0 MB server cost. The drawn PNG
  remains the shareable snapshot.
- **s6-overlay supervision** — Dockerfile now runs /init as
  PID 1 with the agent as an s6 service (auto-restart on death,
  runs as the unprivileged jarvis user). Coded to the standard
  s6-overlay pattern; exercised at first real image build
  (no Docker in this sandbox).

## Not yet done (deploy-time only)
- Deploy: her Render login (Docker deploy of this folder) +
  JARVIS_PC_TOKEN secret in Render env vars.
- Create the private workspace git repo (her GitHub login) and
  set JARVIS_GIT_REPO / JARVIS_GIT_TOKEN.
- First-build verifications that cannot run in this sandbox:
  the Docker image build itself, the bubblewrap-wrapped exec
  path, s6 auto-restart, and Render's userns permission.
- Optional: snapshot backups to R2 (card on file) or B2 (no
  card) — her choice; external watchdog on the VPS stays
  parked with the rest of the A1/VPS track per her word.
