# One image, three commands: api, worker, and the one-shot migrate job.
# Splitting into three would triple the build for zero benefit at this size.

FROM python:3.13-slim-trixie

# Pinned by version, not :latest — this is a supply-chain surface.
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependency layer. Only the manifests are copied, so editing source does not
# invalidate the (slow) resolve+download step.
# --frozen because uv cannot validate the lock against workspace members whose
# pyproject.toml files it hasn't seen yet; --no-install-workspace because the members
# themselves arrive in the next layer.
COPY pyproject.toml uv.lock ./
COPY packages/schemas/pyproject.toml packages/schemas/
COPY packages/db/pyproject.toml packages/db/
COPY apps/api/pyproject.toml apps/api/
COPY apps/workers/pyproject.toml apps/workers/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-install-workspace --no-dev

# Source layer.
COPY packages packages
COPY apps apps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-dev

# The source tree stays in the image on purpose: alembic.ini resolves
# script_location relative to itself, so `alembic -c packages/db/alembic.ini` works
# without a second copy of the migrations.

RUN useradd --create-home --uid 1000 app && chown -R app:app /app

# Beat's schedule volume mounts here. The directory has to exist and be owned by `app`
# *in the image*: Docker seeds a fresh named volume from the image path, so a path that
# does not exist gets created root-owned and beat cannot write its schedule.
RUN mkdir -p /var/lib/celery && chown app:app /var/lib/celery

USER app

EXPOSE 8000

CMD ["fastapi", "run", "apps/api/src/api/main.py", "--host", "0.0.0.0", "--port", "8000"]
