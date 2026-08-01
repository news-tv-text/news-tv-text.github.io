#!/usr/bin/env bash
#
# Ежечасное обновление data.json на VPS — замена GitHub Actions воркфлоу.
#
# Что делает: подтягивает свежую ветку, запускает pipeline/run.py в venv,
# коммитит изменившийся data.json и пушит в origin.
#
# Настройки переопределяются переменными окружения (см. deploy/README.md).

set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
ENV_FILE="${ENV_FILE:-/etc/news-tv/news-tv.env}"
BRANCH="${BRANCH:-main}"
LOCK_FILE="${LOCK_FILE:-/tmp/news-tv-update.lock}"
PUSH_RETRIES="${PUSH_RETRIES:-4}"

log() {
    echo "[$(date -u +'%Y-%m-%d %H:%M:%S UTC')] $*"
}

# Аналог concurrency-группы в Actions: если предыдущий запуск ещё идёт
# (например, LLM отвечает дольше часа), новый молча выходит.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "Предыдущий запуск ещё выполняется — выходим"
    exit 0
fi

# Секреты (OPENMODEL_API_KEY и т.д.) — вместо GitHub Secrets.
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
else
    log "ВНИМАНИЕ: файл с переменными $ENV_FILE не найден"
fi

cd "$REPO_DIR"

# ---------- venv ----------
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "Создаём venv в $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
fi

# Зависимости переустанавливаем только когда requirements.txt реально изменился,
# а не каждый час.
REQ_FILE="$REPO_DIR/pipeline/requirements.txt"
REQ_STAMP="$VENV_DIR/.requirements.sha256"
REQ_HASH="$(sha256sum "$REQ_FILE" | cut -d' ' -f1)"
if [[ ! -f "$REQ_STAMP" || "$(cat "$REQ_STAMP")" != "$REQ_HASH" ]]; then
    log "Устанавливаем зависимости"
    "$VENV_DIR/bin/pip" install --quiet -r "$REQ_FILE"
    echo "$REQ_HASH" > "$REQ_STAMP"
fi

# ---------- git: подтягиваем чужие изменения ----------
git fetch --quiet origin "$BRANCH"
git checkout --quiet "$BRANCH"
# Только fast-forward: если локальная ветка разошлась с origin, лучше упасть
# с ошибкой, чем разбираться с merge-конфликтом data.json вслепую.
git merge --quiet --ff-only "origin/$BRANCH"

# ---------- пайплайн ----------
log "Запускаем pipeline/run.py"
"$VENV_DIR/bin/python" "$REPO_DIR/pipeline/run.py"

# ---------- git: коммитим и пушим ----------
git config user.name "news-tv-bot"
git config user.email "news-tv-bot@localhost"
git add data.json

if git diff --cached --quiet; then
    log "Нет изменений в data.json — пропускаем коммит"
    exit 0
fi

git commit --quiet -m "chore: update news data ($(date -u +'%Y-%m-%d %H:%M UTC'))"

delay=2
for attempt in $(seq 1 "$PUSH_RETRIES"); do
    if git push --quiet origin "$BRANCH"; then
        log "data.json обновлён и запушен"
        exit 0
    fi
    if [[ "$attempt" -lt "$PUSH_RETRIES" ]]; then
        log "Push не удался (попытка $attempt), повтор через ${delay}s"
        sleep "$delay"
        delay=$(( delay * 2 ))
    fi
done

log "ОШИБКА: не удалось запушить после $PUSH_RETRIES попыток"
exit 1
