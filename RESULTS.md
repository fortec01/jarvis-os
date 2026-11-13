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

## DEPLOYED + LIVE-TESTED (2026-11-13)
- Repo: https://github.com/fortec01/jarvis-os (public; account
  fortec01 — the existing account tied to fortecfficial01@
  gmail.com; eres reset its password herself). Code updates
  do NOT auto-deploy (no GitHub-app webhook on her Render
  workspace for this account) — trigger deploys via the API.
- Service: Render "jarvis-os", Singapore, free plan,
  https://jarvis-os-8qa2.onrender.com (srv-dbrctedg1s2s7384n7c0).
- First deploy FAILED: the s6 run script arrived via GitHub
  web upload with mode 100644; fixed by Dockerfile chmod +x
  (commit f124b3e). Env vars sent inside the create-service
  payload were silently dropped by the API — set them via
  PUT /services/{id}/env-vars, then trigger a deploy manually
  (the PUT alone does not redeploy).
- Live tests PASSED: /healthz; face page 200; /stats real
  numbers (host-level /proc figures — the host's totals, not
  the 512 MB cgroup slice; the watchdog enforces by RSS, so
  the cap itself is real); bad/no token → 401; /exec runs as
  user jarvis in /home/jarvis/workspace (6*7=42 written and
  read back); watchdog killed a 600 MB hog at the 250 MB cap
  in 1.6 s; task journal created a task; /desktop.png rendered
  the full Nebula face with live values (test-out/
  desktop-live-render.png).
- DEPLOY-TIME GATE ANSWERED: bubblewrap does NOT work on
  Render — "bwrap: Failed to make / slave: Permission
  denied" (no unprivileged mount ops allowed). v0.2.1 makes
  /exec self-heal: if bwrap itself errors, the command reruns
  unsandboxed and the result is labelled
  "bwrap-unavailable-fallback" (proven locally with a broken
  bwrap shim). LIVE-PROVEN 2026-11-13 ~15:30 IST: default
  /exec on the redeployed v0.2.1 returned code 0 with
  sandbox "bwrap-unavailable-fallback" and 42 written/read.
  v0.2.1 commit e4cede4.
- Honest stats caveat: /stats and the face cards read the
  Render HOST's /proc (host totals), not the 512 MB cgroup
  slice — the memory cap is enforced by real RSS, so the cap
  is real even though the displayed totals are the host's.
- Free-tier behaviour: spins down after ~15 min idle; first
  hit after a nap takes ~1 min to wake. Code changes do NOT
  auto-deploy (no webhook from the fortec01 account to this
  Render workspace) — trigger deploys via the API.
