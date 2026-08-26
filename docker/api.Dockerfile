# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a
FROM ${PYTHON_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml requirements.lock ./
RUN python -m pip install --index-url "${PIP_INDEX_URL}" --no-cache-dir -r requirements.lock && \
    python -m playwright install --with-deps chromium && \
    chmod -R a+rX ${PLAYWRIGHT_BROWSERS_PATH}

# Copy the actual source code
COPY . .

RUN python -m pip install --no-cache-dir --no-deps . && \
    python -m pip check

# Create non-root user and assign permissions
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8100

CMD ["python", "-m", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8100"]
