FROM node:26-bookworm-slim AS node-runtime

FROM python:3.14-slim-bookworm AS python-deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.14-slim-bookworm

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install only the runtime OS packages needed for Themarr's media pipeline.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN groupadd --system themarr && \
    useradd --system --gid themarr --home-dir /nonexistent --shell /usr/sbin/nologin themarr

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
RUN ln -sf /usr/local/bin/node /usr/local/bin/nodejs

COPY --from=python-deps /opt/venv /opt/venv
COPY --chown=themarr:themarr app/ app/
COPY --chown=themarr:themarr templates/ templates/
COPY --chown=themarr:themarr static/ static/

EXPOSE 8080

USER themarr

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "120", "app.web_app:app"]