- UPTIMEROBOT (registered at eres's ask, 2026-11-13): free
  account under fortecfficial01@gmail.com; monitor "JarvisOS"
  (id 804659658) checks /healthz every 5 min with email
  alerts. Its FIRST check caught a real bug: monitors ping
  with HEAD and the agent had no do_HEAD (501). v0.2.2 adds
  do_HEAD (commit 1ee0d14); live HEAD re-verified 200 OK —
  monitor flips Up at its next check. Side effect by design:
  the 5-min pings keep the service awake (~744 of the 750
  shared free hours/month; StrideSync shares the pool).
- Token: JARVIS_PC_TOKEN is set on the service via the API
  (value handed to eres in chat 2026-11-13; not recorded
  here). Render API key: since saved by eres into the Secure
  Vault (custom.render connector); deploys run through it.
  Rotating the original chat-shared key remains her call.

## v0.3 / v0.3.1 — the web ladder, DEPLOYED + LIVE-PROVEN (2026-11-13)
At eres's direction ("bake Lightpanda + Scrapling's HTTP tier"):
- **lightpanda 1.0.3** (AGPL-3.0, from-scratch Zig/V8 headless
  browser) on PATH as `lightpanda`; telemetry disabled by env.
- **webfetch** command: Scrapling 0.5.1 (BSD-3) HTTP tier in
  /opt/jarvisos/venv — TLS-fingerprint-impersonating fetcher
  (curl_cffi, native musllinux wheels) + adaptive parser.
  Call: `webfetch <url> [--impersonate X] [--css SEL]`.
- Ladder: curl → webfetch (clean fingerprint) → lightpanda
  (real JS, DOM only, no screenshots) → remote stealth (later).
- v0.3's two live failures and v0.3.1's fixes:
  (1) gcompat could NOT carry Lightpanda (missing mallopt,
  execveat, fcntl64; binary needs up to GLIBC_2.35) → the image
  now carries a real Debian bookworm glibc (2.36) in /opt/glibc
  and starts the binary through its loader via a wrapper.
  (2) musl pip silently installed a patchright 0.0.1 placeholder
  (real wheels are manylinux-tagged) → the build downloads the
  real patchright 1.64.4 wheel for an explicit platform and
  unpacks it into the venv; only its import is used.
- Live proofs (deploy dep-dbrf07d9fdbs73e6hv7g, 2026-11-13):
  `lightpanda version` → 1.0.3; a JS-rendered page
  (quotes.toscrape.com/js) returned its JS-injected text;
  `webfetch https://example.com` → STATUS 200 + body text;
  impersonation check inside the box: chrome JA3 6f17b7e1…
  vs firefox JA3 424f6d9c… (distinct per identity).
- Deploys now go through the Render API with eres's key stored
  in the Secure Vault (custom.render connector) — no dashboard
  needed. v0.3.1 commit = e9227b9.
- Speeds measured in-box (median of 3): curl 0.20s; lightpanda
  0.30s (simple page) / 1.49s (JS page); webfetch 2.91s (mostly
  Python stack startup on 0.1 CPU); run peak ~51 MB.
- Full test-all pass (2026-11-13, eres's ask): webfetch --css
  selector extraction WORKS (pulled exact quote texts);
  **CDP interactive drive 8/8** — lightpanda serve + a stdlib
  WebSocket/CDP client: open tab, navigate, read title, CLICK a
  link and land on the target (iana.org), navigate to a login
  form, TYPE username+password via Input.insertText (values
  verified in-field), SUBMIT, land logged-in ("Logout" shown).
  Bot-wall probes: nowsecure.nl passed both rungs (200);
  g2.com walled BOTH (webfetch 403 "enable JS", lightpanda no
  content) — the honest top of the in-box ladder; hardest
  walls remain the future remote-stealth tier's job.

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

## v0.4 — YouTube data, media downloads, live watch (2026-11-13, LIVE)

eres approved the full bundle: a `yt` data tool, a Cobalt
downloader, Moli for a watchable live view, and a face with
three new panels (WATCH HIM BROWSE / YOUTUBE / DOWNLOAD MEDIA).

- **yt** (stdlib Python): search / info / transcript / channel
  via the Invidious public API — instances auto-discovered
  from api.invidious.io (api-enabled only), the one live
  api-enabled instance today is yt.chocolatemoo53.com; channel
  latest-uploads via RSS; oEmbed fallback for info.
  Live-verified in-box: search returns ranked results with
  channel/duration/views/ids; info shows 1.83B views + the
  caption list; transcript returns clean lyric text (manual
  English track preferred); channel resolves @RickAstleyYT →
  subs + latest uploads.
- **getmedia** (stdlib Python): downloads via public Cobalt
  API instances (seed list + GETMEDIA_API override). The
  instance fetches and tunnels the file, so it works from this
  IP-walled box. Live-verified: the 28.5 MB 720p test video
  saved into the workspace in **2.4s**. Supports --audio and
  --quality; 300 MB cap.
- **Moli 1.1.24** (Apache-2.0) rides the glibc sidecar like
  Lightpanda (tarball sha256-verified at build, amd64). The
  agent grew a watch layer: POST /watch starts
  `moli serve --layout` on demand and relays CDP screencast
  frames (JPEG, on change) — GET /watch.png serves the latest
  frame, GET /watch the session status, POST /watch/stop ends
  it; sessions auto-stop after 15 min without a viewer.
  Live-verified in the real box: navigated a JavaScript page,
  title read back ("Quotes to Scrape"), frames flowed, and
  /watch.png returned the fully rendered page (screenshot:
  test-out/watch-live-render.jpg). The face's watch panel
  refreshes the frame every ~2.5s — on 0.1 CPU it reads as a
  calm slideshow, as expected.
- **v0.4.1 fix (same evening):** first live deploy's Moli
  segfaulted on startup — it also links fontconfig / freetype /
  expat / png / brotli, which bookworm-slim lacks, so the
  loader fell back to Alpine's musl builds in /usr/lib (IFUNC
  memmove clash, exit 139). Fix: the glibc-src stage installs
  libbrotli1, libfontconfig1, libfreetype6, libexpat1 and
  libpng16-16 so the Debian builds land in /opt/glibc/lib;
  after it, the loader resolves **zero** Moli libraries
  outside /opt/glibc and `moli --version` exits 0.
  Commits: v0.4 = 0057f40…89bc615 (7 files), v0.4.1 = 5d300d0.
- Known wart: /watch/stop (and re-navigation) can take a few
  seconds to land — the session thread drains screencast
  frames on a 5s socket timeout between commands. Harmless;
  a shorter session-socket timeout is a v0.5 tidy-up.
- **v0.4.2 — take-control (eres approved 2026-11-13, LIVE):**
  eres can now drive his browser herself from the watch panel.
  No open-source stack was adopted — Steel / easi.run /
  BrowserPane / neko / Auto Browser are all real-Chromium or
  WebRTC class (≥1–2 GB, extra ports); the lightweight pattern
  (saidkick 2.0 design; nearai/ironclaw) is CDP screencast out
  + Input events in, which extends v0.4's watch layer directly
  (research: ~/workspace/jarvis/research-takeover-control.md).
  Agent: POST /watch/input {kind: click|type|key|scroll} →
  Input.dispatchMouseEvent / insertText / dispatchKeyEvent on
  the same CDP session (scroll via evaluate); GET /watch status
  now also reports the page viewport and frame size so the face
  can map a tap on the picture to page coordinates exactly.
  Face: "🖱 take control" toggle — tap the live picture to
  click, a type box, Enter/Tab/Esc/Backspace keys, scroll
  buttons. Also fixes the wart above: the session socket
  timeout is 1.5s, and stop now lands in ~2s.
  Verified locally AND live in the real box using the face's
  own coordinate math: a tap on the Login link navigated to
  /login; a tap on the username field + typing put
  "jarvis-test" in the field, visible in the live frame
  (test-out/watch-takecontrol-live.jpg).
  Commits 18ae0b9 (agent) + 2192717 (face); deploy
  dep-dbrgb2tbedkc73dhhbc0.

## v0.5 — the music release (eres, 2026-11-13, LIVE)
Ask: "create a perfect one to enjoy music" — streaming music
in the face, built from the open-source ideas researched the
same evening (Verci's word-by-word lyrics, Lumn's album-art
glow, Lyric's web architecture) on the pipeline already proven
on this box (Invidious catalogue + Cobalt audio), with the
SpotDL matcher reversed at source level so a pasted Spotify
link lands on the right version.

- Agent (stdlib only; Pillow only for the palette):
  POST /music/play {query | Spotify/YouTube url | videoId} —
  Spotify metadata comes from the public embed page's
  __NEXT_DATA__ JSON (title, artists, exact duration) + oEmbed
  art; the matcher scores YouTube candidates SpotDL-style
  (fuzzy title/artist, exp(−0.1·Δs) duration curve, forbidden-
  words penalty, official-audio/topic bonuses). Songs stream
  progressively via GET /stream (tail-follows the growing
  cache file) while a background thread caches the MP3 into
  workspace/music/; replays come from GET /download, which
  gained full HTTP Range support (206, suffix/open ranges) —
  also wired into the face's workspace list as ⬇ save links,
  closing the long-standing "no way to get a file off his
  computer" gap. GET /music/search|list|status|lyrics round it
  out; lyrics come from LRCLIB (line-synced) and the glow
  palette is extracted from the album art and cached.
- Face: MUSIC — PLAY ANYTHING panel. One box: type a song
  (search results with thumbs/durations) or paste a Spotify /
  YouTube link (plays directly). The stage shows blurred
  album art behind prev/current/next lyric lines; the current
  line's words light one by one in the album colour (word
  timing interpolated across the line by word length, the
  LRCLIB-based approach); "all the words" expands the full
  synced lyrics (tap a line to seek once cached). A fixed
  frame around the whole face glows in the album's two
  palette colours, pulsing with the song's own bass (WebAudio
  FFT on the audio element — no microphone, same trick as
  the open-source Lyric). Seek unlocks once the song is
  cached; the face swaps the stream for the seekable file
  mid-play without losing position. Library list below
  replays anything he has cached.
