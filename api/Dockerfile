FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first so the dependency layer survives source edits.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY provenance ./provenance

EXPOSE 8000

CMD ["uvicorn", "provenance.main:app", "--host", "0.0.0.0", "--port", "8000"]
