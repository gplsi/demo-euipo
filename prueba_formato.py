"""Prueba de robustez del discriminativo frente al formato de los bienes y servicios.

Toma marcas de test.jsonl (denegadas y aceptadas) y las clasifica tres veces:
  orig: tal cual están en el dataset
  sin:  quitando el punto final de los términos de cada clase
  con:  añadiendo un punto final a los términos de cada clase
Un modelo que se fija en el contenido debería dar casi lo mismo en las tres.

Uso:
  python prueba_formato.py --clasificador modelos/discriminativo --test ../test.jsonl --n 150
"""
import argparse
import json
import random

from app import Clasificador, entrada_discriminativo, a_bool


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
    ap.add_argument("--clasificador", default="modelos/discriminativo")
    ap.add_argument("--test", default="../test.jsonl")
    ap.add_argument("--n", type=int, default=150, help="marcas por clase")
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--dispositivo", default="auto")
    args = ap.parse_args()

    den, acep = [], []
    with open(args.test, encoding="utf-8") as f:
        for linea in f:
            if linea.strip():
                r = json.loads(linea)
                (den if a_bool(r["absolute_refusal"]) else acep).append(r)
    rng = random.Random(args.semilla)
    grupos = {"denegadas": rng.sample(den, args.n), "aceptadas": rng.sample(acep, args.n)}

    c = Clasificador(args.clasificador, dispositivo=args.dispositivo)
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
