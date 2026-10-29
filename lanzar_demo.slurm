#!/bin/bash
#SBATCH --job-name=demo-euipo
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=demo_euipo_%j.log
#
# Lanza el demostrador EUIPO en un nodo con GPU de lamarr.
#   sbatch lanzar_demo.slurm
# Después mira el log (demo_euipo_<id>.log): imprime el túnel SSH que hay que abrir
# desde tu ordenador para ver la web en http://localhost:8000
#
# Ajusta estas rutas si cambian:
DATOS=${DATOS:-$HOME/demoEUIPO}
CLASIFICADOR=${CLASIFICADOR:-/home/gplsi/GPLSI/codigos/transformer_model_classifier/models/comment_euipo_refusals_v1/checkpoint-17212}
GENERADOR=${GENERADOR:-/home/gplsi/GPLSI/ALIA/modelos/Instruction/EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1/final}
PUERTO=${PUERTO:-8000}
CARPETA=$(cd "$(dirname "$0")" && pwd)
[ -n "$SLURM_SUBMIT_DIR" ] && CARPETA=$SLURM_SUBMIT_DIR

[ -f "$CARPETA/.venv/bin/activate" ] && source "$CARPETA/.venv/bin/activate"

echo "Túnel (ejecútalo en tu ordenador):  ssh -N -L ${PUERTO}:$(hostname):${PUERTO} $USER@lamarr"
echo "Luego abre http://localhost:${PUERTO}"

python "$CARPETA/app.py" \
  --test "$DATOS/test.jsonl" \
  --predicciones "$DATOS/predictions.jsonl" \
  --clasificador "$CLASIFICADOR" \
  --generador "$GENERADOR" \
  --host 0.0.0.0 --port "$PUERTO" "$@"
