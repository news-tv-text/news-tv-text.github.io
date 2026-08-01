# Запуск пайплайна на VPS

Раньше `data.json` обновлял GitHub Actions по расписанию `0 * * * *`. Теперь то же
самое делает VPS: раз в час запускает `pipeline/run.py`, коммитит изменившийся
`data.json` и пушит в `origin`. Репозиторий и git остаются как были — уезжает
только исполнение.

## Что здесь лежит

| Файл | Назначение |
| --- | --- |
| `update-news.sh` | Сам скрипт: venv → пайплайн → коммит → push. Умеет запускаться и из cron, и из systemd |
| `news-tv-update.service` | systemd-юнит (oneshot) |
| `news-tv-update.timer` | Таймер, ежечасно дёргающий юнит |
| `news-tv.env.example` | Шаблон файла с секретами вместо GitHub Secrets |

## Установка

Ниже — вариант с systemd-таймером (предпочтительнее cron: логи в journald,
`Persistent=true` догоняет пропущенные запуски после перезагрузки). Голый cron —
в конце.

### 1. Пользователь и клон

```bash
sudo useradd --system --create-home --home-dir /var/lib/newstv --shell /usr/sbin/nologin newstv
sudo install -d -o newstv -g newstv /opt/news-tv
sudo -u newstv git clone git@github.com:news-tv-text/news-tv-text.github.io.git /opt/news-tv
```

Нужны `python3-venv`, `git` и `flock` (пакет `util-linux`):

```bash
sudo apt install -y python3-venv git util-linux
```

### 2. Доступ на push

Пуш идёт под пользователем `newstv`, поэтому ему нужен свой ключ:

```bash
sudo -u newstv ssh-keygen -t ed25519 -N '' -f /var/lib/newstv/.ssh/id_ed25519
sudo -u newstv cat /var/lib/newstv/.ssh/id_ed25519.pub
```

Публичный ключ добавить в репозиторий как **deploy key с правом записи**
(Settings → Deploy keys → Allow write access). Проверить:

```bash
sudo -u newstv ssh -T git@github.com
```

Если клонировали по HTTPS — вместо ключа настройте credential helper с токеном,
имеющим права на запись в репозиторий.

### 3. Секреты

```bash
sudo install -d -m 750 -o newstv -g newstv /etc/news-tv
sudo install -m 600 -o newstv -g newstv \
  /opt/news-tv/deploy/news-tv.env.example /etc/news-tv/news-tv.env
sudo -e /etc/news-tv/news-tv.env   # подставить OPENMODEL_API_KEY
```

### 4. Первый запуск вручную

```bash
sudo -u newstv /opt/news-tv/deploy/update-news.sh
```

Скрипт сам создаст `.venv` и поставит зависимости. Дальше venv переиспользуется,
а `pip install` повторяется только при изменении `pipeline/requirements.txt`.

### 5. Таймер

```bash
sudo cp /opt/news-tv/deploy/news-tv-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now news-tv-update.timer
```

Проверка:

```bash
systemctl list-timers news-tv-update.timer   # когда следующий запуск
journalctl -u news-tv-update.service -n 50   # логи последнего запуска
sudo systemctl start news-tv-update.service  # запустить прямо сейчас
```

## Вариант через cron

Если systemd не хочется — то же самое одной строкой в crontab пользователя
`newstv` (`sudo -u newstv crontab -e`):

```cron
7 * * * * ENV_FILE=/etc/news-tv/news-tv.env /opt/news-tv/deploy/update-news.sh >> /var/log/news-tv.log 2>&1
```

Лог-файл должен быть доступен на запись пользователю `newstv`; ротацию настроить
через `logrotate`.

## Настройки скрипта

Переопределяются переменными окружения:

| Переменная | По умолчанию | Что делает |
| --- | --- | --- |
| `REPO_DIR` | каталог на уровень выше `deploy/` | Корень репозитория |
| `VENV_DIR` | `$REPO_DIR/.venv` | Где живёт venv |
| `ENV_FILE` | `/etc/news-tv/news-tv.env` | Файл с секретами |
| `BRANCH` | `main` | В какую ветку коммитить и пушить |
| `LOCK_FILE` | `/tmp/news-tv-update.lock` | Блокировка от параллельных запусков |
| `PUSH_RETRIES` | `4` | Попыток push с экспоненциальной паузой |

## Отличия от бывшего воркфлоу

- **`pipeline/news.db`** просто лежит на диске VPS и переживает запуски — трюк с
  `actions/cache` и `restore-keys` больше не нужен.
- **Параллельные запуски** отсекаются через `flock` вместо `concurrency`-группы.
- **`GDELT_SOCKS5_PROXY`** из воркфлоу не перенесён: `pipeline/run.py` эту
  переменную нигде не читает (и `PySocks` в `requirements.txt` тоже не
  используется).
- **`GDELT_CLOUD_API_KEY`** наоборот добавлен в шаблон env-файла: код его читает,
  а воркфлоу не передавал — то есть на Actions этот источник всегда молча
  пропускался.
