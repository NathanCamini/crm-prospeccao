FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY agente ./agente
COPY tests ./tests
COPY perfis ./perfis
COPY schema ./schema
RUN pip install ".[dev]" && useradd --uid 1000 --create-home agente

USER agente
ENTRYPOINT ["agente"]
CMD ["--help"]
