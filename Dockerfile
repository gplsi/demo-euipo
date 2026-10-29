# Demostrador EUIPO (caso de uso 1). Los modelos y los .jsonl NO van en la imagen: se montan como volúmenes.
#   docker compose up --build          -> http://localhost:8000
# Por defecto instala PyTorch para CPU. Para GPU NVIDIA:
#   docker compose build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124
FROM python:3.12-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --index-url "$TORCH_INDEX" "torch>=2.1" \
 && pip install -r requirements.txt

COPY app.py prueba_formato.py ejemplos.json ./
COPY static ./static

# app.py busca por defecto: ./modelos/discriminativo, ./modelos/generativo y ../test.jsonl, ../predictions.jsonl
RUN useradd --create-home --uid 1000 demo && mkdir -p modelos && chown -R demo /srv
USER demo
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/estado')"
ENTRYPOINT ["python", "app.py", "--host", "0.0.0.0", "--port", "8000"]
