FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Keep the runtime rooted at /app so BACKEND_DATA_DIR=backend/var resolves
# consistently for both local Compose and later container-hosted installs.
WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

COPY backend/__init__.py ./backend/__init__.py
COPY backend/app.py ./backend/app.py
COPY backend/cli.py ./backend/cli.py
COPY backend/api/ ./backend/api/
COPY backend/bootstrap/ ./backend/bootstrap/
COPY backend/core/ ./backend/core/
COPY backend/debug/ ./backend/debug/
COPY backend/memory/ ./backend/memory/
COPY backend/realtime/ ./backend/realtime/
COPY backend/tools/ ./backend/tools/
COPY backend/vision/ ./backend/vision/
COPY backend/ws/ ./backend/ws/

EXPOSE 8080

CMD ["python", "-m", "backend.cli", "serve"]
