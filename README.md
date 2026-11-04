# Demostrador EUIPO — Caso de uso 1 (denegaciones absolutas)

Aplicación web (FastAPI + HTML/JS sin dependencias) que implementa la tarjeta
«Implementar demostrador EUIPO (Caso de Uso 1)» con el diseño del mockup:

1. **Analizar una marca**: nombre + bienes y servicios desde (a) texto, (b) un `.txt`
   o (c) un desplegable con 10 marcas de la partición de test (semilla fija).
2. El **modelo discriminativo** dice si hay denegación absoluta y con qué probabilidad.
3. Si la hay, el **modelo generativo** (Aitana 2B instruido) da el tipo y la explicación.
4. **Evaluación por lotes**: sube un `.jsonl` con `name`, `goods_and_services` y
   `absolute_refusal`; compara la predicción con la etiqueta y muestra aciertos/fallos,
   gráfica por clase, matriz de confusión, tabla de fallos y CSV descargable.

## Archivos

| Archivo | Qué es |
|---|---|
| `app.py` | servidor, carga de modelos y API |
| `static/` | frontend (`index.html`, `estilo.css`, `app.js`) |
| `ejemplos.json` | ids fijos del lote de ejemplo (200 marcas de test, 30 denegadas; el v1 las acierta todas) |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | ejecución con Docker |
| `lanzar_demo.slurm` | lanzamiento en lamarr con GPU |
| `requirements.txt` | dependencias |

Los dos modelos están en `modelos/` con **Git LFS** (unos 5,5 GB). Antes de clonar:
`git lfs install`. El generativo está partido en 4 archivos `model-0000X-of-00004.safetensors`
(con `model.safetensors.index.json`), porque GitHub no admite archivos LFS de más de 2 GB;
transformers lo carga igual. El discriminativo es el **v1**, el publicado en Hugging Face
(`gplsi/Aitana-euipo-absolute-refusals-1.0`, checkpoint-17212). La demo normaliza la entrada
(quita el punto final de cada clase) para esquivar su sesgo del punto; ver más abajo.

Los `.jsonl` (`test.jsonl`, `predictions.jsonl`) no están en el repositorio: van en la carpeta de arriba.

## Con Docker

Estructura esperada (la misma que en `demoEUIPO`; `modelos/` ya viene en el repositorio):

```
demoEUIPO/
├── test.jsonl
├── predictions.jsonl
└── app/                     ← este repositorio
    └── modelos/
        ├── discriminativo/  (config.json, model.safetensors, tokenizer*)
        └── generativo/      (config.json, model.safetensors, tokenizer*, chat_template.jinja)
```

```bash
cd app
cp .env.example .env          # opcional: cambiar rutas, puerto u opciones
docker compose up --build     # http://localhost:8000
```

- La imagen trae PyTorch para CPU. Con GPU NVIDIA: `docker compose build --build-arg
  TORCH_INDEX=https://download.pytorch.org/whl/cu124` y descomentar `deploy` en `docker-compose.yml`.
- En CPU el generativo en bfloat16 necesita unos 6 GB de RAM para Docker. Con `OPCIONES=--sin-generador`
  solo se usan las explicaciones precalculadas de `predictions.jsonl`.
- Si falta `test.jsonl` o `predictions.jsonl`, la demo arranca igual, pero sin desplegable/lote
  de ejemplo o sin explicaciones precalculadas (Docker crea una carpeta vacía en su lugar).

Datos que usa (de la carpeta `demoEUIPO`):
- `test.jsonl`: partición de test (candidatos del desplegable y lote de ejemplo).
- `predictions.jsonl`: salidas del generativo para las 2.199 marcas denegadas del test.

## Modelos

- Discriminativo: ModernBERT (`ModernBertForSequenceClassification`), `id2label` = {0: false, 1: true}.
- Generativo: Aitana 2B (Llama) con plantilla ChatML (`chat_template.jinja`), que antepone el
  system prompt propio de Aitana al de la tarea, igual que en el entrenamiento con axolotl.
- Hace falta `transformers` 5.x: el config del generativo se guardó con la 5.14.

## Sesgo del discriminativo: el punto final

En el dataset, el 82 % de las marcas denegadas terminan los términos de sus clases con «.»
y solo el 0,1 % de las aceptadas, porque vienen de fuentes distintas. El modelo lo ha aprendido
como atajo (`prueba_formato.py`, 150 + 150 marcas de test):

| | tal cual | sin punto final | con punto final |
|---|---|---|---|
| denegadas → % predicho denegación | 94 % | 53 % | 98 % |
| aceptadas → % predicho denegación | 0 % | 0 % | 87 % |

