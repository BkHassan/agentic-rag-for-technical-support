# Runtime image stays small: compile-only tools never leave the builder.
FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .
RUN grep -Ev '^(pytest|[[:space:]]*#|[[:space:]]*$)' requirements.txt > /tmp/runtime.txt \
    && pip install --upgrade pip \
    && pip install -r /tmp/runtime.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp

# onnxruntime (pulled in by Chroma) needs libgomp at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY app ./app
COPY data/technical_docs ./data/technical_docs
COPY docker/start.py ./docker/start.py

RUN mkdir -p /app/data/chroma && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# One process. Local Chroma uses SQLite, which is not safe across workers.
CMD ["python", "docker/start.py"]
