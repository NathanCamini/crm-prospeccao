FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CRM_DADOS_DIR=/dados

WORKDIR /app
COPY pyproject.toml ./
COPY crm ./crm
COPY tests ./tests
RUN pip install ".[dev]" && useradd --uid 1000 --create-home crm && mkdir -p /dados && chown crm /dados

USER crm
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/saude', timeout=4)"
CMD ["python", "-m", "crm", "servir", "--host", "0.0.0.0", "--porta", "8000"]
