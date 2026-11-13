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
#                 It is glibc-linked (libc+libm only), so Alpine's
#                 gcompat shim carries it.
#   webfetch    — Scrapling 0.5.1 HTTP tier (BSD-3) in a venv:
#                 TLS-fingerprint-impersonating fetcher (curl_cffi,
#                 which ships native musllinux wheels) + parser.
#                 The browser/stealth tier of Scrapling is NOT
#                 installed — it needs Chromium-class RAM.
FROM alpine:3.22

ARG TARGETARCH
ARG S6_VERSION=v3.2.0.0
ARG LIGHTPANDA_VERSION=1.0.3

# python3      -> the agent runtime (stdlib-only code, zero pip deps)
# py3-pillow   -> the visual shell renderer
# font-dejavu  -> widget text
# git, curl    -> workspace sync (pull on boot, push per finished step)
# bubblewrap   -> per-command sandboxing for /exec
# gcompat      -> glibc shim so the Lightpanda binary runs on musl
# py3-pip      -> builds the Scrapling venv below
# xz           -> build-time only, unpacks the s6-overlay tarballs
RUN apk add --no-cache python3 py3-pillow font-dejavu git curl ca-certificates bubblewrap gcompat py3-pip xz \
    && case "$TARGETARCH" in \
         amd64|"") S6_ARCH=x86_64; LP_ARCH=x86_64 ;; \
         arm64) S6_ARCH=aarch64; LP_ARCH=aarch64 ;; \
         *) S6_ARCH=$TARGETARCH; LP_ARCH=$TARGETARCH ;; \
       esac \
    && curl -fsSL -o /tmp/s6-noarch.tar.xz \
         https://github.com/just-containers/s6-overlay/releases/download/${S6_VERSION}/s6-overlay-noarch.tar.xz \
    && curl -fsSL -o /tmp/s6-arch.tar.xz \
         https://github.com/just-containers/s6-overlay/releases/download/${S6_VERSION}/s6-overlay-${S6_ARCH}.tar.xz \
    && tar -Jxpf /tmp/s6-noarch.tar.xz -C / \
    && tar -Jxpf /tmp/s6-arch.tar.xz -C / \
    && rm /tmp/s6-*.tar.xz \
    && curl -fsSL -o /usr/local/bin/lightpanda \
         https://github.com/lightpanda-io/browser/releases/download/${LIGHTPANDA_VERSION}/lightpanda-${LP_ARCH}-linux \
    && chmod +x /usr/local/bin/lightpanda \
    && adduser -D jarvis

WORKDIR /opt/jarvisos
COPY agent/agent.py agent/agent.py
COPY shell/desktop.py shell/wallpaper.jpg shell/face.html shell/
COPY tools/ tools/
COPY rootfs/ /
# The s6 run script must be executable in the image even when the
# git checkout stored it as 0644 (e.g. uploaded via GitHub web UI).
RUN chmod +x /etc/services.d/jarvisos/run tools/webfetch \
    && ln -s /opt/jarvisos/tools/webfetch /usr/local/bin/webfetch

# Scrapling HTTP tier in its own venv (musl-native wheels for
# curl_cffi / lxml / orjson; patchright is imported by Scrapling
# but its browser driver is never launched in this tier).
RUN python3 -m venv /opt/jarvisos/venv \
    && /opt/jarvisos/venv/bin/pip install --no-cache-dir \
         "scrapling==0.5.1" curl_cffi patchright browserforge

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
