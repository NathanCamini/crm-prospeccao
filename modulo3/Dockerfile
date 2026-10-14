# Contexto: esta pasta, mais o módulo 1 como contexto extra "etl" (ver docker-compose.yml),
# de onde vem o serviço de contatos (prospeccao.enrich), instalado sem as dependências pesadas.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY --from=etl pyproject.toml /opt/etl/pyproject.toml
COPY --from=etl src /opt/etl/src
RUN pip install --no-deps /opt/etl
COPY pyproject.toml ./
COPY modulo3 ./modulo3
COPY tests ./tests
RUN pip install ".[dev]" && useradd --uid 1000 --create-home modulo3 && mkdir -p /dados && chown modulo3 /dados

USER modulo3
ENV MODULO3_DADOS_DIR=/dados
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/saude', timeout=4)"
CMD ["uvicorn", "modulo3.main:app", "--host", "0.0.0.0", "--port", "8100"]
