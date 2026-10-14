#!/usr/bin/env bash
# Prueba del prototype con Ollama en CPU, sin permisos de administrador (Ubuntu).
# Uso, desde cualquier carpeta:  bash /ruta/al/kit/run_test.sh 1     (o 2)
# Todo (Ollama, models y results) se queda dentro de la carpeta del kit (USB o home).
set -uo pipefail
OPTION="${1:-1}"
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$KIT/scripts/classroom/config.env"
RES="$KIT/results"; mkdir -p "$RES" "$KIT/models"
OUTPUT="$RES/$(date +%Y%m%d_%H%M)_$(hostname)_opcion$OPTION.txt"
export OLLAMA_MODELS="$KIT/models" OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-4096}" OLLAMA_KEEP_ALIVE=30m
step() { echo -e "\n=== $* ===" | tee -a "$OUTPUT"; }
fail() { echo -e "\nERROR: $*\nCopia este archivo en el hilo: $OUTPUT" | tee -a "$OUTPUT"; exit 1; }

step "1/6 Equipo"
{ grep -m1 "model name" /proc/cpuinfo; echo "Núcleos: $(nproc)"
  grep -qw avx2 /proc/cpuinfo && echo "AVX2: sí" || echo "AVX2: NO (equipo antiguo: irá lento)"
  free -h | head -2; df -h "$KIT" | tail -1; python3 --version; } 2>&1 | tee -a "$OUTPUT"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "Hace falta Python 3.10 o superior (arranca en Ubuntu)."

step "2/6 Ollama (descarga si no está)"
if [ ! -f "$KIT/ollama/bin/ollama" ]; then
  mkdir -p "$KIT/ollama"; cd "$KIT/ollama"
  for URL in "https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION/ollama-linux-amd64.tgz" \
             "https://ollama.com/download/ollama-linux-amd64.tgz"; do
    echo "Descargando $URL" | tee -a "$OUTPUT"
    curl -fL --retry 2 -o ollama.tgz "$URL" && tar -xzf ollama.tgz && break
    rm -f ollama.tgz
  done
  [ -f bin/ollama ] || fail "No se pudo descargar Ollama (¿la red o el proxy lo bloquean?)."
  rm -f ollama.tgz; rm -rf lib/ollama/cuda_* lib/ollama/rocm* lib/ollama/*vulkan* 2>/dev/null  # solo CPU: ahorra espacio
  cd - >/dev/null
fi
OLLAMA="$KIT/ollama/bin/ollama"
if ! "$OLLAMA" --version >/dev/null 2>&1; then   # p. ej. USB montado con noexec: se copia al home
  echo "No se puede ejecutar desde $KIT; copio Ollama a ~/.cache/kit_ollama" | tee -a "$OUTPUT"
  mkdir -p ~/.cache/kit_ollama && cp -r "$KIT/ollama/." ~/.cache/kit_ollama/ && chmod +x ~/.cache/kit_ollama/bin/ollama
  OLLAMA=~/.cache/kit_ollama/bin/ollama
  "$OLLAMA" --version >/dev/null 2>&1 || fail "Ollama no se puede ejecutar en este equipo (política del aula)."
fi
"$OLLAMA" --version 2>&1 | tee -a "$OUTPUT"

step "3/6 Servidor de Ollama"
if curl -s localhost:11434/api/version >/dev/null; then echo "Ya hay un servidor en marcha; lo reutilizo." | tee -a "$OUTPUT"
else
  "$OLLAMA" serve > "$RES/ollama_serve.log" 2>&1 & PID=$!; trap 'kill $PID 2>/dev/null' EXIT
  for i in $(seq 30); do curl -s localhost:11434/api/version >/dev/null && break; sleep 1; done
  curl -s localhost:11434/api/version >/dev/null || fail "El servidor no arrancó; mira $RES/ollama_serve.log"
fi

step "4/6 Modelos (descarga si faltan)"
elegir() {  # baja el primer modelo de la lista que exista
  for M in $1; do
    "$OLLAMA" list | awk '{print $1}' | grep -qx "$M" && { echo "$M"; return 0; }
    "$OLLAMA" pull "$M" >&2 && { echo "$M"; return 0; }
    echo "  $M no disponible, pruebo el siguiente" >&2
  done; return 1; }
M4B=$(elegir "$MODELS_4B") || fail "Ningún modelo de 4B disponible; edita MODELS_4B en scripts/classroom/config.env."
echo "Modelo 4B: $M4B" | tee -a "$OUTPUT"
if [ "$OPTION" = "2" ]; then
  LIBRE=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo)
  [ "$LIBRE" -ge "$MIN_RAM_OPTION2_GB" ] || [ "${FORCE:-0}" = "1" ] || \
    fail "Solo hay ${LIBRE} GB libres y la opción 2 necesita ~${MIN_RAM_OPTION2_GB}. Haz la opción 1 (o FORCE=1 bash run_test.sh 2)."
  MAG=$(elegir "$AGENT_MODELS") || fail "Ningún modelo de agente disponible; edita AGENT_MODELS."
  MEMB=$(elegir "$EMBEDDING_MODEL") || fail "No se pudo bajar $EMBEDDING_MODEL."
  echo "Agente: $MAG · Embeddings: $MEMB" | tee -a "$OUTPUT"
fi

step "5/6 Banco de pruebas (opción $OPTION). Puede tardar varios minutos por pregunta"
cd "$KIT"
export LLM_BACKEND=ollama LLM_TIMEOUT=900
if [ "$OPTION" = "2" ]; then
  export LLM_MODEL="$MAG" LLM_SMALL_MODEL="$M4B" EMBEDDING_MODEL="$MEMB" RAG_K=5
else
  export LLM_MODEL="$M4B" EMBEDDING_MODEL=none RAG_K=3
fi
# El primer modelo tarda en cargarse; evaluate.py muestra ese tiempo aparte ("carga máx.").
python3 evaluate.py --today 2026-10-01 2>&1 | tee -a "$OUTPUT"

step "6/6 Memoria usada por los models"
"$OLLAMA" ps 2>&1 | tee -a "$OUTPUT"; free -h | head -2 | tee -a "$OUTPUT"
echo -e "\nHecho. Sube al hilo este archivo: $OUTPUT"
