"""Сводный текст Τμήμα Τελωνείων (mof.gov.cy/customs) → строки в раскладке сводки CyLaw.

Раскладка как у Leginet: заголовок статьи и ссылки на законы — на поле слева, текст — правее.
Колонки у разных сводок разные, их положение берётся из номеров статей «N.». Преамбулы нет:
текст начинается с «ΜΕΡΟΣ I» перед статьёй 1, до него — сведения об изменениях и оглавление.
"""

import re
from collections import Counter
from dataclasses import replace

from . import layout, leginet

# строка, после которой разбор сводки CyLaw без преамбулы начинает текст
# (consolidated.HISTORY_HEADING)
SCHEDULE_HEADING = 'ΠΑΡΑΡΤΗΜΑ'
TEXT_HEADING = 'Ιστορικό Τροποποιήσεων'
# номер статьи отдельным словом: по нему находится левый край колонки текста
ARTICLE_NUMBER_RE = re.compile(r'^\d+[Α-Ω]?\.$')
# номер страницы — отдельной строкой
FURNITURE_RE = re.compile(r'^\d+$')
FIRST_ARTICLE_RE = re.compile(r'^1\.\s')
ARTICLE_RE = leginet.ARTICLE_RE
# знак препинания отдельным словом и насколько он может отстоять от слова перед ним, pt
PUNCTUATION_RE = re.compile(r'^[.,·;:ꞏ∙]$')
PUNCTUATION_DX = 3
PUNCTUATION_DY = 10
PART_RE = re.compile(r'^ΜΕΡΟΣ\s')
# строка оглавления: «1.     Συνοπτικός τίτλος» или «1     Συνοπτικός τίτλος»
TOC_RE = re.compile(r'^(\d+[Α-Ω]?)\.?\s+(\S.*)$')
# заголовок Μέρους перед статьёй 1 — не дальше стольких строк
PART_LOOKBACK = 12
# заголовок на поле стоит выше или ниже строки номера не больше чем на столько, pt;
# слова с таким разбросом — одна строка
HEADING_SHIFT = 4
# просвет больше шага строк во столько раз — пустая строка между абзацами
GAP_RATIO = 1.25
# заголовки по центру начинаются правее левого края текста не меньше чем на столько, pt
CENTER_OFFSET = 90

# отступ строки у левого края текста (leginet.indent_of)
TEXT_INDENT = 12
CENTERED_INDENT = 25


def columns_of(pages):
    """Колонки по самому частому левому краю номера статьи, левее которого рядом по высоте
    заголовок на поле (в оглавлении номер стоит первым); просвет между абзацами — по самому
    частому шагу строк текста."""
    lefts = Counter()
    for words in pages:
        for x0, y, _, text in words:
            if ARTICLE_NUMBER_RE.match(text) and any(
                other_x1 < x0 and abs(other_y - y) <= HEADING_SHIFT
                for _, other_y, other_x1, _ in words
            ):
                lefts[round(x0)] += 1
    text_left = lefts.most_common(1)[0][0] - 1
    margin_right = text_left - 2
    steps = Counter()
    for words in pages:
        tops = [
            y
            for y, parts in leginet.page_lines(words, HEADING_SHIFT)
            if any(x1 > margin_right for _, x1, _ in parts)
        ]
        steps.update(round(after - before) for before, after in zip(tops, tops[1:]))
    return leginet.Columns(
        margin_right,
        text_left,
        text_left + CENTER_OFFSET,
        FURNITURE_RE,
        HEADING_SHIFT,
        steps.most_common(1)[0][0] * GAP_RATIO,
    )


def text_start(all_rows):
    """Номер строки «ΜΕΡΟΣ I» перед статьёй 1 (или самой статьи 1, если Μέρους нет)."""
    first = next(
        index
        for index, (_, body, _, gap) in enumerate(all_rows)
        if body and gap is not None and FIRST_ARTICLE_RE.match(body)
    )
    for index in range(first - 1, max(first - PART_LOOKBACK, 0) - 1, -1):
        if PART_RE.match(all_rows[index][1]):
            return index
    return first


def toc_headings(toc_rows):
    """Оглавление → {номер статьи: заголовок}; первая запись номера."""
    found = {}
    for margin, body, _, _ in toc_rows:
        match = TOC_RE.match(' '.join(part for part in (margin, body) if part))
        if match:
            found.setdefault(match.group(1), match.group(2))
    return found


def attach_punctuation(words):
    """Знак препинания отдельным словом PDF ставит выше строки: он переносится на строку
    слова, сразу за которым стоит."""
    fixed = []
    for x0, y, x1, text in words:
        if PUNCTUATION_RE.match(text):
            before = [
                other
                for other in words
                if abs(other[2] - x0) <= PUNCTUATION_DX
                and abs(other[1] - y) <= PUNCTUATION_DY
                and not PUNCTUATION_RE.match(other[3])
            ]
            if before:
                y = min(before, key=lambda other: abs(other[1] - y))[1]
        fixed.append((x0, y, x1, text))
    return fixed


