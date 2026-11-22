"""Сводный текст Leginet (leginet.eu): PDF с полем и текстом в одной строке → строки в
раскладке сводки CyLaw, которую разбирает `consolidated.Parser`.

Поле (левее колонки текста) — заголовок статьи и ссылки на законы. Заголовок выносится
своей строкой перед статьёй, ссылки на законы отбрасываются (их собирает `amended_by`).
Перед преамбулой — оглавление, оно не нужно.
"""

import html
import re
import subprocess
from dataclasses import dataclass

from .layout import fix_lookalikes

# правый край поля, pt: слова поля кончаются не правее, текст начинается правее
MARGIN_RIGHT = 166
# левый край колонки текста, pt, и ширина знака для перевода в отступ строки
TEXT_LEFT = 170
CHAR_WIDTH = 5
# по центру: заголовки Μέρος
CENTER_LEFT = 230
LINE_TOLERANCE = 2
# межстрочный шаг текста ≈11,4 pt; больше — пустая строка между абзацами
GAP = 16
PAGE_BREAK = '\f'

WORD_RE = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>'
)
FURNITURE_RE = re.compile(
    r'^(Copyright © Leginet|Cyprus Legal Portal|\d+\([IΙ]\)/\d{4}$)'
)
# ссылки на законы на полях: «136(Ι) του 2002», «Κεφ. 113.», «8(α)(β) του 15(Ι) του 2026.»,
# перечень через пробел
LAW_REF = (
    r'(\d+[Α-Ω]?(\(\d+\))?(\([α-ω]+\))*\s+του\s+)?'
    r'(\d+(\([IΙ]\))?\s+του\s+\d{4}|Κεφ\.\s*\d+[Α-Ω]?)'
)
LAW_REF_RE = re.compile(rf'^{LAW_REF}(\s+{LAW_REF})*\.?$')
# «2 του 15(Ι) του 2026.»: статья изменяющего закона
AMENDED_BY_RE = re.compile(
    r'^\d+[Α-Ω]?(\(\d+\))?(\([α-ω]+\))*\s+του\s+(\d+)\([IΙ]\)\s+του\s+(\d{4})'
)
PREAMBLE_RE = re.compile(r'^ΕΠΕΙΔΗ')
ARTICLE_RE = re.compile(r'^(\d+[Α-Ω]?)\.')
# строка оглавления: «28. Παροχή δικαιοδοσίας στο Δικαστήριο Εργατικών Διαφορών»
TOC_RE = re.compile(r'^(\d+[Α-Ω]?)\.\s+(\S.*)$')
# статья изменяющего закона, которую Leginet приводит в сводке: «11. Ο παρών Νόμος [15(Ι)/2026]…»
AMENDING_PROVISION_RE = re.compile(r'^\d+[Α-Ω]?\.\s+Ο\s+παρών\s+Νόμος\s+\[')
# знаки, которыми Leginet заменяет греческие буквы
GLYPHS = str.maketrans({'µ': 'μ', '∆': 'Δ', 'Ω': 'Ω'})