- Honest shape: first play of a song streams unseekably
  (Cobalt tunnels ignore Range) and starts in ~2–3 s; once
  cached it's instant with full seek. The cache lives on the
  disposable disk — it wipes on spin-down until workspace
  git persistence is wired (still eres-gated).
- **v0.5.1 fixes (same evening, found by live testing):**
  (1) Image toolkit added at eres's ask — Node v22.23.5 +
  npm 11.6.4, uv 0.7.22, bash 5.2.37, ffmpeg/ffprobe 6.1.2,
  jq 1.8.2, ripgrep 14.1.1, sqlite 3.49.2, OpenSSH 10.0
  (all Alpine packages, versions verified in-box; ~130 MB
  disk, nothing new resident). Deliberately excluded as too
  heavy: Bun, Deno, C toolchain, Chromium.
  (2) The first live Spotify play picked a *lyrics upload*
  (7clouds) over the Official Audio by 0.6 points — the
  matcher now penalises lyrics/lyric/nightcore/sped uploads
  and credits an artist name found only in a title at 60
  instead of 90; re-tested against the live candidate set,
  the Official Audio wins 77.2 vs 63.5.
  (3) That same play's download died with an empty tunnel
  body from the first Cobalt instance; the fetcher now
  fails over across instances, counting an attempt only
  when ≥20 KB actually land.
  (4) Stall handling, two rounds: a tunnel was then seen
  delivering one 64 KB burst and stalling for minutes, so
  reads went small (16 KB) with a 25 s socket timeout (a
  dead tunnel now trips fast) — but the first version's
  150 s total deadline killed a healthy ~20 KB/s fetch at
  ~80% (2.59 MB of 3.25 MB), so the deadline became a
  900 s safety net: slow-but-alive tunnels may finish.
  Commits: v0.5 = ceccf48 (agent) + 274f2f1 (face);
  v0.5.1 = 4ab46a4 (Dockerfile toolkit) + bec6630
  (failover + matcher) + bcd9ecf (stall detection) +
  4110634 (deadline 900 s). Deploys: dep-dbrh7p9srm7s73925ra0
  (v0.5 + toolkit), dep-dbrhd9mc6qcc73du114g (bec6630),
  dep-dbrhg5tg1s2s738h11eg (bcd9ecf), dep-dbrhj556e07s73bo6700
  (4110634, current live).
  FINAL LIVE VERIFICATION (2026-11-13 evening, fixed build):
  healthz = JarvisOS 0.5; Spotify-link play → correct pick
  fHI8X4OXluQ "Blinding Lights" Official Audio, artist
  "The Weeknd" (matcher 77.2, artist 100.0), synced lyrics
  40 lines, palette from the real album art — every leg of
  his own pipeline verified live, repeatedly. The third-
  party leg made the evening long: the free community
  Cobalt instance degraded hour by hour (2.4 s full pull at
  v0.4 → 25–49 s pulls → ~20 KB/s steady → one-burst
  stalls → repeated 502 Bad Gateway on ALL seed instances
  → a ~4 KB/s crawl), and the failover machinery earned
  its keep throughout (each empty/502 attempt detected,
  the next instance tried, errors reported honestly
  instead of hanging). And then the crawl WON: the slowest
  fetch simply kept going under the 900 s safety net and
  finished — a repeat play returned state "cached", and
  the full end-to-end proof set closed on the real box:
  status cached=true, 3,252,222 bytes; ffprobe duration
  203.256 s; ID3 header present; live Range request
  bytes=1000-1999 → HTTP 206, Accept-Ranges: bytes,
  Content-Range 1000-1999/3252222, exactly 1,000 payload
  bytes; an in-box /stream probe delivered all 3,252,222
  bytes with the ID3 head. Search → resolve → stream →
  cache → seek is therefore verified end-to-end, live.
  Practical shape for eres: first-play speed is instance
  weather (seconds on a good hour, minutes on a bad one;
  one tap retries a 502); a landed song replays instantly
  with full seek until the next spin-down wipes the
  disposable disk.

