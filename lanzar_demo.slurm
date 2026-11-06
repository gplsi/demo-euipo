#!/bin/bash
#SBATCH --job-name=demo-euipo
#SBATCH --cpus-per-task=4
#SBATCH --mem=4G
#SBATCH --time=08:00:00
#SBATCH --output=demo_euipo_%j.log
#
# Lanza el demostrador EUIPO en un nodo con GPU de lamarr.
#   sbatch lanzar_demo.slurm
# Después mira el log (demo_euipo_<id>.log): imprime el túnel SSH que hay que abrir
# desde tu ordenador para ver la web en http://localhost:8000
#
# Requiere LITELLM_API_KEY en $CARPETA/.env (o exportada); si no, la web la pide.
# Ajusta estas rutas si cambian:
DATOS=${DATOS:-$HOME/demoEUIPO}
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
