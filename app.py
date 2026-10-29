"""Demostrador EUIPO — Caso de uso 1 (denegaciones absolutas, art. 7 EUTMR).

Backend FastAPI que sirve el frontend de static/ y expone:
  GET  /api/estado               modelos cargados y modo de cada uno
  GET  /api/candidatos           10 marcas de la partición de test (semilla fija)
  POST /api/clasificar           modelo discriminativo: ¿denegación absoluta?
  POST /api/explicar             modelo generativo: tipo y explicación
  POST /api/lote                 sube un .jsonl y lo evalúa contra absolute_refusal
  POST /api/lote/ejemplo         lote de ejemplo muestreado de test.jsonl
  GET  /api/lote/{id}            progreso y resultados
  GET  /api/lote/{id}/csv        predicciones de todo el lote

Uso (ver README.md):
  python app.py --test test.jsonl --predicciones predictions.jsonl \
      --clasificador /ruta/checkpoint-17212 --generador /ruta/EUIPO_Aitana_2B_...
"""
import argparse
import csv
import hashlib
import io
import json
import random
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

AQUI = Path(__file__).resolve().parent
# Nombres a mostrar cuando los modelos están copiados en app/modelos/
NOMBRES_LOCALES = {
    "discriminativo": "euipo_refusals_v2 (sin atajo del punto)",
    "generativo": "EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1",
}

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


def normalizar_gs(gs):
    """Quita el punto final de los términos de cada clase.

    El discriminativo actual usa ese punto como atajo (las denegadas del dataset suelen
    terminar en «.» y las aceptadas no), así que sin normalizar una marca escrita a mano
    con punto final sale casi siempre como denegación. Ver prueba_formato.py.
    """
    return [{**g, "terms": str(g.get("terms", "")).strip().rstrip(".").rstrip()} for g in gs]


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

def _dispositivo(pedido):
    import torch
    if pedido and pedido != "auto":
        return pedido
    return "cuda" if torch.cuda.is_available() else "cpu"


class Clasificador:
    modo = "modelo"

    def __init__(self, ruta, tokenizador=None, dispositivo="auto", max_len=512):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        self.dev = _dispositivo(dispositivo)
        self.tok = AutoTokenizer.from_pretrained(tokenizador or ruta)
        self.model = AutoModelForSequenceClassification.from_pretrained(ruta).to(self.dev).eval()
        self.max_len = max_len
        self.nombre = NOMBRES_LOCALES.get(Path(ruta).name, Path(ruta).parent.name + "/" + Path(ruta).name)
        id2label = {int(k): str(v).lower() for k, v in self.model.config.id2label.items()}
        positivos = {"true", "1", "label_1", "yes", "refusal", "positive"}
        self.idx = next((i for i, l in id2label.items() if l in positivos), 1)
        self.n_labels = self.model.config.num_labels

    def predecir(self, textos, lote=32):
        probs = []
        with self.torch.inference_mode():
            for i in range(0, len(textos), lote):
                enc = self.tok(textos[i:i + lote], truncation=True, max_length=self.max_len,
                               padding=True, return_tensors="pt").to(self.dev)
                logits = self.model(**enc).logits.float()
                if self.n_labels == 1:
                    p = self.torch.sigmoid(logits[:, 0])
                else:
                    p = self.torch.softmax(logits, dim=-1)[:, self.idx]
                probs.extend(p.cpu().tolist())
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


