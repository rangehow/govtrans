FROM python:3.12-slim

# Install system dependencies (curl for healthcheck, build tools if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy pyproject.toml first to enable efficient layer caching for dependencies
COPY pyproject.toml ./

# Create minimal stub packages so setuptools find_packages() succeeds during dependency install layer cache
RUN mkdir -p apps services agents pipelines tools evaluation && \
    touch apps/__init__.py services/__init__.py agents/__init__.py pipelines/__init__.py tools/__init__.py evaluation/__init__.py

# Install runtime dependencies including psycopg2-binary for PostgreSQL support
RUN pip install --no-cache-dir psycopg2-binary && \
    pip install --no-cache-dir .

# Copy the actual source code
COPY . .

# Re-install package code (without reinstalling dependencies) to register modules/entry points
RUN pip install --no-cache-dir --no-deps .

# Create non-root user and assign permissions
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8100
