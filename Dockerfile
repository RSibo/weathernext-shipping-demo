# Showcase image: the React app built with Node, served by FastAPI. `scripts/deploy.sh` builds it
# with `gcloud run deploy --source .`. Adapted from
# hcls-capstone-dev/gauravks-fde-k8s-copilot/backend/Dockerfile.

FROM node:22-slim AS frontend
WORKDIR /app/frontend
# .npmrc carries legacy-peer-deps=true, which the lockfile was resolved with; without it `npm ci`
# fails on peer ranges.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.0
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Locked runtime dependencies only (no dev extra, and not the project itself: the app runs from
# /app via PYTHONPATH). This layer stays cached until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY config.py schemas.py ./
COPY agent/ agent/
COPY api/ api/
COPY tools/ tools/
# Includes the replay cube, recorded routes and golden runs: gitignored (data terms), but uploaded
# by .gcloudignore so they live only in this private image.
COPY data/ data/
COPY --from=frontend /app/frontend/dist frontend/dist

ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app PYTHONUNBUFFERED=1

# Cloud Run injects PORT. `exec` makes uvicorn PID 1, so the SIGTERM at scale-in reaches it and
# in-flight SSE streams close cleanly instead of being cut when the grace period ends.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