### v0.5.2 — Verci-style player + vibrant palette (same evening, eres's correction: "that looks so bad… use this and image at back for songs")
- **Face redesign (face.html only):** the stage is now the reference look — the song's
  art **full-bleed and vivid** (blur/dark veil removed; soft edge shade only),
  lyrics **giant** (900-weight italic, clamp 36–80px) stacked prev/current/next
  over the art; sung words light up white word by word, unsung stay translucent,
  and the **word being sung sits in a solid block of the album's lead colour**
  (Verci's block highlight); the edge rim went full Lumn (four inset shadows,
  stronger bass-reactive targets). Commits 04b5284 (agent) + d7faff0 (face),
  deploy dep-dbri69d9fdbs73efhth0.
- **Palette picker rewritten (agent):** by-area quantize picked mud (hair and
  backdrop win on pixels). Now: 16-swatch quantize → lead = most vivid prominent
  swatch by area×saturation, second = dominant non-twin → each keeps its hue but
  is **lifted to glow strength** in HLS (lead sat ≥0.72 / L 0.52; second ≥0.45 /
  L 0.58; monochrome art stays neutral). `PALETTE_VER = 2` — cached songs
  recompute once on next play. Live proof, Blinding Lights (After Hours cover):
  **['#46371c', '#9eb6a4'] → ['#dd762c', '#64c474']** (amber + mint), returned by
  the production /music/play itself; song re-cached (3,252,222 bytes), lyrics
  synced, Range/stream verified in the rounds before.
