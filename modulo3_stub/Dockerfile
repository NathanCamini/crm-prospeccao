FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY modulo3 ./modulo3
COPY tests ./tests
RUN pip install ".[dev]" && useradd --uid 1000 --create-home modulo3

USER modulo3
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/saude', timeout=4)"
CMD ["uvicorn", "modulo3.main:app", "--host", "0.0.0.0", "--port", "8100"]
