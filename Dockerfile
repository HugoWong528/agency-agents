# Use Python 3.12 slim for a small image
FROM python:3.12-slim

# Prevent .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer cache)
COPY company/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the company source code
COPY company/ ./

# Copy all agent *.md files into the image so they can be read at runtime.
# We preserve the same directory structure relative to /app/..
COPY academic/      ../academic/
COPY design/        ../design/
COPY engineering/   ../engineering/
COPY finance/       ../finance/
COPY game-development/ ../game-development/
COPY marketing/     ../marketing/
COPY paid-media/    ../paid-media/
COPY product/       ../product/
COPY project-management/ ../project-management/
COPY sales/         ../sales/
COPY spatial-computing/  ../spatial-computing/
COPY specialized/   ../specialized/
COPY strategy/      ../strategy/
COPY support/       ../support/
COPY testing/       ../testing/

# Railway injects $PORT at runtime; default to 8000 for local runs
EXPOSE 8000

# uvicorn with 2 workers (adjust for Railway plan limits)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