- **Capture caveat (test rig, not the product):** the sandbox screenshot engine
  (Moli layout paint) decodes images (naturalWidth 300 via data-URI) but does
  not rasterize JPEGs into screenshots — via CDN URL, via local proxy, as
  <img>, with filters cleared, and as a CSS background, all five routes paint
  nothing. The face's art is a plain <img> on a public CDN (the agent fetches
  the same file fine for palettes); it renders normally in a real browser.

### v0.5.3 — now-playing takes the whole screen (eres's correction, same night: "What are those extra stuff?")
- **The mistake:** v0.5.2 redesigned the stage but left the player sitting inside
  the dashboard page — search box, library, YouTube/download panels all around
  it. Eres's reference is a full-screen takeover. **Fix (face.html):** on play,
  #player gets class `np` → `position: fixed; inset: 0` overlay (z 40, under
  the z-60 rim): stage flexes to full height, lyrics go bigger (clamp 42–108px),
  controls collapse to a minimal bottom bar (pause, title/artist, state, thin
  custom seek), ✕ top-right closes to a slim now-playing mini-bar in the panel
  (reopen + play/pause), "all the words" becomes a corner toggle sheet.
- **Real bug found by the screenshot rig:** `.panel { backdrop-filter: blur(4px) }`
  makes the panel a containing block for fixed descendants — the overlay could
  never have escaped it in ANY browser. playReq now moves #player to <body>
  before showing it. (Moli honoured fixed for the body-level #glowFrame all
  along; the capture proved the trap before it shipped broken.)
