# JarvisOS — Jarvis's own work computer, as one small Docker image.
# Deploys to Render free (512 MB) as a single web service; the same
# image also runs unchanged on a bigger host if it ever moves.
#
# Base: Alpine Linux — the smallest complete container OS
# (~7 MB Docker base image; musl libc + BusyBox).
# Supervision: s6-overlay (MIT wrapper over the ISC s6 suite) as
# PID 1 — if the agent dies, s6 restarts it automatically.
#
# v0.3 adds the web ladder (agent itself stays stdlib-only):
#   lightpanda  — from-scratch headless browser (Zig/V8, AGPL-3.0),
#                 pinned release binary; runs JS at ~30 MB per page.
#                 It is glibc-linked and needs symbols up to
#                 GLIBC_2.35 that Alpine's gcompat shim lacks
#                 (mallopt, execveat, fcntl64 — v0.3's live failure),
#                 so v0.3.1 carries a REAL glibc (Debian bookworm,
#                 2.36) in /opt/glibc and starts the binary through
#                 its loader via a wrapper script.
#   webfetch    — Scrapling 0.5.1 HTTP tier (BSD-3) in a venv:
#                 TLS-fingerprint-impersonating fetcher (curl_cffi,
#                 which ships native musllinux wheels) + parser.
#                 Scrapling imports patchright at module load; the
#                 real patchright wheels are manylinux-tagged, so
#                 musl pip silently fell back to a 0.0.1 placeholder
#                 (v0.3's other live failure) — v0.3.1 downloads the
#                 real wheel and unpacks it into the venv by hand.
#                 Only its import is used; its browser driver is
#                 never launched. Scrapling's own browser/stealth
#                 tier is NOT installed — Chromium-class RAM.
#
# v0.4 adds eyes, YouTube data and a downloader (agent stays
# stdlib-only; the tools below are stdlib Python too):
#   moli        — from-scratch Rust headless browser (V8 + Servo
#                 Stylo, Apache-2.0), pinned 1.1.24 (tarball
#                 sha256-verified below, amd64). DOM-first like
#                 lightpanda, PLUS on-demand layout: real
#                 screenshots + CDP screencast frames at ~84 MB
#                 measured peak — the agent relays the frames so
#                 eres can watch him browse live (/watch).
#                 glibc-linked like lightpanda → same loader
#                 wrapper through /opt/glibc.
#   yt          — YouTube data tool: search/info/transcripts/
#                 channels via the Invidious public API (auto-
#                 discovered api-enabled instances) + RSS +
#                 oEmbed fallbacks.
#   getmedia    — media downloader via public Cobalt API
#                 instances (the instance fetches and tunnels
#                 the file, so downloads work from this box).
FROM debian:bookworm-slim AS glibc-src

FROM alpine:3.22

ARG TARGETARCH
ARG S6_VERSION=v3.2.0.0
ARG LIGHTPANDA_VERSION=1.0.3
ARG MOLI_VERSION=1.1.24
ARG MOLI_SHA256_AMD64=09e54e48be914df9596e8b9046cd2e1c67d5911761a09b0445488dd5d39c56d6

