# Demostrador EUIPO — Caso de uso 1 (denegaciones absolutas)

Aplicación web (FastAPI + HTML/JS sin dependencias) que implementa la tarjeta
«Implementar demostrador EUIPO (Caso de Uso 1)» con el diseño del mockup:

1. **Analizar una marca**: nombre + bienes y servicios desde (a) texto, (b) un `.txt`
   o (c) un desplegable con 10 marcas de test (semilla fija) y un botón que trae otras 10 al azar
   de un grupo de 100.
2. El **modelo discriminativo** dice si hay denegación absoluta y con qué probabilidad.
3. Si la hay, el **modelo generativo** (Aitana 2B instruido) da el tipo y la explicación.
4. **Evaluación por lotes**: sube un `.jsonl` con `name`, `goods_and_services` y
   `absolute_refusal`; compara la predicción con la etiqueta y muestra aciertos/fallos,
   gráfica por clase, matriz de confusión, tabla de fallos y CSV descargable.

## Archivos

| Archivo | Qué es |
|---|---|
| `app.py` | servidor, cliente de los modelos (LiteLLM) y API |
| `static/` | frontend (`index.html`, `estilo.css`, `app.js`) |
| `ejemplos.json` | ids fijos del lote de ejemplo (200 marcas de test, 30 denegadas; el v1 las acierta todas) |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | ejecución con Docker |
| `lanzar_demo.slurm` | lanzamiento con GPU |
| `requirements.txt` | dependencias |

Los dos modelos **no se cargan en local**: se consumen por API desde el gateway LiteLLM
(`https://litellm.tools.gplsi.es`) con **una única API key** para ambos:

- `POST /v1/classify` → `Aitana-euipo-absolute-refusals-1.0` (discriminativo, v1)
- `POST /v1/chat/completions` → `EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1` (generativo)

La clave se toma de `LITELLM_API_KEY` (fichero `.env` o variable de entorno), que tiene prioridad.
Si no está definida, la web muestra un campo para escribirla; se valida con una llamada y se guarda
solo en memoria del servidor (no en disco). Sin clave, el discriminativo queda simulado y las
explicaciones salen de `predictions.jsonl`. La carpeta `modelos/` y `herramientas/` ya no se usan.

Los `.jsonl` (`test.jsonl`, `predictions.jsonl`) no están en el repositorio: van en la carpeta de arriba.

## Con Docker

Estructura esperada (la misma que en `demoEUIPO`):

```
demoEUIPO/
├── test.jsonl
├── predictions.jsonl
└── app/                     ← este repositorio
```

```bash
cd app
cp .env.example .env          # pon LITELLM_API_KEY (o déjala vacía y escríbela en la web)
docker compose up --build     # http://localhost:8000
```

- La imagen es ligera (sin PyTorch ni modelos).
- Si falta `test.jsonl` o `predictions.jsonl`, la demo arranca igual, pero sin desplegable/lote
  de ejemplo o sin explicaciones precalculadas (Docker crea una carpeta vacía en su lugar).

Datos que usa (de la carpeta `demoEUIPO`):
- `test.jsonl`: partición de test (candidatos del desplegable y lote de ejemplo).
- `predictions.jsonl`: salidas del generativo para las 2.199 marcas denegadas del test.

## Despliegue automático (GitHub Actions + GHCR + Watchtower)

Cada push a `main` ejecuta `.github/workflows/docker.yml`, que construye la imagen y la publica en
`ghcr.io/leurzz/demo-euipo` (tags `latest` y `sha-<commit>`). Watchtower, en el servidor, comprueba
el registro cada 5 minutos y recrea el contenedor `demo` si hay una imagen nueva.

**Una vez, en GitHub:** Settings → Actions → General → *Workflow permissions* → «Read and write».
Haz un primer push (o lanza el workflow a mano) y, en el paquete publicado, decide la visibilidad:
pública (más simple) o privada.

**Una vez, en el servidor:**

```bash
mkdir -p /opt/demo-euipo && cd /opt/demo-euipo
# copia aquí docker-compose.yml y .env.example (renómbralo a .env y pon LITELLM_API_KEY)
# copia test.jsonl y predictions.jsonl y ajusta TEST_JSONL / PREDICCIONES_JSONL en .env
docker compose up -d        # crea el contenedor «demo-euipo»
```

Se reutiliza el Watchtower que ya corre en el servidor. Ese Watchtower solo vigila los contenedores
cuyo nombre se le pasa como argumento, así que hay que relanzarlo **añadiendo `demo-euipo`** y
conservando lo demás (`DOCKER_API_VERSION`, intervalo, otros nombres):

```bash
docker stop watchtower && docker rm watchtower
docker run -d --name watchtower --restart unless-stopped   -e DOCKER_API_VERSION=1.44   -v /var/run/docker.sock:/var/run/docker.sock   containrrr/watchtower --interval 120 --cleanup web-summer-school web-cidegent demo-euipo
```

Si el paquete de GHCR es privado: `docker login ghcr.io -u leurzz` (PAT con `read:packages`) y añade
al `docker run` `-v $HOME/.docker/config.json:/config.json:ro -e DOCKER_CONFIG=/`.
Comprueba con `docker logs -f watchtower`.

