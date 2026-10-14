FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PROSPECCAO_DATA_DIR=/app/data \
    HOME=/tmp

WORKDIR /app

# Dependências primeiro (melhor uso de cache de camadas)
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[dev]"

COPY tests ./tests

# Usuário sem privilégios; a pasta de dados é montada como volume
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data /app/trabalho && chown -R app:app /app \
    && chmod 1777 /app/trabalho
USER app

EXPOSE 8501

ENTRYPOINT ["prospeccao"]
CMD ["--help"]