def pdf_words(path):
    """Страницы PDF → [[(x0, y, x1, текст), …], …], y — нижний край слова."""
    output = subprocess.run(
        ['pdftotext', '-bbox', str(path), '-'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pages = []
    for page in output.split('<page ')[1:]:
        words = []
        for match in WORD_RE.finditer(page):
            # строка — по нижнему краю: у цифр и букв разный верхний
            x0, _, x1, y1 = (float(value) for value in match.groups()[:4])
            text = html.unescape(match.group(5)).translate(GLYPHS)
            words.append((x0, y1, x1, text))
        pages.append(words)
    return pages


def page_lines(words, tolerance=LINE_TOLERANCE):
    """Слова страницы → строки: [(y, [(x0, x1, текст), …]), …] сверху вниз."""
    lines = []
    for x0, y, x1, text in sorted(words, key=lambda word: (word[1], word[0])):
        if lines and abs(lines[-1][0] - y) <= tolerance:
            lines[-1][1].append((x0, x1, text))
        else:
            lines.append((y, [(x0, x1, text)]))
    return [(y, sorted(parts)) for y, parts in lines]


@dataclass(frozen=True)
class Columns:
    """Раскладка страницы, pt: правый край поля, левый край текста, начало строк по центру;
    `furniture` — колонтитулы, `line_tolerance` — разброс нижнего края слов одной строки,
    `gap` — просвет, больше которого между строками пустая строка."""

    margin_right: float
    text_left: float
    center_left: float
    furniture: re.Pattern
    line_tolerance: float = LINE_TOLERANCE
    gap: float = GAP


LEGINET = Columns(MARGIN_RIGHT, TEXT_LEFT, CENTER_LEFT, FURNITURE_RE)


def split(parts, columns=LEGINET):
    """Слова строки → (поле, текст, левый край текста)."""
    margin = ' '.join(text for x0, x1, text in parts if x1 <= columns.margin_right)
    body = [(x0, text) for x0, x1, text in parts if x1 > columns.margin_right]
    left = body[0][0] if body else None
    return margin, ' '.join(text for _, text in body), left


def indent_of(left, columns=LEGINET):
    if left >= columns.center_left:
        return 40
    return 12 + max(0, round((left - columns.text_left) / CHAR_WIDTH))


def rows(path, columns=LEGINET, pages=None):
    """Строки без колонтитулов: (поле, текст, отступ, разрыв перед строкой).

    Разрыв — `PAGE_BREAK` на границе страниц или '' для пустой строки (просвет больше
    `GAP`)."""
    result = []
    for words in pages if pages is not None else pdf_words(path):
        previous = None
        for y, parts in page_lines(words, columns.line_tolerance):
            line = ' '.join(text for _, _, text in parts)
            if columns.furniture.match(line) or line.startswith('Page '):
                continue
            margin, body, left = split(parts, columns)
            gap = None
            if previous is None:
                gap = PAGE_BREAK
            elif body and y - previous > columns.gap:
                gap = ''
            if body or previous is None:
                # просвет считается между строками текста: строки поля мельче и чаще
                previous = y
            indent = indent_of(left, columns) if left is not None else 0
            result.append((margin, body, indent, gap))
    return result


def amended_by(path, year_from):
    """Ссылки поля «N του X(Ι) του YYYY» с годом не раньше `year_from`: [(ссылка, начало
    текста рядом), …] — для сверки изменений, не для текста."""
    all_rows = rows(path)
    found = []
    for index, note in margin_notes(all_rows):
        match = AMENDED_BY_RE.match(note)
        if match and int(match.group(4)) >= year_from:
            body = next((body for _, body, _, _ in all_rows[index:] if body), '')
            found.append((note, body[:60]))
    return found


def margin_notes(all_rows, across_gaps=False):
    """Тексты поля, собранные из строк: [(номер первой строки, текст), …].

    Запись поля переносится на несколько строк, между ними бывают строки без поля. Запись
    кончается точкой; новая начинается и после пустой строки или разрыва страницы — кроме
    `across_gaps`: тогда через пустую строку переносится запись, кроме ссылки на закон."""
    notes = []
    parts = []
    first = 0
    for index, (margin, _, _, gap) in enumerate(all_rows):
        if not margin:
            continue
        breaks = not across_gaps or LAW_REF_RE.match(' '.join(parts))
        if parts and gap is not None and breaks:
            notes.append((first, ' '.join(parts)))
            parts = []
        if not parts:
            first = index
        parts.append(margin)
        if margin.endswith('.'):
            notes.append((first, ' '.join(parts)))
            parts = []
    if parts:
        notes.append((first, ' '.join(parts)))
    return notes


def article_headings(all_rows, toc=None, across_gaps=False):
    """Заголовки статей с поля: {номер строки, где начинается статья: заголовок}.

    Заголовок начинается в строке статьи или ниже, если над ним на поле ссылка на закон.
    Запись поля, начатая не в абзаце статьи (ссылка «Πίνακας.» у εδαφίου), — не заголовок.
    Статья без заголовка на поле получает заголовок из оглавления `toc` {номер: заголовок}."""
    starts = []
    start = 0
    for index, (_, body, _, gap) in enumerate(all_rows):
        if body and gap is not None:
            start = index
        starts.append(start)
    headings = {}
    for index, text in margin_notes(all_rows, across_gaps):
        start = starts[index]
        if LAW_REF_RE.match(text) or not ARTICLE_RE.match(all_rows[start][1]):
            continue
        headings.setdefault(start, text)
    for index, (_, body, _, gap) in enumerate(all_rows):
        article = ARTICLE_RE.match(body) if body and gap is not None else None
        if article and index not in headings and article.group(1) in (toc or {}):
            headings[index] = toc[article.group(1)].rstrip('.') + '.'
    return headings


def toc_headings(toc_rows):
    """Оглавление перед преамбулой → {номер статьи: заголовок}; первая запись номера."""
    found = {}
    for margin, body, _, _ in toc_rows:
        # оглавление сдвинуто влево: номер статьи попадает в поле
        match = TOC_RE.match(' '.join(part for part in (margin, body) if part))
        if match:
            found.setdefault(match.group(1), match.group(2))
    return found


def pdf_lines(path):
    """Строки в раскладке сводки CyLaw: заголовок поля — своей строкой с отступом 6,
    текст — с отступом 12 и больше, `Προοίμιο` перед преамбулой."""
    all_rows = rows(path)
    start = next(
        index for index, (_, body, _, _) in enumerate(all_rows) if PREAMBLE_RE.match(body)
    )
    toc = toc_headings(all_rows[:start])
    return ['Προοίμιο', ''] + body_lines(all_rows[start:], toc)


def body_lines(all_rows, toc, across_gaps=False):
    """Строки текста после оглавления → строки в раскладке сводки CyLaw; `across_gaps` —
    как в margin_notes."""
    headings = article_headings(all_rows, toc, across_gaps)
    lines = []
    skipping = False
    for index, (_, body, indent, gap) in enumerate(all_rows):
        if body and AMENDING_PROVISION_RE.match(body):
            skipping = True
        elif gap is not None and body:
            skipping = False
        if skipping:
            continue
        if gap == PAGE_BREAK:
            lines.append(PAGE_BREAK)
        elif gap == '':
            lines.append('')
        if index in headings:
            # заголовок поля — своей строкой перед текстом, где он начался
            lines.extend(['', ' ' * 6 + headings[index], ''])
        if body:
            lines.append(' ' * indent + fix_lookalikes(body))
    return lines