class Generador:
    def __init__(self, ruta, dispositivo="auto", max_new_tokens=320, precision="auto"):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.dev = _dispositivo(dispositivo)
        self.tok = AutoTokenizer.from_pretrained(ruta)
        if precision == "auto":  # bf16 en GPU; en CPU float32 (más RAM, ~9 GB, pero más compatible)
            precision = "bfloat16" if self.dev.startswith("cuda") else "float32"
        dtype = getattr(torch, precision)
        try:
            self.model = AutoModelForCausalLM.from_pretrained(ruta, dtype=dtype)
        except TypeError:  # transformers antiguos
            self.model = AutoModelForCausalLM.from_pretrained(ruta, torch_dtype=dtype)
        self.model.to(self.dev).eval()
        self.max_new_tokens = max_new_tokens
        self.nombre = NOMBRES_LOCALES.get(Path(ruta).name, Path(ruta).name)
        if self.nombre == "final":
            self.nombre = Path(ruta).parent.name
        self.lock = threading.Lock()

    def _plantilla(self, usuario):
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": usuario}]
        try:
            return self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        except Exception:  # plantillas sin rol system
            msgs = [{"role": "user", "content": SYSTEM_PROMPT + "\n\n" + usuario}]
            return self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)

    def generar(self, usuario):
        texto = self._plantilla(usuario)
        enc = self.tok(texto, return_tensors="pt", add_special_tokens=False).to(self.dev)
        pad = self.tok.pad_token_id if self.tok.pad_token_id is not None else self.tok.eos_token_id
        with self.lock, self.torch.inference_mode():
            out = self.model.generate(**enc, max_new_tokens=self.max_new_tokens,
                                      do_sample=False, pad_token_id=pad)
        return self.tok.decode(out[0, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()


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

class Peticion(BaseModel):
    name: str
    goods: str = ""
    candidato_id: str | None = None


def crear_app(args):
    datos = DatosTest(args.test)
    candidatos = []
    for r in datos.muestra(args.n_candidatos, args.semilla, args.muestreo):
        gs = r.get("goods_and_services") or []
        candidatos.append({
            "id": str(r.get("id", "")), "name": r.get("name", ""), "goods": gs_a_texto(gs),
            "goods_and_services": gs, "absolute_refusal": a_bool(r.get("absolute_refusal")),
        })
    por_id = {c["id"]: c for c in candidatos}
    # Lote de ejemplo fijo (ejemplos.json): ids de test.jsonl elegidos a mano; si no, se muestrea
    lote_fijo = []
    if args.ejemplos and datos.ruta:
        lote_fijo = json.loads(Path(args.ejemplos).read_text(encoding="utf-8")).get("lote", [])
        print(f"[datos] lote de ejemplo fijo: {len(lote_fijo)} marcas de {Path(args.ejemplos).name}")

    if args.clasificador:
        clasificador = Clasificador(args.clasificador, args.tokenizador, args.dispositivo, args.max_len)
    else:
        print("[aviso] Sin --clasificador: el discriminativo está SIMULADO (solo para probar la interfaz).")
        clasificador = ClasificadorSimulado(datos.etiquetas)
    generador = Generador(args.generador, args.dispositivo, args.max_new_tokens, args.precision) if args.generador else None

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
        if generador and not args.usar_precalculadas:
            gen_modo = "modelo"
        elif precalculadas:
            gen_modo = "precalculado" + (" + modelo" if generador else "")
        else:
            gen_modo = "modelo" if generador else "no disponible"
        return {
            "clasificador": {"modo": clasificador.modo, "nombre": clasificador.nombre},
            "generador": {"modo": gen_modo, "nombre": generador.nombre if generador else
                          ("predictions.jsonl" if precalculadas else "")},
            "umbral": args.umbral, "semilla": args.semilla, "muestreo": args.muestreo,
            "lote_ejemplo": bool(datos.ruta),
        }

    @app.get("/api/candidatos")
    def get_candidatos():
        return [{k: c[k] for k in ("id", "name", "goods", "absolute_refusal")} for c in candidatos]

    @app.post("/api/clasificar")
    def clasificar(p: Peticion):
        if not p.name.strip():
            raise HTTPException(400, "Falta el nombre de la marca")
        gs = resolver_gs(p)
        tal_cual = entrada_discriminativo(p.name.strip(), gs)
        normalizada = entrada_discriminativo(p.name.strip(), normalizar_gs(gs))
        p_tal_cual, p_norm = clasificador.predecir([tal_cual, normalizada])
        # La decisión se toma siempre sobre la entrada normalizada; la otra se muestra como referencia
        return {"entrada": normalizada, "entrada_sin_normalizar": tal_cual,
                "probabilidad": p_norm, "probabilidad_sin_normalizar": p_tal_cual,
                "denegacion": p_norm >= args.umbral, "umbral": args.umbral, "modo": clasificador.modo}

    @app.post("/api/explicar")
    def explicar(p: Peticion):
        if not p.name.strip():
            raise HTTPException(400, "Falta el nombre de la marca")
        prompt = prompt_generativo(p.name.strip(), resolver_gs(p))
        t0 = time.time()
        if prompt in precalculadas and (args.usar_precalculadas or not generador):
            crudo, fuente = precalculadas[prompt], "precalculada"
        elif generador:
            crudo, fuente = generador.generar(prompt), "modelo"
        else:
            raise HTTPException(503, "No hay modelo generativo cargado y esta marca no está en "
                                     "predictions.jsonl. Arranca con --generador.")
        tipo, texto = parsear_salida(crudo)
        return {"tipo": tipo, "explicacion": texto, "crudo": crudo, "fuente": fuente,
                "prompt": prompt, "segundos": round(time.time() - t0, 2)}

    # ---------------- lotes ----------------

    def evaluar(tid, filas_iter, total):
        t = trabajos[tid]
        try:
            buf, filas = [], []

            def vaciar():
                # cada marca va dos veces: normalizada (decide) y tal cual (referencia)
                probs = clasificador.predecir([x for b in buf for x in b[1]], args.lote)
                for i, (fila, _) in enumerate(buf):
                    p = probs[2 * i]
                    fila["prob"] = p
                    fila["prob_sin_normalizar"] = probs[2 * i + 1]
                    fila["pred"] = p >= args.umbral
                    filas.append(fila)
                t["hechas"] = len(filas)
                buf.clear()

            for r in filas_iter:
                if "absolute_refusal" not in r or not r.get("name"):
                    t["descartadas"] += 1
                    continue
                gs = r.get("goods_and_services") or []
                if isinstance(gs, str):
                    gs = texto_a_gs(gs)
                fila = {"id": str(r.get("id") or ""), "name": str(r["name"]),
                        "real": a_bool(r["absolute_refusal"])}
                buf.append((fila, (entrada_discriminativo(fila["name"], normalizar_gs(gs)),
                                   entrada_discriminativo(fila["name"], gs))))
                if len(buf) >= args.lote:
                    vaciar()
            if buf:
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
                "estado": "hecho", "filas": filas,
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
        w.writerow(["id", "name", "absolute_refusal", "prediccion", "probabilidad_normalizada",
                    "probabilidad_sin_normalizar", "acierto"])
        for f in t["filas"]:
            w.writerow([f["id"], f["name"], f["real"], f["pred"], f"{f['prob']:.4f}",
                        f"{f['prob_sin_normalizar']:.4f}", f["real"] == f["pred"]])
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=resultados_lote.csv"})

    app.mount("/", StaticFiles(directory=AQUI / "static", html=True), name="static")
    return app


def main():
    ap = argparse.ArgumentParser(description="Demostrador EUIPO (caso de uso 1)")
    ap.add_argument("--test", help="test.jsonl del dataset (candidatos y lote de ejemplo)")
    ap.add_argument("--predicciones", help="predictions.jsonl con salidas del generativo")
    ap.add_argument("--clasificador", help="checkpoint del modelo discriminativo")
    ap.add_argument("--tokenizador", help="tokenizador si el checkpoint no lo incluye")
    ap.add_argument("--generador", help="carpeta del modelo generativo instruido")
    ap.add_argument("--sin-generador", action="store_true",
                    help="no cargar el generativo (solo explicaciones de predictions.jsonl)")
    ap.add_argument("--usar-precalculadas", action="store_true",
                    help="si la marca está en predictions.jsonl, usar esa salida aunque haya modelo")
    ap.add_argument("--dispositivo", default="auto", help="auto, cpu, cuda, cuda:0...")
    ap.add_argument("--precision", default="auto", choices=["auto", "bfloat16", "float16", "float32"],
                    help="tipo de los pesos del generativo (bfloat16 en CPU usa la mitad de RAM)")
    ap.add_argument("--umbral", type=float, default=0.5)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--max-new-tokens", type=int, default=320)
    ap.add_argument("--lote", type=int, default=32, help="tamaño de lote del discriminativo")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--n-candidatos", type=int, default=10)
    ap.add_argument("--muestreo", choices=["estratificado", "aleatorio"], default="estratificado")
    ap.add_argument("--ejemplo-n", type=int, default=200)
    ap.add_argument("--ejemplo-pos", type=int, default=30)
    ap.add_argument("--ejemplos", help="ejemplos.json con los id del lote de ejemplo (por defecto app/ejemplos.json)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    # Rutas por defecto: modelos en app/modelos/ y datos en la carpeta de arriba (demoEUIPO)
    por_defecto = {
        "clasificador": AQUI / "modelos" / "discriminativo" / "config.json",
        "generador": AQUI / "modelos" / "generativo" / "config.json",
        "test": AQUI.parent / "test.jsonl",
        "predicciones": AQUI.parent / "predictions.jsonl",
        "ejemplos": AQUI / "ejemplos.json",
    }
    for opcion, ruta in por_defecto.items():
        if getattr(args, opcion) is None and ruta.is_file():
            valor = ruta.parent if ruta.name == "config.json" else ruta
            setattr(args, opcion, str(valor))
            print(f"[config] --{opcion} {valor}")
    if args.sin_generador:
        args.generador = None
    uvicorn.run(crear_app(args), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
