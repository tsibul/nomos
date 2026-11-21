# Инструкция по заполнению базы (черновик)

Черновик 2026-11-21. Описывает, как работает сейчас: код `src/nomos/sources/` (сборщик
`collect_cylaw`, загрузчик `load_texts`) и содержимое `Nomos-texts`. Чего ещё нет — помечено
«нет»; невыясненное — **[?]** и в разделе 8. Утверждённая модель — `docs/design/data-model.md`;
при расхождении прав проект, а инструкция правится.

## 1. Где лежат тексты

- Репозиторий `../Nomos-texts` (закрытый; копия — приватный GitHub `nomos-texts`). Путь —
  `NOMOS_TEXTS_DIR` в `.env` основного репозитория; `.env` читает `nomos/settings.py`.
  Относительный путь отсчитывается от текущей папки процесса — команды запускать из корня
  `Nomos` **[?]** (так работает `../Nomos-texts`; не проверялось из других папок).
- Ветки `Nomos-texts`: `develop`, `main`, ветки сессий — как в коде (раздел 7).

## 2. Папки и файлы

```
Nomos-texts/
  cy/law/2000/95_I/           акт = папка по uid
    act.xml                   описание акта, фрагменты, изменения, редакции
    collect.xml               манифест сборщика (для собираемых актов)
    el.xml                    тексты на языке оригинала
    ru.xml                    наш перевод
    cylaw-notes.xml           примечания источника (служебный файл сборщика)
  cy/law/2024/…, cy/kdp/…     изменяющие акты — каждый в своей папке, тем же устройством
  glossary/el-ru.xml          глоссарий перевода (en-ru — нет)
  sources/…                   исходники (PDF и т. п.); загрузчик эту ветку пропускает
```

- Папка акта = его uid (`cy/law/2000/95_I`, для ЕС — `eu/dir/2006/112`).
- Загрузчик находит акты по всем `act.xml` в дереве, кроме путей, где есть `sources`.
- Языковые файлы называются как угодно; имя задаёт `file` в `<expression>` (обычно `el.xml`).

## 3. `act.xml`

Корень `<act>`, атрибуты, которые читает загрузчик:

| Атрибут | Обязателен | Что |
|---|---|---|
| `uid` | да | адрес акта |
| `jurisdiction`, `kind`, `number`, `year` | да | `kind` — из `Act.Kind`: law, kdp, dir, reg, reg_impl, std, instr, circ, dec |
| `published-on`, `published-in` | нет | дата и издание публикации |
| `in-force-from` | нет | вступление в силу |
| `structure-lang` | да **[?]** | язык опорной структуры (`el` для Кипра, `en` для ЕС) |

Атрибуты `date-adopted`, `celex`, `eli` в модели есть, загрузчик их **не читает**.

Дочерние элементы:

- `<expression lang status source file title short-title public>` — одно на язык.
  - `status` — из `Expression.Status`: official_text, official_translation,
    unofficial_translation, our_translation.
  - `source` — код из `SOURCES` загрузчика: сейчас только `cylaw`, `cy-gazette`, `nomos`.
    Поле обязательное: неизвестный код — ошибка загрузки (`eurlex` нужно добавить).
  - `public` — показывать ли на публичной части.
- `<fragment uid kind eid parent order-key>` — фрагмент.
  - `kind` — prt tis cpt sct art par sub pnt ind anx tbl rct cit fnt sch def.
  - `parent` — **eid** родителя, который должен стоять в файле **раньше**; иначе родитель
    молча пустой. Порядок фрагментов в файле — сверху вниз по дереву.
  - `uid` — постоянный: сборщик сохраняет его по eid при пересборке.
- `<amendment key provision kind published-on in-force-from applies-from>текст</amendment>` —
  изменение, которое вносит **этот** акт; `key` вида `art_2:insert`, базовый текст — `base`;
  текст элемента — примечание. `marker` загрузчик не читает.
- `<relation kind to>` — связь с другим актом (`amends`, `transposes`, `based_on`, `repeals`,
  `corrects`, `explains`). Акта `to` нет в базе — ошибка загрузки: изменяющие акты должны
  лежать рядом.
- `<version …>` внутри фрагмента — редакция: `key`, `num`, `heading`, `amendment`
  (`<uid акта>#<key>`; несуществующее изменение — ошибка), `state`, `valid-from`,
  `applies-from`, `known-from` (по умолчанию 2024-01-01).

