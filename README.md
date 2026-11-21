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

## Licences

- Code: MIT ([LICENSE](LICENSE)).
- Our translations and markup: CC BY 4.0 ([LICENSE-CONTENT](LICENSE-CONTENT)).
- Official source texts have their own legal regime. This repository contains
  code only; texts are kept in a separate private repository.