Notas: en el servidor pon la clave en `.env` (la escrita en la web vive solo en memoria y se pierde
al recrear el contenedor). Para desarrollar en local: `docker compose -f docker-compose.dev.yml up --build`.

## Modelos

- Discriminativo: ModernBERT; el endpoint `/v1/classify` devuelve la probabilidad de cada clase y la demo
  usa la de la positiva (`true`, `LABEL_1`, …). El parser acepta varios formatos de respuesta habituales.
- Generativo: Aitana 2B con plantilla ChatML; se llama con `messages` (system + user), `temperature=0`
  y `max_tokens=--max-new-tokens`. Si el servidor rechaza el rol `system`, se funde en el mensaje de usuario.

## Sesgo del discriminativo: el punto final

En el dataset, el 82 % de las marcas denegadas terminan los términos de sus clases con «.»
y solo el 0,1 % de las aceptadas, porque vienen de fuentes distintas. El modelo lo ha aprendido
como atajo (`prueba_formato.py`, 150 + 150 marcas de test):

| | tal cual | sin punto final | con punto final |
|---|---|---|---|
| denegadas → % predicho denegación | 94 % | 53 % | 98 % |
| aceptadas → % predicho denegación | 0 % | 0 % | 87 % |

La app usa la entrada **tal cual, sin normalizar**, igual que en el entrenamiento: así acierta
las marcas del dataset, pero una marca escrita a mano con punto final tras los términos tenderá
a salir como denegación. Si la probabilidad es ≥ 50 % (`--umbral`) se llama al generativo.

Hay un segundo atajo, el **idioma**: el 36,5 % de las denegadas tienen los bienes y servicios
en otro idioma (letras acentuadas, alemán, español...) frente al 2,7 % de las aceptadas, porque
las aceptadas vienen casi siempre en inglés. Una marca aceptada escrita en español sale como
denegación (p. ej. SAMUEL SMITH: inglés 0,000; español 1,000).
En la demo conviene escribir los bienes y servicios en inglés; para el reentrenamiento hay que
equilibrar idiomas o usar siempre la versión inglesa.

## Formatos de entrada a los modelos (los mismos del entrenamiento)

- Discriminativo: `NOMBRE: 28. términos 35. términos` (script de parseo de la tarjeta).
- Generativo: system prompt de la tarjeta + usuario
  `Analyze the following refused trademark application:\n\n[TRADEMARK NAME]\n{name}\n\n[GOODS AND SERVICES]\n{dicts unidos por "; "}`.
  Comprobado: reconstruye exactamente las 2.199 entradas de `predictions.jsonl`.
- La salida `[REFUSAL TYPE] … [SUMMARY EXPLANATION] …` se separa en tipo y explicación.

En el cuadro de texto los bienes y servicios van **una clase por línea** con su número
delante (`9. Computer games`). Si eliges un candidato y no lo editas, se usa el
`goods_and_services` original del dataset, sin pérdidas.

## Arrancar sin Docker

```bash
cd app
python -m venv .venv && source .venv/bin/activate     # en Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                  # y rellena LITELLM_API_KEY
python app.py --test ../test.jsonl --predicciones ../predictions.jsonl   # http://localhost:8000
```

En lamarr: `sbatch lanzar_demo.slurm` (mira el log para el túnel SSH).

Sin API key la interfaz arranca igual: el discriminativo queda **simulado** (usa la etiqueta del test,
con franja naranja) y las explicaciones solo existen para marcas denegadas de `predictions.jsonl`.

## Opciones útiles

| Opción | Por defecto | |
|---|---|---|
| `--umbral` | 0.5 | probabilidad a partir de la cual es denegación |
| `--usar-precalculadas` | no | con API key, usar igualmente `predictions.jsonl` si la marca está (más rápido) |
| `--muestreo` | `estratificado` | `estratificado` = 5 denegadas + 5 no; `aleatorio` = muestreo simple (con semilla 42 sale 0 denegadas, porque solo el 1,4 % lo son) |
| `--semilla`, `--n-candidatos`, `--grupo-candidatos` | 42, 10, 100 | desplegable y botón aleatorio (elige 10 de un grupo de 100) |
| `--ejemplo-n`, `--ejemplo-pos` | 200, 30 | lote de ejemplo del botón «Usar lote de ejemplo» |
| `--lote` | 32 | tamaño de lote del discriminativo en la evaluación |
| `--base-url`, `--modelo-clasificador`, `--modelo-generador` | ver `.env.example` | gateway y nombres de modelo (también `LITELLM_BASE_URL`, `CLASIFICADOR_MODEL`, `GENERADOR_MODEL`) |

## API

`GET /api/estado`, `POST /api/clave` (`{"clave"}`), `GET /api/candidatos` (`?aleatorio=1`), `POST /api/clasificar` y `POST /api/explicar`
(`{"name", "goods", "candidato_id"?, "peticion_id"?}`), `POST /api/detener/{peticion_id}`
(corta la generación en curso), `POST /api/lote` (multipart `archivo`), `POST /api/lote/ejemplo`,
`GET /api/lote/{id}`, `POST /api/lote/{id}/detener` (para y deja los resultados de lo ya clasificado),
`GET /api/lote/{id}/csv`.