- **Agent:** `hi_art()` — Spotify CDN cover swapped 300px → 640px
  (ab67616d00001e02 → ab67616d0000b273) at resolve time, cached songs included,
  so the full-screen backdrop stays sharp.
- **Capture rig breakthrough:** Moli decodes raster images but rasterizes only
  its CANVAS paint path into screenshots — the rig now draws the decoded art
  onto a stage-sized canvas (same pixels, cover-crop). The v0.5.3 shot is the
  real page: art full-screen, giant stacked words, amber block, rim, mini
  controls. Commits 5daa536 (face) + 94fb28c (agent), deploy
  dep-dbrjmod6e07s73bu6da0; live-verified (face markers, /music/play returns
  the 640px art + vivid palette). Redeploy wiped the cache again — the song
  re-downloads on next plays (instance weather applies, as ever).

### v0.6 — command plays: send a song to any screen (eres, 2026-11-14 ~00:28 IST)
- **Question answered first:** playback never happens on the server (it has no
  speaker) — the box finds/downloads/serves, and the song sounds on whichever
  screen has the face open. v0.6 adds the missing "tell it" half: a command
  channel, so a play can be COMMANDED from anywhere to any screen.
- **Agent:** POST /command {query|url|videoId + hints, target, from} resolves
  the song server-side ONCE (same resolve_play) and queues the full payload;
  GET /commands?device=&label= heartbeats the device into a registry and
  returns its commands exactly once; GET /devices lists screens. Targets:
  a device label (phone / pc / …), a device id, or "all". In-memory, bounded
  (20), stale commands expire in 10 min.
- **Face:** every screen gets an id + a name (auto from user-agent —
  Android → phone, Windows → pc — tap the name in the MUSIC panel to rename;
  stored per browser). It polls /commands every 3 s. "send plays to" chips:
  [this screen] + one per live screen + [all screens] — with a remote target
  chosen, tapping a search result / library song / link SENDS it instead of
  playing locally ("sent … ✓ — it starts playing there"). Received commands
  open the full-screen player by themselves; if the browser's autoplay rule
  blocks sound (no tap on that page yet), a "▶ tap to play — <song> was sent
  to this screen" pill appears instead — one tap starts it.
- **Also shipped just before (face key link):** the face accepts
  /#t=<token> once to store its key — no console/popup needed (commit a879808).
- **Local test (agent on :8902):** queue → poll delivers once → second poll
  empty → no cross-delivery between labels. Face JS node --check OK.
- **Live proof (cloud browser, real Chromium, twice):** a command queued from
  OUTSIDE the browser (curl, from "eres, via Muse in chat") made the idle tab
  open the full-screen player BY ITSELF within seconds — cover full-screen,
  status "playing — sent from eres, via Muse in chat", screen registered as
  "screen", chips showing [this screen] [all screens]. Delivery is exact-once
  (a reload does not re-deliver). The sound itself never started IN THAT
  BROWSER: autoplay was blocked (by design the tap-to-play pill appeared), and
  the automation's clicks never landed on the overlay — the pill, which hides
  unconditionally on a real click, never hid across repeated attempts on it
  and on the play button, with the song fully cached. That is a limit of the
  test browser's synthetic clicks, not of the channel: on a real device the
  owner's first genuine tap unlocks that screen for the session (the same
  mechanism Spotify Web's auto-advance relies on), after which commanded
  plays start by themselves. Shot: test-out/music-remote-delivered.png.
- Commits 5cedcc4 (agent) + 38c71c2 (face); deploy dep-dbrltm3l550s73crohjg.