def mark_articles(all_rows):
    """Статья «N.» у левого края текста начинает абзац, даже если просвет перед ней мал.

    Отступ статьи, сдвинутой на знак лишним пробелом («86.- (1)»), — отступ текста."""
    marked = []
    for margin, body, indent, gap in all_rows:
        if abs(indent - TEXT_INDENT) <= 1 and ARTICLE_RE.match(body):
            indent = TEXT_INDENT
            if gap is None:
                gap = ''
        marked.append((margin, body, indent, gap))
    return marked


def join_parts(lines):
    """«ΜΕΡΟΣ N» и название Μέρους заглавными (по центру, через пустые строки) — одна строка,
    как в сводке CyLaw."""
    joined = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not PART_RE.match(line.strip()):
            joined.append(line)
            continue
        parts = [line.rstrip()]
        following = index
        while following < len(lines) and not lines[following].strip():
            following += 1
        while following < len(lines) and is_part_title(lines[following]):
            parts.append(lines[following].strip())
            following += 1
            index = following
            while following < len(lines) and not lines[following].strip():
                following += 1
        joined.extend([' '.join(parts), ''])
    return joined


def close_paragraph(joined):
    """Пустая строка перед разрывом страницы, которым кончается `joined`, если перед ним
    текст."""
    end = len(joined)
    while end and joined[end - 1] == leginet.PAGE_BREAK:
        end -= 1
    if end < len(joined) and end and joined[end - 1].strip():
        joined.insert(end, '')


def is_part_title(line):
    text = line.strip()
    return (
        bool(text)
        and text.isupper()
        and len(line) - len(line.lstrip()) > TEXT_INDENT
        and not ARTICLE_RE.match(text)
        and not PART_RE.match(text)
    )


def split_schedule(pages, columns):
    """Страницы до «ΠΑΡΑΡΤΗΜΑ» по центру и с него: у Παράρτημα нет поля, номера пунктов
    стоят левее края текста статей."""
    for index in range(len(pages) - 1, -1, -1):
        for y, parts in leginet.page_lines(pages[index], columns.line_tolerance):
            text = ' '.join(word for _, _, word in parts)
            if text == SCHEDULE_HEADING and parts[0][0] >= columns.center_left:
                page = pages[index]
                above = [word for word in page if word[1] < y - columns.line_tolerance]
                below = [word for word in page if word[1] >= y - columns.line_tolerance]
                return pages[:index] + [above], [below] + pages[index + 1:]
    return pages, []


def schedule_columns(pages, columns):
    """Колонки Παράρτημα: поля нет, край текста — самый частый левый край строк."""
    lefts = Counter(
        round(parts[0][0])
        for words in pages
        for _, parts in leginet.page_lines(words, columns.line_tolerance)
    )
    text_left = lefts.most_common(1)[0][0] - 1
    return replace(columns, margin_right=0, text_left=text_left)


def center_headings(lines):
    """Первая строка заголовка Παράρτημα, перенесённого на следующую строку по центру,
    стоит левее центра; она тоже по центру. Иначе заголовок — текст предыдущего пункта."""
    centered = list(lines)
    for index in range(1, len(centered) - 2):
        line = centered[index]
        following = centered[index + 2]
        if (
            not centered[index - 1].strip()
            and not centered[index + 1].strip()
            and indent_of_line(line) > TEXT_INDENT
            and not layout.starts_item(line)
            and indent_of_line(following) >= CENTERED_INDENT
            and following.strip()[:1].islower()
        ):
            centered[index] = ' ' * CENTERED_INDENT + line.strip()
    return centered


def indent_of_line(line):
    return len(line) - len(line.lstrip())


def close_before_centered(lines):
    """Пустая строка перед строкой по центру после разрыва страницы: иначе она продолжает
    абзац предыдущей страницы (Μέρος, заголовок пункта Παράρτημα)."""
    closed = []
    for line in lines:
        if line.strip() and indent_of_line(line) >= CENTERED_INDENT:
            close_paragraph(closed)
        closed.append(line)
    return closed


def pdf_lines(path):
    pages = [attach_punctuation(words) for words in leginet.pdf_words(path)]
    columns = columns_of(pages)
    body_pages, schedule_pages = split_schedule(pages, columns)
    all_rows = mark_articles(leginet.rows(path, columns, body_pages))
    start = text_start(all_rows)
    toc = toc_headings(all_rows[:start])
    lines = leginet.body_lines(all_rows[start:], toc, across_gaps=True)
    if schedule_pages:
        # заголовки статей из оглавления к пунктам Παράρτημα не относятся
        schedule_rows = leginet.rows(path, schedule_columns(schedule_pages, columns),
                                     schedule_pages)
        margin, body, indent, _ = schedule_rows[0]
        schedule_rows[0] = (margin, body, indent, '')
        lines += center_headings(leginet.body_lines(schedule_rows, {}, across_gaps=True))
    return [TEXT_HEADING, ''] + close_before_centered(join_parts(lines))