## 4. Языковые файлы

```xml
<texts act="cy/law/2000/95_I" lang="ru">
  <text fragment="<uid фрагмента>" version="<key редакции>" heading="…" unknown="…">
    <p>… <ref id="r1" to="art_11">статьи 11</ref> …</p>
    <note kind="…" origin="…" status="…">…</note>
  </text>
</texts>
```

- Один `<text>` на фрагмент и редакцию. Разметка — p, ref, em, strong, sup, sub, fn, margin,
  table, q (модель 3.4).
- `<ref to>`: eid своего акта (`art_11`), `uid#eid` (`eu/dir/2006/112#art_9`) или `uid`.
  `mode`, `date` загрузчик не читает; `resolved` вычисляется, `external_url` не ставится.
- `<note>` — примечания (сомнение в переводе и т. п.).
- `translator`, `review` у `<text>` в файлах есть (`claude-sonnet-5-5`, `draft`), загрузчик их
  **не загружает**, хотя поля `FragmentText.translator`, `review` в модели есть.

## 5. Команды

Из корня `Nomos`:

```bash
uv run python src/manage.py collect_cylaw cy/law/2000/95_I
```

Собирает закон Кипра по `collect.xml`: PDF → `act.xml`, `el.xml`, `cylaw-notes.xml` закона и
`act.xml`, `el.xml` изменяющих актов; размечает ссылки в `el.xml` и заново — в `ru.xml`;
печатает отчёт. `--texts-dir` — другая папка текстов.

```bash
uv run python src/manage.py load_texts
```

Пересобирает БД из `Nomos-texts`: в одной транзакции удаляет всё и грузит заново. Ошибка —
откат, БД прежняя.

```bash
uv run python src/manage.py check
```

```bash
uv run python src/manage.py migrate
```

```bash
uv run python src/manage.py test nomos
```

Тесты **[?]** (точный вызов не проверялся). Тест загрузки пропускается, если нет
`NOMOS_TEXTS_DIR/cy/law/2000/95_I/collect.xml`.

Сборщика актов ЕС (`collect_eurlex`) **нет** — план в `eu-vat-sources.md`.

## 6. Порядок работы

1. **Сбор.** Манифест → сборщик → `act.xml` и тексты оригинала. Отчёт сборщика разбирается;
   ручные решения — в манифест, не правкой собранных файлов.
2. **Загрузка и проверка.** `load_texts`, затем `check`, тесты, просмотр на сайте (`npm run dev`
   и Django).
3. **Перевод** — отдельными сессиями Claude Code, пачками, не через API (CLAUDE.md).
   - Пачка — раздел или группа статей; результат — `ru.xml` с `translator` (модель) и
     `review="draft"`.
   - Глоссарий — `glossary/el-ru.xml`; для актов ЕС нужен `en-ru` (нет).
   - Сомнения — `<note>` в тексте.
   - Размер пачки, порядок выверки и смена `review` после неё — **[?]**.
4. **Ссылки.** Правила — `src/nomos/sources/cylaw/refs.py` (`refs.EL`); размечает сборщик.
   Перевод размечается заново по тем же id (`mark_translation`): расхождения — в отчёт.
   Ссылки на другие акты сейчас пропускаются.
5. **Проверки** перед слиянием: `check`, `migrate`, тесты, `load_texts` без ошибок.

## 7. Ветки и слияние

- Код (`Nomos`): работа от `develop`, каждая сессия — своя ветка; слияние в `develop` без
  вопроса, если `check`, `migrate` и тесты проходят; в `main` — только с согласия
  пользователя.
- Тексты (`Nomos-texts`): так же — ветка сессии от `develop` **[?]** (так делали сессии
  сборщика; правило нигде не записано).
- Изменение модели — только после утверждения проекта в `docs/design/`.
- Новая функция остаётся в своей ветке до проверки.

## 8. Открытые вопросы

1. Обязателен ли `structure-lang` и что будет без него.
2. Правило веток `Nomos-texts` — записать ли в CLAUDE.md.
3. Точная команда тестов.
4. Загружать ли `translator`/`review` (поля в модели есть) и кто ставит `review` после
   выверки.
5. Размер пачки перевода и порядок выверки.
6. Где описать манифест `collect.xml` (сейчас — только в коде сборщика).
