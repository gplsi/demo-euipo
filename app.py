"""Demostrador EUIPO — Denegaciones absolutas (art. 7 EUTMR).

Backend FastAPI que sirve el frontend de static/ y expone:
  GET  /api/estado               modelos cargados y modo de cada uno
  GET  /api/candidatos           10 marcas de test (semilla fija; ?aleatorio=1: otras 10 del grupo de 100)
  POST /api/clasificar           modelo discriminativo: ¿denegación absoluta?
  POST /api/explicar             modelo generativo: tipo y explicación
  POST /api/detener/{id}         detiene la explicación en curso con ese peticion_id
  POST /api/lote                 sube un .jsonl y lo evalúa contra absolute_refusal
  POST /api/lote/ejemplo         lote de ejemplo muestreado de test.jsonl
  GET  /api/lote/{id}            progreso y resultados
  POST /api/lote/{id}/detener    detiene el lote; quedan los resultados de lo ya clasificado
  GET  /api/lote/{id}/csv        predicciones de todo el lote

Los dos modelos se consumen por HTTP desde el gateway LiteLLM (misma API key para ambos):
  POST {base}/v1/classify          clasificador
  POST {base}/v1/chat/completions  generador

Uso (ver README.md): definir LITELLM_API_KEY en .env (o escribirla en la interfaz) y
  python app.py --test test.jsonl --predicciones predictions.jsonl
"""
import argparse
import csv
import hashlib
import io
import json
import os
import random
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

AQUI = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(AQUI / ".env")
except ImportError:  # sin python-dotenv, solo variables de entorno reales
    pass

BASE_URL_DEF = "https://litellm.tools.gplsi.es"
MODELO_CLASIFICADOR_DEF = "Aitana-euipo-absolute-refusals-1.0"
MODELO_GENERADOR_DEF = "EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1"

# Prompts del modelo generativo, tal cual en la tarjeta de entrenamiento.
SYSTEM_PROMPT = (
    "You are an expert European Union Intellectual Property Office (EUIPO) trademark examiner. "
    "Your task is to analyze trademark applications that have been refused registration on "
    "absolute grounds under Article 7 of the EUTMR. \n\n"
    "You will be provided with the trademark's \"Name\" and the requested \"Goods and Services\". \n\n"
    "You must evaluate the relationship between the mark and the goods/services to output:\n"
    "1. The exact absolute ground for refusal (e.g., Descriptive, Lack of distinctiveness, Customary, "
    "Deceptive, Geographical indication, Contrary to public policy/morality, etc.).\n"
    "2. A clear, objective, and legal summary explaining why the mark falls under this refusal type.\n\n"
    "Do not include any introductory or concluding remarks. Strictly follow the output format."
)
USER_TMPL = (
    "Analyze the following refused trademark application:\n\n"
    "[TRADEMARK NAME]\n{name}\n\n[GOODS AND SERVICES]\n{goods}"
)


# --------------------------------------------------------------------------
# Formatos de entrada
# --------------------------------------------------------------------------

_CLASE = re.compile(r"(?:^|(?<=\s))([1-9]|[1-3][0-9]|4[0-5])\.\s+")
# Al principio de línea también vale el número sin punto: "32 Beer", "32) Beer", "Clase 32: Beer"
_CLASE_INICIO = re.compile(r"^(?:(?:clase|class|cl\.)\s*)?([1-9]|[1-3][0-9]|4[0-5])\s*[.):\-–]?\s+", re.I)


def texto_a_gs(texto):
    """'28. Juegos; ...\\n35. Publicidad' -> [{'classNumber': 28, 'terms': '...'}, ...].

    Cada línea que empieza por un número de clase de Niza (1-45), con o sin punto
    detrás, abre un elemento; dentro de una línea, '9. a 41. b' también se separa.
    Las líneas sin número se añaden a la clase anterior (o quedan con classNumber '').
    """
    gs = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        m0 = _CLASE_INICIO.match(linea)
        if m0:
            gs.append({"classNumber": int(m0.group(1)), "terms": ""})
            linea = linea[m0.end():]
        marcas = list(_CLASE.finditer(linea))
        previo = linea[: marcas[0].start()] if marcas else linea
        if previo.strip():
            if gs:
                gs[-1]["terms"] = (gs[-1]["terms"] + " " + previo.strip()).strip()
            else:
                gs.append({"classNumber": "", "terms": previo.strip()})
        for i, m in enumerate(marcas):
            fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(linea)
            gs.append({"classNumber": int(m.group(1)), "terms": linea[m.end():fin].strip()})
    return [g for g in gs if g["terms"]]