Por eso la app **normaliza siempre** la entrada (quita el punto final de los términos de cada
clase, como en el crawl del registro) y decide con esa probabilidad; la probabilidad sin
normalizar se muestra al lado como referencia, también en la tabla y el CSV de lotes.
Si la probabilidad normalizada es ≥ 50 % (`--umbral`) se llama al generativo; si no, no.

Hay un segundo atajo, el **idioma**: el 36,5 % de las denegadas tienen los bienes y servicios
en otro idioma (letras acentuadas, alemán, español...) frente al 2,7 % de las aceptadas, porque
las aceptadas vienen casi siempre en inglés. Una marca aceptada escrita en español sale como
denegación (p. ej. SAMUEL SMITH: inglés 0,000; español 1,000; español normalizado 0,554).
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

## Arrancar en lamarr (modelos reales)

```bash
# una vez
cd ~/demo_euipo            # esta carpeta copiada a lamarr
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# copia test.jsonl y predictions.jsonl a ~/demoEUIPO (o exporta DATOS=/otra/ruta)

sbatch lanzar_demo.slurm
cat demo_euipo_<id>.log    # imprime el túnel SSH
```

En tu ordenador: `ssh -N -L 8000:<nodo>:8000 usuario@lamarr` y abre http://localhost:8000.

Sin Slurm (en un nodo con GPU directamente):

```bash
python app.py --test test.jsonl --predicciones predictions.jsonl \
  --clasificador /home/gplsi/GPLSI/codigos/transformer_model_classifier/models/comment_euipo_refusals_v1/checkpoint-17212 \
  --generador /home/gplsi/GPLSI/ALIA/modelos/Instruction/EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1/final
```

## Arrancar en tu ordenador con los modelos copiados

Si existen `app/modelos/discriminativo/` y `app/modelos/generativo/` (copiados de lamarr:
el checkpoint-17212 y la carpeta `final` del generativo), y `test.jsonl` y
`predictions.jsonl` están en la carpeta de arriba (`demoEUIPO`), basta con:

```powershell
cd C:\Users\UA\Documents\extras\demoEUIPO\app
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

y abrir http://localhost:8000. Arrancar tarda alrededor de un minuto, porque lee todo `test.jsonl` y carga los dos modelos.
En CPU el generativo (2B) ocupa unos 9 GB de RAM y tarda bastante en cada explicación;
`--usar-precalculadas` usa `predictions.jsonl` cuando la marca está ahí, y
`--sin-generador` no lo carga (solo explicaciones precalculadas) y `--precision bfloat16`
lo carga con la mitad de RAM (unos 4,5 GB).

## Probar la interfaz sin modelos

```bash
pip install fastapi uvicorn python-multipart
python app.py --test test.jsonl --predicciones predictions.jsonl
```

Sin `--clasificador` (y sin `app/modelos/discriminativo/`) el discriminativo queda **simulado** (usa la etiqueta del test y la
web lo avisa con una franja naranja). Sin `--generador` las explicaciones salen de
`predictions.jsonl`, así que solo funcionan para marcas denegadas del test.
(`torch` y `transformers` solo hacen falta con modelos reales.)

## Opciones útiles

| Opción | Por defecto | |
|---|---|---|
| `--umbral` | 0.5 | probabilidad a partir de la cual es denegación |
| `--usar-precalculadas` | no | con generador cargado, usar igualmente `predictions.jsonl` si la marca está (más rápido) |
| `--tokenizador` | el del checkpoint | si el checkpoint no guarda el tokenizador, la ruta del modelo base |
| `--muestreo` | `estratificado` | `estratificado` = 5 denegadas + 5 no; `aleatorio` = muestreo simple (con semilla 42 sale 0 denegadas, porque solo el 1,4 % lo son) |
| `--semilla`, `--n-candidatos` | 42, 10 | desplegable |
| `--ejemplo-n`, `--ejemplo-pos` | 200, 30 | lote de ejemplo del botón «Usar lote de ejemplo» |
| `--lote` | 32 | tamaño de lote del discriminativo en la evaluación |
| `--dispositivo` | auto | `cuda`, `cuda:1`, `cpu`… |

La etiqueta positiva del clasificador se toma de `id2label` (`True`, `LABEL_1`, …);
si no se reconoce se asume el índice 1.

## API

`GET /api/estado`, `GET /api/candidatos`, `POST /api/clasificar` y `POST /api/explicar`
(`{"name", "goods", "candidato_id"?}`), `POST /api/lote` (multipart `archivo`),
`POST /api/lote/ejemplo`, `GET /api/lote/{id}`, `GET /api/lote/{id}/csv`.
