# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a
FROM ${PYTHON_IMAGE}

ARG TOFU_WHEEL_SHA256=681ddbeaf599b7932308e42a5ae7330064dc4bbe9c68e2b30af6561d3f9daca8
ARG PIP_INDEX_URL=https://pypi.org/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/tofu
COPY vendor/tofu-agent/requirements.lock /tmp/tofu-requirements.lock
COPY vendor/tofu-agent/tofu_agent-0.17.0-py3-none-any.whl /tmp/tofu_agent-0.17.0-py3-none-any.whl

RUN echo "${TOFU_WHEEL_SHA256}  /tmp/tofu_agent-0.17.0-py3-none-any.whl" | sha256sum -c - && \
    python -m pip install --index-url "${PIP_INDEX_URL}" --no-cache-dir -r /tmp/tofu-requirements.lock && \
    python -m pip install --no-cache-dir --no-deps /tmp/tofu_agent-0.17.0-py3-none-any.whl && \
    python -m pip check && \
    useradd --create-home --uid 10001 --shell /usr/sbin/nologin tofu && \
    mkdir -p /home/tofu/.config/tofu-agent /workspace && \
    chown -R tofu:tofu /home/tofu/.config /workspace

WORKDIR /workspace
ENV TOFU_AGENT_HOST=0.0.0.0 \
    TOFU_AGENT_PORT=15001 \
    TOFU_AGENT_CONFIG_PATH=/home/tofu/.config/tofu-agent/provider.json \
    MALLOC_ARENA_MAX=2 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 15001
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=12 \
    CMD curl -fsS http://localhost:15001/health/ready || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["tofu-agent", "serve"]
USER tofu
