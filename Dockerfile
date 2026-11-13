# JarvisOS — Jarvis's own work computer, as one small Docker image.
# Deploys to Render free (512 MB) as a single web service; the same
# image also runs unchanged on a bigger host if it ever moves.
#
# Base: Alpine Linux — the smallest complete container OS
# (~7 MB Docker base image; musl libc + BusyBox).
# Supervision: s6-overlay (MIT wrapper over the ISC s6 suite) as
# PID 1 — if the agent dies, s6 restarts it automatically.
FROM alpine:3.22

ARG TARGETARCH
ARG S6_VERSION=v3.2.0.0

# python3      -> the agent runtime (stdlib-only code, zero pip deps)
# py3-pillow   -> the visual shell renderer
# font-dejavu  -> widget text
# git, curl    -> workspace sync (pull on boot, push per finished step)
# bubblewrap   -> per-command sandboxing for /exec
# xz           -> build-time only, unpacks the s6-overlay tarballs
RUN apk add --no-cache python3 py3-pillow font-dejavu git curl ca-certificates bubblewrap xz \
    && case "$TARGETARCH" in \
         amd64|"") S6_ARCH=x86_64 ;; \
         arm64) S6_ARCH=aarch64 ;; \
         *) S6_ARCH=$TARGETARCH ;; \
       esac \
    && curl -fsSL -o /tmp/s6-noarch.tar.xz \
         https://github.com/just-containers/s6-overlay/releases/download/${S6_VERSION}/s6-overlay-noarch.tar.xz \
    && curl -fsSL -o /tmp/s6-arch.tar.xz \
         https://github.com/just-containers/s6-overlay/releases/download/${S6_VERSION}/s6-overlay-${S6_ARCH}.tar.xz \
    && tar -Jxpf /tmp/s6-noarch.tar.xz -C / \
    && tar -Jxpf /tmp/s6-arch.tar.xz -C / \
    && rm /tmp/s6-*.tar.xz \
    && adduser -D jarvis

WORKDIR /opt/jarvisos
COPY agent/agent.py agent/agent.py
COPY shell/desktop.py shell/wallpaper.jpg shell/face.html shell/
COPY rootfs/ /

ENV JARVIS_WORKSPACE=/home/jarvis/workspace \
    PORT=10000 \
    S6_KEEP_ENV=1
RUN mkdir -p /home/jarvis/workspace && chown -R jarvis:jarvis /home/jarvis /opt/jarvisos

# Host env vars required at runtime (never in the image):
#   JARVIS_PC_TOKEN  - the agent's auth token
#   JARVIS_GIT_REPO  - private workspace repo URL (optional, for sync)
#   JARVIS_GIT_TOKEN - token for that repo (optional)
ENTRYPOINT ["/init"]
