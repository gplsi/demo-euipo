"""Prueba de robustez del discriminativo frente al formato de los bienes y servicios.

Toma marcas de test.jsonl (denegadas y aceptadas) y las clasifica tres veces:
  orig: tal cual están en el dataset
  sin:  quitando el punto final de los términos de cada clase
  con:  añadiendo un punto final a los términos de cada clase
Un modelo que se fija en el contenido debería dar casi lo mismo en las tres.

Uso:
  LITELLM_API_KEY=... python prueba_formato.py --test ../test.jsonl --n 150
"""
import argparse
import json
import os
import random

from app import (BASE_URL_DEF, MODELO_CLASIFICADOR_DEF, ClasificadorRemoto, ClienteLiteLLM,
                 a_bool, entrada_discriminativo)


def variante(gs, modo):
    out = []
    for g in gs:
        t = str(g.get("terms", "")).strip()
        if modo == "sin":
            t = t.rstrip(".").rstrip()
        elif modo == "con" and t and not t.endswith("."):
            t += "."
        out.append({"classNumber": g.get("classNumber", ""), "terms": t})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("LITELLM_BASE_URL", BASE_URL_DEF))
    ap.add_argument("--modelo", default=os.environ.get("CLASIFICADOR_MODEL", MODELO_CLASIFICADOR_DEF))
    ap.add_argument("--test", default="../test.jsonl")
    ap.add_argument("--n", type=int, default=150, help="marcas por clase")
    ap.add_argument("--semilla", type=int, default=1)
    args = ap.parse_args()

    den, acep = [], []
    with open(args.test, encoding="utf-8") as f:
        for linea in f:
            if linea.strip():
                r = json.loads(linea)
                (den if a_bool(r["absolute_refusal"]) else acep).append(r)
    rng = random.Random(args.semilla)
    grupos = {"denegadas": rng.sample(den, args.n), "aceptadas": rng.sample(acep, args.n)}

    c = ClasificadorRemoto(ClienteLiteLLM(args.base_url, os.environ.get("LITELLM_API_KEY")), args.modelo)
    print(f"{'grupo':<10} {'formato':<6} % predicho denegación")
    for nombre, filas in grupos.items():
        for modo in ("orig", "sin", "con"):
            textos = [entrada_discriminativo(r["name"], variante(r.get("goods_and_services") or [], modo))
                      for r in filas]
            probs = c.predecir(textos, lote=16)
            pct = 100 * sum(p >= 0.5 for p in probs) / len(probs)
            print(f"{nombre:<10} {modo:<6} {pct:5.1f}")


if __name__ == "__main__":
    main()