# python3      -> the agent runtime (stdlib-only code, zero pip deps)
# py3-pillow   -> the visual shell renderer
# font-dejavu  -> widget text
# git, curl    -> workspace sync (pull on boot, push per finished step)
# bubblewrap   -> per-command sandboxing for /exec
# py3-pip      -> builds the Scrapling venv below
# xz           -> build-time only, unpacks the s6-overlay tarballs
COPY --from=glibc-src /usr/lib /opt/glibc-root/lib
RUN apk add --no-cache python3 py3-pillow font-dejavu git curl ca-certificates bubblewrap py3-pip xz \
    && case "$TARGETARCH" in \
         amd64|"") S6_ARCH=x86_64; LP_ARCH=x86_64; MOLI_ARCH=x86_64; GLIBC_LIBDIR=x86_64-linux-gnu; GLIBC_LOADER=ld-linux-x86-64.so.2; PW_PLAT=manylinux1_x86_64 ;; \
         arm64) S6_ARCH=aarch64; LP_ARCH=aarch64; MOLI_ARCH=aarch64; GLIBC_LIBDIR=aarch64-linux-gnu; GLIBC_LOADER=ld-linux-aarch64.so.1; PW_PLAT=manylinux_2_17_aarch64 ;; \
         *) S6_ARCH=$TARGETARCH; LP_ARCH=$TARGETARCH; MOLI_ARCH=x86_64; GLIBC_LIBDIR=x86_64-linux-gnu; GLIBC_LOADER=ld-linux-x86-64.so.2; PW_PLAT=manylinux1_x86_64 ;; \
       esac \
    && curl -fsSL -o /tmp/s6-noarch.tar.xz \
         https://github.com/just-containers/s6-overlay/releases/download/${S6_VERSION}/s6-overlay-noarch.tar.xz \
    && curl -fsSL -o /tmp/s6-arch.tar.xz \
         https://github.com/just-containers/s6-overlay/releases/download/${S6_VERSION}/s6-overlay-${S6_ARCH}.tar.xz \
    && tar -Jxpf /tmp/s6-noarch.tar.xz -C / \
    && tar -Jxpf /tmp/s6-arch.tar.xz -C / \
    && rm /tmp/s6-*.tar.xz \
    && mkdir -p /opt/glibc/lib /opt/jarvisos/bin \
    && cp -a /opt/glibc-root/lib/${GLIBC_LIBDIR}/. /opt/glibc/lib/ \
    && cp -aL /opt/glibc-root/lib/${GLIBC_LIBDIR}/${GLIBC_LOADER} /opt/glibc/${GLIBC_LOADER} \
    && rm -rf /opt/glibc-root \
    && curl -fsSL -o /opt/jarvisos/bin/lightpanda-real \
         https://github.com/lightpanda-io/browser/releases/download/${LIGHTPANDA_VERSION}/lightpanda-${LP_ARCH}-linux \
    && chmod +x /opt/jarvisos/bin/lightpanda-real \
    && printf '#!/bin/sh\nexec /opt/glibc/%s --library-path /opt/glibc/lib /opt/jarvisos/bin/lightpanda-real "$@"\n' "${GLIBC_LOADER}" > /usr/local/bin/lightpanda \
    && chmod +x /usr/local/bin/lightpanda \
    && curl -fsSL -o /tmp/moli.tar.gz \
         https://github.com/lexmount/moli/releases/download/v${MOLI_VERSION}/moli-${MOLI_ARCH}-unknown-linux-gnu.tar.gz \
    && if [ "$MOLI_ARCH" = "x86_64" ]; then echo "${MOLI_SHA256_AMD64}  /tmp/moli.tar.gz" | sha256sum -c -; fi \
    && tar -xzf /tmp/moli.tar.gz -C /tmp \
    && cp /tmp/moli-v${MOLI_VERSION}-${MOLI_ARCH}-unknown-linux-gnu/moli /opt/jarvisos/bin/moli-real \
    && chmod +x /opt/jarvisos/bin/moli-real \
    && rm -rf /tmp/moli.tar.gz /tmp/moli-v${MOLI_VERSION}-${MOLI_ARCH}-unknown-linux-gnu \
    && printf '#!/bin/sh\nexec /opt/glibc/%s --library-path /opt/glibc/lib /opt/jarvisos/bin/moli-real "$@"\n' "${GLIBC_LOADER}" > /usr/local/bin/moli \
    && chmod +x /usr/local/bin/moli \
    && echo "${PW_PLAT}" > /opt/jarvisos/patchright-platform.txt \
    && adduser -D jarvis

WORKDIR /opt/jarvisos
COPY agent/agent.py agent/agent.py
COPY shell/desktop.py shell/wallpaper.jpg shell/face.html shell/
COPY tools/ tools/
COPY rootfs/ /
# The s6 run script must be executable in the image even when the
# git checkout stored it as 0644 (e.g. uploaded via GitHub web UI).
RUN chmod +x /etc/services.d/jarvisos/run tools/webfetch tools/yt tools/getmedia \
    && ln -s /opt/jarvisos/tools/webfetch /usr/local/bin/webfetch \
    && ln -s /opt/jarvisos/tools/yt /usr/local/bin/yt \
    && ln -s /opt/jarvisos/tools/getmedia /usr/local/bin/getmedia

# Scrapling HTTP tier in its own venv (musl-native wheels for
# curl_cffi / lxml / orjson). patchright is imported by Scrapling
# at module load but its browser driver is never launched in this
# tier; its real wheel is manylinux-tagged, so download it with an
# explicit platform and unpack it into the venv by hand (a wheel
# is a zip; the package code is platform-independent Python).
RUN python3 -m venv /opt/jarvisos/venv \
    && /opt/jarvisos/venv/bin/pip install --no-cache-dir \
         "scrapling==0.5.1" curl_cffi browserforge pyee greenlet \
    && SP=$(echo /opt/jarvisos/venv/lib/python3*/site-packages) \
    && /opt/jarvisos/venv/bin/pip download --no-deps --only-binary=:all: \
         --platform "$(cat /opt/jarvisos/patchright-platform.txt)" \
         -d /tmp/pw "patchright==1.64.4" \
    && /opt/jarvisos/venv/bin/python -m zipfile -e /tmp/pw/*.whl "${SP}/" \
    && rm -rf /tmp/pw

ENV JARVIS_WORKSPACE=/home/jarvis/workspace \
    PORT=10000 \
    S6_KEEP_ENV=1 \
    LIGHTPANDA_DISABLE_TELEMETRY=true
RUN mkdir -p /home/jarvis/workspace && chown -R jarvis:jarvis /home/jarvis /opt/jarvisos

# Host env vars required at runtime (never in the image):
#   JARVIS_PC_TOKEN  - the agent's auth token
#   JARVIS_GIT_REPO  - private workspace repo URL (optional, for sync)
#   JARVIS_GIT_TOKEN - token for that repo (optional)
ENTRYPOINT ["/init"]
