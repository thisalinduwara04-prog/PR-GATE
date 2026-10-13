# One Dockerfile, four targets (B5/B7):
#   backend : FastAPI app, worker, git, SQLite. Holds the GitHub token and webhook secret.
#   scanner : Semgrep, Gitleaks, OSV-Scanner behind an internal HTTP API. Holds no secrets.
#   agent   : the Claude Code CLI behind an internal HTTP API. Holds only the Claude token.
#   test    : scanner + git + dev dependencies, for scripts/test.sh.
# Each runtime target runs as its own non-root user (uids 1000 / 1100 / 1200).

ARG PYTHON_IMAGE=python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce

# ---------------------------------------------------------------------------------------------------
# Downloads: checksum-verified release binaries. curl/xz stay in this stage, not in the runtime images.
FROM ${PYTHON_IMAGE} AS downloads
ARG NODE_VERSION=20.20.2
ARG GITLEAKS_VERSION=8.30.1
ARG OSV_SCANNER_VERSION=2.6.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp/dl
RUN curl -fsSLO --retry 5 --retry-all-errors https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
 && curl -fsSL --retry 5 --retry-all-errors https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt | grep " node-v${NODE_VERSION}-linux-x64.tar.xz$" | sha256sum -c - \
 && mkdir -p /opt/node && tar -xJf node-v${NODE_VERSION}-linux-x64.tar.xz -C /opt/node --strip-components=1
RUN curl -fsSLO --retry 5 --retry-all-errors https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz \
 && curl -fsSL --retry 5 --retry-all-errors https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_checksums.txt | grep " gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz$" | sha256sum -c - \
 && mkdir -p /opt/bin && tar -xzf gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz -C /opt/bin gitleaks
RUN curl -fsSLO --retry 5 --retry-all-errors https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_linux_amd64 \
 && curl -fsSL --retry 5 --retry-all-errors https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_SHA256SUMS | grep " osv-scanner_linux_amd64$" | sha256sum -c - \
 && install -m 0755 osv-scanner_linux_amd64 /opt/bin/osv-scanner

# ---------------------------------------------------------------------------------------------------
# Base: Python runtime dependencies shared by every service.
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
RUN python -c "import tomllib,subprocess,sys; p=tomllib.load(open('pyproject.toml','rb'))['project']; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*p['dependencies']])"
RUN mkdir -p /workspaces /data

# ---------------------------------------------------------------------------------------------------
FROM base AS backend
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home app && chown app:app /data /workspaces
COPY app /app/app
COPY config /app/config
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------------------------------
FROM base AS scanner
ARG SEMGREP_VERSION=1.177.0
# Semgrep gets its own virtualenv: its dependencies (e.g. a newer starlette) must not clash with the service.
RUN python -m venv /opt/semgrep \
 && /opt/semgrep/bin/pip install --no-cache-dir semgrep==${SEMGREP_VERSION} \
 && ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep
COPY --from=downloads /opt/bin/gitleaks /opt/bin/osv-scanner /usr/local/bin/
RUN useradd --uid 1100 --create-home scanner
COPY app /app/app
COPY config /app/config
ENV SEMGREP_ENABLE_VERSION_CHECK=0 \
    SEMGREP_SEND_METRICS=off
USER scanner
EXPOSE 9000
CMD ["uvicorn", "app.services.scanner:create_scanner_app", "--factory", "--host", "0.0.0.0", "--port", "9000"]

# ---------------------------------------------------------------------------------------------------
FROM base AS agent
ARG CLAUDE_CODE_VERSION=2.1.278
COPY --from=downloads /opt/node/bin /usr/local/bin/
COPY --from=downloads /opt/node/lib /usr/local/lib/
COPY --from=downloads /opt/node/include /usr/local/include/
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} && npm cache clean --force
RUN useradd --uid 1200 --create-home agent
COPY app /app/app
ENV DISABLE_AUTOUPDATER=1
USER agent
EXPOSE 9100
CMD ["uvicorn", "app.services.agent:create_agent_app", "--factory", "--host", "0.0.0.0", "--port", "9100"]

# ---------------------------------------------------------------------------------------------------
FROM scanner AS test
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
RUN python -c "import tomllib,subprocess,sys; p=tomllib.load(open('pyproject.toml','rb'))['project']; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*p['optional-dependencies']['dev']])"
RUN useradd --uid 1000 --create-home app
COPY tests /app/tests
USER app
CMD ["pytest", "-q"]
