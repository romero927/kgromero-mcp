FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --create-home app
USER app

ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["kgromero-mcp", "--http"]
