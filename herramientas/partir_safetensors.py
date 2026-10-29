"""Parte un model.safetensors en trozos de <= 1,5 GB (formato estándar de Hugging Face).

GitHub no acepta archivos LFS de más de 2 GB. transformers carga los trozos igual,
gracias a model.safetensors.index.json.

Uso (desde la raíz del repositorio):
  python herramientas/partir_safetensors.py modelos/generativo/model.safetensors modelos/generativo
  (después borra o mueve fuera modelos/generativo/model.safetensors)
"""
import json, os, sys
from safetensors import safe_open
from safetensors.torch import save_file
src, out, lim = sys.argv[1], sys.argv[2], int(1.5e9)
f = safe_open(src, framework="pt"); meta = f.metadata() or {"format": "pt"}
grupos, actual, tam = [], [], 0
for k in f.keys():
    t = f.get_tensor(k); n = t.numel() * t.element_size()
    if actual and tam + n > lim: grupos.append(actual); actual, tam = [], 0
    actual.append(k); tam += n
grupos.append(actual)
N = len(grupos); mapa = {}; total = 0
for i, ks in enumerate(grupos, 1):
    nombre = f"model-{i:05d}-of-{N:05d}.safetensors"
    ts = {k: f.get_tensor(k).contiguous() for k in ks}
    total += sum(t.numel() * t.element_size() for t in ts.values())
    save_file(ts, os.path.join(out, nombre), metadata=meta)
    mapa.update({k: nombre for k in ks}); print(nombre, len(ks), flush=True)
json.dump({"metadata": {"total_size": total}, "weight_map": mapa}, open(os.path.join(out, "model.safetensors.index.json"), "w"), indent=2)
