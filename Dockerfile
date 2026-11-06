# Demostrador EUIPO (caso de uso 1). Los modelos se consumen por API (LiteLLM); los .jsonl se montan como volúmenes.
#   docker compose up --build          -> http://localhost:4545 (externo; interno 8000)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py prueba_formato.py ejemplos.json ./
COPY static ./static

# app.py busca por defecto ../test.jsonl y ../predictions.jsonl
RUN useradd --create-home --uid 1000 demo && chown -R demo /srv
USER demo
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/estado')"
ENTRYPOINT ["python", "app.py", "--host", "0.0.0.0", "--port", "8000"]