def gs_a_texto(gs):
    return "\n".join(
        f"{g.get('classNumber', '')}. {str(g.get('terms', '')).strip()}".lstrip(". ") for g in gs
    )


def entrada_discriminativo(name, gs):
    # Igual que el script de parseo del entrenamiento: "NOMBRE: 28. términos 35. términos"
    partes = []
    for g in gs:
        terms = str(g.get("terms", "")).strip()
        if terms:
            partes.append(f"{g.get('classNumber', '')}. {terms}")
    return f"{name}: " + " ".join(partes)


def prompt_generativo(name, gs):
    # Igual que en predictions.jsonl: repr de cada dict unido por "; "
    return USER_TMPL.format(name=name, goods="; ".join(str(g) for g in gs))


_SALIDA = re.compile(r"\[REFUSAL TYPE\]\s*(.*?)\s*\[SUMMARY EXPLANATION\]\s*(.*)", re.S)


def parsear_salida(texto):
    m = _SALIDA.search(texto or "")
    if not m:
        return "", (texto or "").strip()
    return m.group(1).strip(), m.group(2).strip()


def a_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "1", "yes", "si", "sí"}


# --------------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------------

class ClienteLiteLLM:
    """Cliente HTTP del gateway LiteLLM. Una sola API key sirve para los dos modelos.

    Prioridad de la clave: variable de entorno / .env (LITELLM_API_KEY) > la escrita en la interfaz.
    La clave de la interfaz solo vive en memoria del proceso.
    """

    def __init__(self, base_url, clave_env=None):
        self.base_url = base_url.rstrip("/")
        self.clave_env = (clave_env or "").strip() or None
        self.clave_ui = None

    @property
    def clave(self):
        return self.clave_env or self.clave_ui

    @property
    def origen(self):
        return "env" if self.clave_env else ("interfaz" if self.clave_ui else None)

    def post(self, ruta, cuerpo, timeout=180, clave=None):
        clave = clave or self.clave
        if not clave:
            raise HTTPException(401, "Falta la API key de los modelos (LITELLM_API_KEY o campo de la interfaz)")
        try:
            r = httpx.post(self.base_url + ruta, json=cuerpo, timeout=timeout,
                           headers={"Authorization": f"Bearer {clave}"})
        except httpx.HTTPError as e:
            raise HTTPException(502, f"No se pudo contactar con {self.base_url}: {type(e).__name__}")
        if r.status_code in (401, 403):
            raise HTTPException(401, "API key inválida o sin permiso para este modelo")
        if r.status_code >= 400:
            codigo = r.status_code if r.status_code < 500 else 502
            raise HTTPException(codigo, f"El servidor de modelos respondió {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            raise HTTPException(502, "Respuesta no JSON del servidor de modelos")


_POSITIVOS = {"true", "1", "label_1", "yes", "refusal", "positive", "absolute_refusal"}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _prob_positiva(item):
    """Probabilidad de la clase positiva a partir de un resultado de /v1/classify.

    El formato exacto depende del servidor, así que se aceptan las variantes habituales:
    número, [p0, p1], {"label", "score"}, [{"label", "score"}, ...] o {"true": p, "false": q}.
    """
    if _num(item):
        return float(item)
    if isinstance(item, dict):
        if "label" in item and any(k in item for k in ("score", "probability", "prob")):
            p = float(next(item[k] for k in ("score", "probability", "prob") if k in item))
            return p if str(item["label"]).strip().lower() in _POSITIVOS else 1.0 - p
        for k in ("probs", "probabilities", "scores", "probability", "prob", "score"):
            if k in item:
                return _prob_positiva(item[k])
        for k, v in item.items():
            if str(k).strip().lower() in _POSITIVOS and _num(v):
                return float(v)
    if isinstance(item, list) and item:
        if all(_num(v) for v in item):
            return float(item[1] if len(item) > 1 else item[0])
        if all(isinstance(v, dict) for v in item):
            for d in item:
                if str(d.get("label", "")).strip().lower() in _POSITIVOS:
                    return _prob_positiva(d)
            if len(item) == 1:
                return _prob_positiva(item[0])
    raise ValueError(f"formato de respuesta de /v1/classify no reconocido: {json.dumps(item)[:200]}")


class ClasificadorRemoto:
    modo = "remoto"

    def __init__(self, cliente, modelo):
        self.cliente = cliente
        self.nombre = modelo
        self.modelo = modelo

    def predecir(self, textos, lote=32):
        probs = []
        for i in range(0, len(textos), lote):
            resp = self.cliente.post("/v1/classify", {"model": self.modelo, "input": textos[i:i + lote]})
            items = resp
            if isinstance(resp, dict):
                items = next((resp[k] for k in ("data", "results", "predictions", "outputs", "output")
                              if k in resp), None)
            if not isinstance(items, list) or len(items) != len(textos[i:i + lote]):
                raise HTTPException(502, "Respuesta inesperada de /v1/classify: "
                                         + json.dumps(resp)[:200])
            try:
                probs.extend(_prob_positiva(it) for it in items)
            except (ValueError, TypeError) as e:
                raise HTTPException(502, str(e))
        return probs


class ClasificadorSimulado:
    """Solo para probar la interfaz sin el modelo: usa la etiqueta de test.jsonl si la marca está."""
    modo = "simulado"
    nombre = "simulado (sin modelo)"

    def __init__(self, etiquetas):
        self.etiquetas = etiquetas

    def predecir(self, textos, lote=32):
        out = []
        for t in textos:
            nombre = t.split(":", 1)[0].strip().lower()
            h = int(hashlib.md5(t.encode()).hexdigest()[:6], 16) / 0xFFFFFF
            if nombre in self.etiquetas:
                out.append(0.9 + 0.09 * h if self.etiquetas[nombre] else 0.01 + 0.05 * h)
            else:
                out.append(0.02 + 0.1 * h)
        return out


class GeneradorRemoto:
    def __init__(self, cliente, modelo, max_new_tokens=320):
        self.cliente = cliente
        self.nombre = modelo
        self.modelo = modelo
        self.max_new_tokens = max_new_tokens

    def _peticion(self, usuario):
        def cuerpo(msgs):
            return {"model": self.modelo, "messages": msgs, "temperature": 0,
                    "max_tokens": self.max_new_tokens}
        try:
            resp = self.cliente.post("/v1/chat/completions", cuerpo(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": usuario}]))
        except HTTPException as e:
            if e.status_code != 400:
                raise
            # plantillas sin rol system: se funde en el mensaje de usuario
            resp = self.cliente.post("/v1/chat/completions", cuerpo(
                [{"role": "user", "content": SYSTEM_PROMPT + "\n\n" + usuario}]))
        try:
            return (resp["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise HTTPException(502, "Respuesta inesperada de /v1/chat/completions: " + json.dumps(resp)[:200])

    def generar(self, usuario, detener=None):
        """detener: threading.Event opcional; si se activa se descarta la respuesta y devuelve None."""
        res = {}

        def trabajo():
            try:
                res["ok"] = self._peticion(usuario)
            except Exception as e:  # noqa: BLE001 — se relanza en el hilo que espera
                res["error"] = e

        hilo = threading.Thread(target=trabajo, daemon=True)
        hilo.start()
        while hilo.is_alive():
            hilo.join(0.2)
            if detener and detener.is_set():
                return None
        if "error" in res:
            raise res["error"]
        return res["ok"]


# --------------------------------------------------------------------------
# Datos de test: candidatos y lote de ejemplo
# --------------------------------------------------------------------------

class DatosTest:
    def __init__(self, ruta):
        self.ruta = Path(ruta) if ruta else None
        self.pos, self.neg, self.etiquetas, self.por_id = [], [], {}, {}
        if not self.ruta:
            return
        t0 = time.time()
        with open(self.ruta, "rb") as f:
            while True:
                off = f.tell()
                linea = f.readline()
                if not linea:
                    break
                if not linea.strip():
                    continue
                r = json.loads(linea)
                ref = a_bool(r.get("absolute_refusal"))
                (self.pos if ref else self.neg).append(off)
                self.por_id[str(r.get("id", ""))] = off
                self.etiquetas[str(r.get("name", "")).strip().lower()] = ref
        print(f"[datos] {self.ruta.name}: {len(self.pos)} denegadas, {len(self.neg)} no denegadas "
              f"({time.time() - t0:.0f} s)")

    def leer(self, offsets):
        filas = []
        with open(self.ruta, "rb") as f:
            for off in offsets:
                f.seek(off)
                filas.append(json.loads(f.readline()))
        return filas

    def muestra(self, n, semilla, muestreo):
        if not self.ruta:
            return []
        rng = random.Random(semilla)
        if muestreo == "aleatorio":
            offs = rng.sample(self.pos + self.neg, n)
        else:  # estratificado: mitad denegadas, mitad no
            npos = min(len(self.pos), n // 2)
            offs = rng.sample(self.pos, npos) + rng.sample(self.neg, n - npos)
            rng.shuffle(offs)
        return self.leer(offs)

    def por_ids(self, ids):
        """Filas con esos id, en ese orden (los que no estén en test se ignoran)."""
        return self.leer([self.por_id[i] for i in map(str, ids) if i in self.por_id])

    def lote_ejemplo(self, n, n_pos, semilla):
        rng = random.Random(semilla)
        offs = rng.sample(self.pos, min(n_pos, len(self.pos))) + rng.sample(self.neg, n - n_pos)
        rng.shuffle(offs)
        return self.leer(offs)


# --------------------------------------------------------------------------
# Aplicación
# --------------------------------------------------------------------------

class ClaveApi(BaseModel):
    clave: str


class Peticion(BaseModel):
    name: str
    goods: str = ""
    candidato_id: str | None = None
    peticion_id: str | None = None  # para poder detener la explicación con /api/detener/{id}


def crear_app(args):
    datos = DatosTest(args.test)
    def a_candidato(r):
        gs = r.get("goods_and_services") or []
        return {"id": str(r.get("id", "")), "name": r.get("name", ""), "goods": gs_a_texto(gs),
                "goods_and_services": gs, "absolute_refusal": a_bool(r.get("absolute_refusal"))}

    # Los 10 de siempre (semilla fija) y un grupo de hasta 100 del que sale el botón «aleatorio»
    candidatos = [a_candidato(r) for r in datos.muestra(args.n_candidatos, args.semilla, args.muestreo)]
    grupo = {c["id"]: c for c in candidatos}
    for r in datos.muestra(max(0, args.grupo_candidatos - len(candidatos)), args.semilla + 1, args.muestreo):
        c = a_candidato(r)
        grupo.setdefault(c["id"], c)
    por_id = grupo
    # Lote de ejemplo fijo (ejemplos.json): ids de test.jsonl elegidos a mano; si no, se muestrea
    lote_fijo = []
    if args.ejemplos and datos.ruta:
        lote_fijo = json.loads(Path(args.ejemplos).read_text(encoding="utf-8")).get("lote", [])
        print(f"[datos] lote de ejemplo fijo: {len(lote_fijo)} marcas de {Path(args.ejemplos).name}")

    cliente = ClienteLiteLLM(args.base_url, os.environ.get("LITELLM_API_KEY"))
    remoto = ClasificadorRemoto(cliente, args.modelo_clasificador)
    simulado = ClasificadorSimulado(datos.etiquetas)
    generador = GeneradorRemoto(cliente, args.modelo_generador, args.max_new_tokens)
    if not cliente.clave:
        print("[aviso] Sin LITELLM_API_KEY: la interfaz pedirá la clave; mientras tanto el "
              "discriminativo está SIMULADO.")

    def clasificador_actual():
        return remoto if cliente.clave else simulado

    precalculadas = {}
    if args.predicciones:
        with open(args.predicciones, encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    r = json.loads(linea)
                    precalculadas[r["input"]] = r["output"]
        print(f"[datos] {len(precalculadas)} explicaciones precalculadas")

    def resolver_gs(p):
        c = por_id.get(p.candidato_id or "")
        if c and c["name"] == p.name and c["goods"].strip() == p.goods.strip():
            return c["goods_and_services"]  # sin pérdidas: mismo formato que el dataset
        return texto_a_gs(p.goods)

    app = FastAPI(title="Demostrador EUIPO")
    trabajos = {}

    @app.get("/api/estado")
    def estado():
        clf = clasificador_actual()
        if cliente.clave and not args.usar_precalculadas:
            gen_modo = "remoto"
        elif precalculadas:
            gen_modo = "precalculado" + (" + remoto" if cliente.clave else "")
        else:
            gen_modo = "no disponible"
        return {
            "clasificador": {"modo": clf.modo, "nombre": clf.nombre},
            "generador": {"modo": gen_modo, "nombre": generador.nombre if cliente.clave else
                          ("predictions.jsonl" if precalculadas else "")},
            "clave": {"origen": cliente.origen},
            "umbral": args.umbral, "semilla": args.semilla, "muestreo": args.muestreo,
            "lote_ejemplo": bool(datos.ruta),
        }

    @app.post("/api/clave")
    def guardar_clave(c: ClaveApi):
        if cliente.clave_env:
            raise HTTPException(409, "La API key está definida en el servidor (.env) y tiene prioridad")
        clave = c.clave.strip()
        if not clave:
            raise HTTPException(400, "Introduce la API key")
        # Comprueba la clave con una llamada mínima antes de guardarla (solo en memoria)
        cliente.post("/v1/classify", {"model": args.modelo_clasificador, "input": ["test"]},
                     timeout=60, clave=clave)
        cliente.clave_ui = clave
        return {"origen": cliente.origen}

    @app.get("/api/candidatos")
    def get_candidatos(aleatorio: bool = False):
        elegidos = candidatos
        if aleatorio:
            todos = list(grupo.values())
            den = [c for c in todos if c["absolute_refusal"]]
            acep = [c for c in todos if not c["absolute_refusal"]]
            n_den = min(len(den), args.n_candidatos // 2) if args.muestreo == "estratificado" else 0
            if n_den:
                elegidos = random.sample(den, n_den) + random.sample(acep, min(len(acep), args.n_candidatos - n_den))
                random.shuffle(elegidos)
            else:
                elegidos = random.sample(todos, min(len(todos), args.n_candidatos))
        return [{k: c[k] for k in ("id", "name", "goods", "absolute_refusal")} for c in elegidos]

    @app.post("/api/clasificar")
    def clasificar(p: Peticion):
        if not p.name.strip():
            raise HTTPException(400, "Falta el nombre de la marca")
        gs = resolver_gs(p)
        entrada = entrada_discriminativo(p.name.strip(), gs)
        clf = clasificador_actual()
        prob = clf.predecir([entrada])[0]
        return {"entrada": entrada, "probabilidad": prob, "denegacion": prob >= args.umbral,
                "umbral": args.umbral, "modo": clf.modo}

    detenciones = {}  # peticion_id -> threading.Event

    @app.post("/api/detener/{pid}")
    def detener(pid: str):
        detenciones.setdefault(pid, threading.Event()).set()
        return {"detenido": pid}

    @app.post("/api/explicar")
    def explicar(p: Peticion):
        if not p.name.strip():
            raise HTTPException(400, "Falta el nombre de la marca")
        prompt = prompt_generativo(p.name.strip(), resolver_gs(p))
        t0 = time.time()
        if prompt in precalculadas and (args.usar_precalculadas or not cliente.clave):
            crudo, fuente = precalculadas[prompt], "precalculada"
        elif cliente.clave:
            ev = detenciones.setdefault(p.peticion_id, threading.Event()) if p.peticion_id else None
            try:
                crudo, fuente = generador.generar(prompt, ev), "modelo"
            finally:
                detenciones.pop(p.peticion_id, None)
            if crudo is None:
                raise HTTPException(409, "Explicación detenida")
        else:
            raise HTTPException(401, "Falta la API key para el modelo generativo y esta marca "
                                     "no está en predictions.jsonl.")
        tipo, texto = parsear_salida(crudo)
        return {"tipo": tipo, "explicacion": texto, "crudo": crudo, "fuente": fuente,
                "prompt": prompt, "segundos": round(time.time() - t0, 2)}

    # ---------------- lotes ----------------

    def evaluar(tid, filas_iter, total):
        t = trabajos[tid]
        try:
            buf, filas = [], []

            def vaciar():
                probs = clasificador_actual().predecir([b[1] for b in buf], args.lote)
                for (fila, _), p in zip(buf, probs):
                    fila["prob"] = p
                    fila["pred"] = p >= args.umbral
                    filas.append(fila)
                t["hechas"] = len(filas)
                buf.clear()

            for r in filas_iter:
                if t.get("detener"):
                    break
                if "absolute_refusal" not in r or not r.get("name"):
                    t["descartadas"] += 1
                    continue
                gs = r.get("goods_and_services") or []
                if isinstance(gs, str):
                    gs = texto_a_gs(gs)
                fila = {"id": str(r.get("id") or ""), "name": str(r["name"]),
                        "real": a_bool(r["absolute_refusal"])}
                buf.append((fila, entrada_discriminativo(fila["name"], gs)))
                if len(buf) >= args.lote:
                    vaciar()
            if buf and not t.get("detener"):
                vaciar()
            tp = sum(f["real"] and f["pred"] for f in filas)
            fn = sum(f["real"] and not f["pred"] for f in filas)
            fp = sum(not f["real"] and f["pred"] for f in filas)
            tn = len(filas) - tp - fn - fp
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            fallos = sorted((f for f in filas if f["real"] != f["pred"]),
                            key=lambda f: abs(f["prob"] - args.umbral))
            t.update({
                "estado": "hecho", "filas": filas, "detenido": bool(t.get("detener")),
                "resultado": {
                    "total": len(filas), "aciertos": tp + tn, "fallos": fp + fn,
                    "tp": tp, "fn": fn, "fp": fp, "tn": tn,
                    "accuracy": (tp + tn) / len(filas) if filas else 0.0,
                    "precision": prec, "recall": rec,
                    "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
                    "fallos_lista": fallos[:500], "fallos_truncados": len(fallos) > 500,
                },
            })
        except Exception as e:  # noqa: BLE001 — se muestra en la interfaz
            t.update({"estado": "error", "error": f"{type(e).__name__}: {e}"})

    def lanzar(nombre, filas_iter, total, tid=None):
        tid = tid or uuid.uuid4().hex[:12]
        trabajos[tid] = {"estado": "en curso", "archivo": nombre, "total": total, "hechas": 0,
                         "descartadas": 0, "inicio": time.time()}
        threading.Thread(target=evaluar, args=(tid, filas_iter, total), daemon=True).start()
        return {"id": tid}

    @app.post("/api/lote")
    def subir_lote(archivo: UploadFile = File(...)):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        with tmp:
            while trozo := archivo.file.read(1 << 20):
                tmp.write(trozo)
        with open(tmp.name, "rb") as f:
            total = sum(1 for linea in f if linea.strip())

        tid = uuid.uuid4().hex[:12]

        def filas():
            try:
                with open(tmp.name, encoding="utf-8") as f:
                    for linea in f:
                        if linea.strip():
                            try:
                                yield json.loads(linea)
                            except json.JSONDecodeError:
                                trabajos[tid]["descartadas"] += 1
            finally:
                Path(tmp.name).unlink(missing_ok=True)

        return lanzar(archivo.filename or "lote.jsonl", filas(), total, tid)

    @app.post("/api/lote/ejemplo")
    def lote_ejemplo():
        if not datos.ruta:
            raise HTTPException(400, "Arranca con --test para usar el lote de ejemplo")
        if lote_fijo:
            filas = datos.por_ids(lote_fijo)
        else:
            filas = datos.lote_ejemplo(args.ejemplo_n, args.ejemplo_pos, args.semilla)
        return lanzar(f"ejemplo de {datos.ruta.name} ({len(filas)} marcas)", iter(filas), len(filas))

    @app.post("/api/lote/{tid}/detener")
    def detener_lote(tid: str):
        t = trabajos.get(tid)
        if not t:
            raise HTTPException(404, "Lote no encontrado")
        t["detener"] = True
        return {"detenido": tid}

    @app.get("/api/lote/{tid}")
    def ver_lote(tid: str):
        t = trabajos.get(tid)
        if not t:
            raise HTTPException(404, "Lote no encontrado")
        return {k: v for k, v in t.items() if k != "filas"}

    @app.get("/api/lote/{tid}/csv")
    def csv_lote(tid: str):
        t = trabajos.get(tid)
        if not t or t["estado"] != "hecho":
            raise HTTPException(404, "Lote no terminado")
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "name", "absolute_refusal", "prediccion", "probabilidad", "acierto"])
        for f in t["filas"]:
            w.writerow([f["id"], f["name"], f["real"], f["pred"], f"{f['prob']:.4f}", f["real"] == f["pred"]])
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=resultados_lote.csv"})

    app.mount("/", StaticFiles(directory=AQUI / "static", html=True), name="static")
    return app


def main():
    ap = argparse.ArgumentParser(description="Demostrador EUIPO (caso de uso 1)")
    ap.add_argument("--test", help="test.jsonl del dataset (candidatos y lote de ejemplo)")
    ap.add_argument("--predicciones", help="predictions.jsonl con salidas del generativo")
    ap.add_argument("--base-url", default=os.environ.get("LITELLM_BASE_URL", BASE_URL_DEF),
                    help="gateway LiteLLM (también LITELLM_BASE_URL)")
    ap.add_argument("--modelo-clasificador",
                    default=os.environ.get("CLASIFICADOR_MODEL", MODELO_CLASIFICADOR_DEF))
    ap.add_argument("--modelo-generador",
                    default=os.environ.get("GENERADOR_MODEL", MODELO_GENERADOR_DEF))
    ap.add_argument("--usar-precalculadas", action="store_true",
                    help="si la marca está en predictions.jsonl, usar esa salida aunque haya API key")
    ap.add_argument("--umbral", type=float, default=0.5)
    ap.add_argument("--max-new-tokens", type=int, default=320)
    ap.add_argument("--lote", type=int, default=32, help="tamaño de lote del discriminativo")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--n-candidatos", type=int, default=10)
    ap.add_argument("--grupo-candidatos", type=int, default=100,
                    help="marcas de test entre las que elige el botón aleatorio")
    ap.add_argument("--muestreo", choices=["estratificado", "aleatorio"], default="estratificado")
    ap.add_argument("--ejemplo-n", type=int, default=200)
    ap.add_argument("--ejemplo-pos", type=int, default=30)
    ap.add_argument("--ejemplos", help="ejemplos.json con los id del lote de ejemplo (por defecto app/ejemplos.json)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    # Rutas por defecto: datos en la carpeta de arriba (demoEUIPO)
    por_defecto = {
        "test": AQUI.parent / "test.jsonl",
        "predicciones": AQUI.parent / "predictions.jsonl",
        "ejemplos": AQUI / "ejemplos.json",
    }
    for opcion, ruta in por_defecto.items():
        if getattr(args, opcion) is None and ruta.is_file():
            setattr(args, opcion, str(ruta))
            print(f"[config] --{opcion} {ruta}")
    uvicorn.run(crear_app(args), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
