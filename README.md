# Nomos

Database of normative texts (Cyprus tax laws, EU acts, IFRS) with editions from
2024-01-01, parallel languages (Greek / English / Russian), cross-references and
notes on translation doubts. Pilot: VAT and VIES.

## Setup

```bash
uv sync
cp .env.example .env   # fill in the values
uv run python src/manage.py check
uv run python src/manage.py migrate
uv run python src/manage.py compilemessages   # interface translations (.mo, not in git)
```

Requires Python 3.14, MariaDB, the `libmariadb-dev` headers (for `mysqlclient`),
and gettext (for `compilemessages`).

## Публикация

Переменные `.env` (образец — `.env.example`):
- `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY` — длинная случайная строка;
- `DJANGO_ALLOWED_HOSTS` — домены через запятую; `DJANGO_CSRF_TRUSTED_ORIGINS` — те же
  адреса со схемой (`https://example.org`);
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`; `NOMOS_TEXTS_DIR`;
- `NOMOS_PUBLIC_ONLY=1` — без входа видны только публичные тексты;
- по необходимости: `DJANGO_HTTPS` (по умолчанию 1), `DJANGO_HSTS_SECONDS`,
  `DJANGO_PROXY_SSL_HEADER=1` (за прокси с X-Forwarded-Proto), `DJANGO_STATIC_ROOT`.

Сборка:
    npm ci && npm run build
    uv sync
    uv run python src/manage.py migrate
    uv run python src/manage.py compilemessages
    uv run python src/manage.py collectstatic --noinput
    uv run python src/manage.py check --deploy

`compilemessages` нужен GNU gettext (`msgfmt`) на машине сборки.
Каталог `STATIC_ROOT` (по умолчанию `staticfiles/`) отдаёт веб-сервер по адресу `/static/`.
Учётную запись для входа создаёт владелец: `uv run python src/manage.py createsuperuser`.

## Licences

- Code: MIT ([LICENSE](LICENSE)).
- Our translations and markup: CC BY 4.0 ([LICENSE-CONTENT](LICENSE-CONTENT)).
- Official source texts have their own legal regime. This repository contains
  code only; texts are kept in a separate private repository.
