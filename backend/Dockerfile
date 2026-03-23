ARG PORTWORLD_NODE_IMAGE=node:22-bookworm-slim

FROM ${PORTWORLD_NODE_IMAGE} AS node-runtime

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Keep the runtime rooted at /app so BACKEND_DATA_DIR=backend/var resolves
# consistently for both local Compose and later container-hosted installs.
WORKDIR /app

COPY --from=node-runtime /usr/local/ /usr/local/

COPY pyproject.toml ./pyproject.toml
COPY backend/README.md ./backend/README.md
COPY backend/__init__.py ./backend/__init__.py
COPY backend/cli.py ./backend/cli.py
COPY portworld_cli/ ./portworld_cli/
COPY backend/api/ ./backend/api/
COPY backend/bootstrap/ ./backend/bootstrap/
COPY backend/core/ ./backend/core/
COPY backend/extensions/ ./backend/extensions/
COPY backend/infrastructure/ ./backend/infrastructure/
COPY backend/memory/ ./backend/memory/
COPY backend/realtime/ ./backend/realtime/
COPY backend/tools/ ./backend/tools/
COPY backend/vision/ ./backend/vision/
COPY backend/ws/ ./backend/ws/

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["python", "-m", "backend.cli", "serve"]
