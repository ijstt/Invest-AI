#!/usr/bin/env bash
# Скрипт для скачивания моделей Qwen2.5 для локального LLM.
# 
# Использование:
#   ./scripts/download_models.sh           # скачать все модели
#   ./scripts/download_models.sh --7b-only  # только 7B модель
#   ./scripts/download_models.sh --3b-only  # только 3B модель
#
# Модели скачиваются в директорию models/ и импортируются в Ollama.
# Требует установленный docker compose с контейнером geo-ollama.

set -euo pipefail
cd "$(dirname "$0")/.."

MODELS_DIR="models"
mkdir -p "$MODELS_DIR"

# Qwen2.5-7B-Instruct Q4_K_M (~4.7 GB) - основная модель для синтеза отчётов
MODEL_7B_URL="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf"
MODEL_7B_FILE="$MODELS_DIR/qwen2.5-7b-instruct-q4_k_m.gguf"

# Qwen2.5-3B-Instruct Q4_K_M (~2.1 GB) - лёгкая модель для ask-роутера
MODEL_3B_URL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
MODEL_3B_FILE="$MODELS_DIR/qwen2.5-3b-instruct-q4_k_m.gguf"

DOWNLOAD_7B=1
DOWNLOAD_3B=1

for arg in "$@"; do
  case "$arg" in
    --7b-only) DOWNLOAD_3B=0 ;;
    --3b-only) DOWNLOAD_7B=0 ;;
    *) echo "Неизвестный флаг: $arg" >&2; exit 2 ;;
  esac
done

download_model() {
  local url="$1"
  local file="$2"
  local name="$3"
  
  if [[ -f "$file" ]]; then
    echo "✓ $name уже скачана: $file"
    return 0
  fi
  
  echo "▶ Скачиваю $name (~$(du -h "$file" 2>/dev/null | cut -f1 || echo "4.7GB"))..."
  echo "  URL: $url"
  
  if command -v wget &>/dev/null; then
    wget -c "$url" -O "$file"
  elif command -v curl &>/dev/null; then
    curl -L -C - "$url" -o "$file"
  else
    echo "✗ Не найден wget или curl" >&2
    return 1
  fi
  
  echo "✓ $name скачана: $file"
}

if [[ "$DOWNLOAD_7B" == 1 ]]; then
  download_model "$MODEL_7B_URL" "$MODEL_7B_FILE" "Qwen2.5-7B-Instruct"
fi

if [[ "$DOWNLOAD_3B" == 1 ]]; then
  download_model "$MODEL_3B_URL" "$MODEL_3B_FILE" "Qwen2.5-3B-Instruct"
fi

# Создание Modelfile для Ollama
create_modelfile() {
  local gguf="$1"
  local modelfile="$2"
  local ctx="$3"
  
  cat > "$modelfile" << EOF
# Импорт скачанного Qwen2.5 в Ollama.
# Использование:
#   ollama create $(basename "$modelfile" .modelfile) -f "$modelfile"
FROM ./$(basename "$gguf")

PARAMETER num_ctx $ctx
PARAMETER temperature 0.3
EOF
}

echo "▶ Создаю Modelfile для Ollama..."
create_modelfile "$MODEL_7B_FILE" "$MODELS_DIR/Modelfile" 8192
create_modelfile "$MODEL_3B_FILE" "$MODELS_DIR/Modelfile-3b" 4096

# Импорт в Ollama (если контейнер запущен)
if docker compose ps geo-ollama &>/dev/null | grep -q "Up"; then
  echo "▶ Импортирую модели в Ollama..."
  
  import_model() {
    local tag="$1"
    local modelfile="$2"
    
    if ! docker exec geo-ollama ollama list 2>/dev/null | grep -q "$tag"; then
      echo "  Создаю модель $tag..."
      docker exec geo-ollama ollama create "$tag" -f "/import/$(basename "$modelfile")" \
        || echo "  ⚠ Не удалось создать $tag"
    else
      echo "  Модель $tag уже существует в Ollama"
    fi
  }
  
  import_model "qwen2.5:7b-instruct" "$MODELS_DIR/Modelfile"
  import_model "qwen2.5:3b-instruct" "$MODELS_DIR/Modelfile-3b"
else
  echo "ℹ Контейнер geo-ollama не запущен. Модели скачаны, но не импортированы."
  echo "  Для импорта запустите: ./up.sh"
fi

echo "✓ Готово."
