# ApplyCanary — single image running the dashboard and the in-process scheduler.
#
# Three stages so neither the C toolchain (for Python wheels) nor Node and
# node_modules (for the React bundle) reach the final image. Runtime carries
# only the interpreter, installed packages and the built static assets.

# ---------------------------------------------------------------- frontend
FROM node:22-slim AS frontend

WORKDIR /fe
# Manifests alone first, so this layer caches on dependency changes rather than
# on every source edit.
COPY frontend/package.json frontend/package-lock.json* ./
# `npm ci` when a lockfile is present, `npm install` otherwise — the repo is
# usable either way.
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else npm install --no-audit --no-fund; fi

COPY frontend/ ./
# Build the production bundle in frontend/dist.
RUN npm run build

# ---------------------------------------------------------------- build
FROM python:3.12-slim AS build

# gcc is needed by any dependency without a prebuilt wheel for this platform
# (rapidfuzz and pydantic-core publish wheels, but a pip resolver picking an
# older release can still fall back to source).
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install into a virtualenv so the whole tree copies to the runtime stage as one
# self-contained directory.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copied alone so this layer caches on dependency changes, not code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="ApplyCanary" \
      org.opencontainers.image.description="Job discovery, ATS scoring and application agent" \
      org.opencontainers.image.source="https://github.com/StephenJarso/applycanary"

# curl serves the healthcheck below. tzdata lets TZ resolve to a real zone, which
# the digest and GitHub-refresh cron triggers depend on for correct local hours.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gosu tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Bind all interfaces: 127.0.0.1 inside a container is unreachable from the
    # host. The security boundary is the published port, which docker-compose
    # pins to the host loopback. See the comment there before changing it.
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/applycanary.db

# Run unprivileged. Created before the code copy so ownership is set in one pass.
RUN useradd --create-home --uid 10001 canary \
 && mkdir -p /data \
 && chown -R canary:canary /data

WORKDIR /app
COPY --chown=canary:canary app/ ./app/
COPY --chown=canary:canary run.py ./
# Read at runtime from the working directory (app/pipeline/ingest.py:31), so it
# must be present in the image. Override with a bind mount to edit the curated
# board list without rebuilding.
COPY --chown=canary:canary companies.yaml ./
COPY --chown=root:root docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Built React bundle from the frontend stage. Copied after app/ so it is not
# overwritten, and served from / by app/main.py.
COPY --from=frontend --chown=canary:canary /fe/dist ./frontend/dist

USER root
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Declared for documentation; compose does the actual publishing.
EXPOSE 8000

# Persistent data is mounted by the hosting platform at runtime.
# /health reports scheduler state, so a hung scheduler surfaces as unhealthy
# rather than staying invisible behind a listening socket.
HEALTHCHECK --interval=60s --timeout=10s --start-period=25s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null || exit 1

CMD ["python", "run.py"]
