# Use Python 3.12 slim for a small image
FROM python:3.12-slim

# Prevent .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer cache)
COPY company/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the company source code into /app
COPY company/ ./

# Copy agent category directories into /agent_repo/ so agents.py can find them.
# Using individual COPY instructions keeps them outside /app and matches
# the relative path expected by config.py (REPO_ROOT = parent of company/).
COPY academic/           /agent_repo/academic/
COPY design/             /agent_repo/design/
COPY engineering/        /agent_repo/engineering/
COPY finance/            /agent_repo/finance/
COPY game-development/   /agent_repo/game-development/
COPY marketing/          /agent_repo/marketing/
COPY paid-media/         /agent_repo/paid-media/
COPY product/            /agent_repo/product/
COPY project-management/ /agent_repo/project-management/
COPY sales/              /agent_repo/sales/
COPY spatial-computing/  /agent_repo/spatial-computing/
COPY specialized/        /agent_repo/specialized/
COPY strategy/           /agent_repo/strategy/
COPY support/            /agent_repo/support/
COPY testing/            /agent_repo/testing/

# Tell config.py where the repo root is when running in the container.
# REPO_ROOT is used by agents.py to locate the *.md files.
ENV REPO_ROOT=/agent_repo

# Railway injects $PORT at runtime; default to 8000 for local runs
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
