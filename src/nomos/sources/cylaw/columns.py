"""Таблицы в несколько столбцов в приложениях сводки CyLaw (59(Ι)/2010, Πίνακες 4 и 5).

pdftotext -layout ставит столбцы рядом в одной строке («Βασικό … Συμπληρωματικό»), и
обычный разбор склеивал их построчно. Здесь таблица находится по строке шапки, столбцы
делятся по координате x, а таблица в строках заменяется строкой-меткой: парсер ставит на
её место фрагмент `tbl` с ячейками `cel` (адрес ячейки — см. Cell.token).
"""

import re
from dataclasses import dataclass, field

from . import layout

# метка таблицы в строках: символ из частной области Unicode и номер таблицы
MARK = ''
MARK_INDENT = 30
# отрезок строки: слова, между которыми не больше двух пробелов
SEGMENT_RE = re.compile(r'\S+(?: {1,2}\S+)*')
# строка во всю ширину бывает выровнена по ширине: между словами до пяти пробелов
WIDE_SEGMENT_RE = re.compile(r'\S+(?: {1,5}\S+)*')
HEADER_GAP = 8
HEADER_WORDS = 3
HEADER_INDENT = 15
CAPTION_INDENT = 25
CAPTION_WORDS = 8
# ряды основной части таблицы разделены не меньше чем двумя пустыми строками
ROW_GAP = 2
# поиск пробела для разреза строки, в которой слова столбцов сошлись без промежутка
CUT_REACH = 12
ITEM_START_RE = re.compile(r'^(\([0-9α-ωivxν]{1,5}\)|\d+\.)\s')
PHRASE_END_RE = re.compile(r'([.:;∙·˙-]|\sκαι|\sή)$')


@dataclass
class Segment:
    start: int
    end: int
    text: str

    @property
    def center(self):
        return (self.start + self.end) / 2


@dataclass
class Cell:
    """Ячейка: ряд шапки `h1`, `h2` или ряд основной части `1`, `2`, …; столбец с 1.

    Объединённые ячейки — диапазоном: `cel_h1-2_1` (два ряда шапки), `cel_h1_2-3`."""

    row: str
    col: int
    last_row: str = ''
    last_col: int = 0
    header: bool = False
    text: str = ''
    blocks: list = field(default_factory=list)

    @property
    def token(self):
        row = self.row
        if self.last_row and self.last_row != self.row:
            row = '%s-%s' % (row, self.last_row.lstrip('h'))
        col = str(self.col)
        if self.last_col and self.last_col != self.col:
            col = '%s-%d' % (col, self.last_col)
        return 'cel_%s_%s' % (row, col)


@dataclass
class Table:
    caption: str = ''
    cells: list = field(default_factory=list)


def segments(line, pattern=SEGMENT_RE):
    return [
        Segment(match.start(), match.end(), match.group())
        for match in pattern.finditer(line)
    ]


def header_segments(line):
    """Строка шапки: 2–4 коротких названия столбцов, далеко друг от друга, без цифр."""
    found = segments(line)
    if not 2 <= len(found) <= 4 or found[0].start < HEADER_INDENT:
        return None
    for previous, segment in zip(found, found[1:]):
        if segment.start - previous.end < HEADER_GAP:
            return None
    for segment in found:
        if len(segment.text.split()) > HEADER_WORDS:
            return None
        if not segment.text[0].isupper() or re.search(r'[\d.,:;()]', segment.text):
            return None
    return found


def header_at(lines, index, skip):
    """Шапка таблицы в строке `index`. Шапка, у которой рядом короткие строки («Αύξων»,
    «Αρ.», «%»: названия столбцов в несколько строк, 59(Ι), Έκτος Πίνακας), — не та."""
    if skip(lines[index]):
        return None
    found = header_segments(lines[index])
    if found is None:
        return None
    for near in (index - 1, index + 1):
        if 0 <= near < len(lines) and not skip(lines[near]):
            text = lines[near].strip()
            if (
                text
                and len(text.split()) <= 2
                and caption_of(lines[near]) is None
                and header_segments(lines[near]) is None
            ):
                return None
    return found


def caption_of(line):
    found = segments(line)
    if len(found) != 1 or found[0].start < CAPTION_INDENT:
        return None
    text = found[0].text
    if len(text.split()) > CAPTION_WORDS or text[-1] in '.:;,':
        return None
    return text


def extract(lines, start, skip, stops):
    """Таблицы с шапкой после строки `start` → (строки с метками, [Table]).

    `skip(line)` — колонтитулы; `stops(text)` — заголовок части или приложения: на нём
    таблица кончается."""
    result = list(lines[:start])
    tables = []
    index = start
    while index < len(lines):
        header = header_at(lines, index, skip)
        if header is None:
            result.append(lines[index])
            index += 1
            continue
        table, after = read_table(lines, index, header, skip, stops)
        if table is None:
            result.append(lines[index])
            index += 1
            continue
        index = after
        caption = caption_of(result[-1]) if result else None
        if caption is not None and not stops(caption):
            result.pop()
        else:
            caption = ''
        table.caption = caption
        tables.append(table)
        result.extend(['', ' ' * MARK_INDENT + '%s%d' % (MARK, len(tables)), ''])
    return result, tables


def read_table(lines, index, header, skip, stops):
    """Шапка в строке `index` (и, если есть, в следующей) → (Table, индекс после неё).

    Строка, похожая на шапку, под которой нет текста в её столбцах, — не таблица: (None, …)."""
    lower = header_segments(lines[index + 1]) if index + 1 < len(lines) else None
    table = Table()
    if lower is None:
        leaves = header
        for number, segment in enumerate(leaves, 1):
            table.cells.append(Cell('h1', number, header=True, text=segment.text))
        index += 1
    else:
        leaves = sorted(
            [segment for segment in header if segment.end < lower[0].start] + lower,
            key=lambda segment: segment.start,
        )
        top_cells(table, header, lower, leaves)
        index += 2
    body, index = read_body(lines, index, leaves, skip, stops)
    cuts = boundaries(body, leaves)
    if cuts is None:
        return None, index
    rows = split_rows(body)
    for row_number, row in enumerate(rows, 1):
        columns = [[] for _ in leaves]
        for line in row:
            for column, part in enumerate(cut(line, cuts)):
                columns[column].append(part)
        for column, parts in enumerate(columns, 1):
            blocks = cell_blocks(parts)
            if blocks:
                table.cells.append(Cell(str(row_number), column, blocks=blocks))
    return table, index


def top_cells(table, header, lower, leaves):
    """Шапка в два ряда: названия слева от нижнего ряда — на оба ряда, остальные —
    над столбцами нижнего ряда, ближайшими по центру."""
    owners = [
        segment for segment in header if segment not in leaves
    ]
    spans = {id(segment): [] for segment in owners}
    for segment in lower:
        owner = min(owners, key=lambda top: abs(top.center - segment.center))
        spans[id(owner)].append(leaves.index(segment) + 1)
    for segment in header:
        if segment in leaves:
            number = leaves.index(segment) + 1
            table.cells.append(
                Cell('h1', number, last_row='h2', header=True, text=segment.text)
            )
        else:
            columns = spans[id(segment)]
            table.cells.append(Cell(
                'h1',
                min(columns),
                last_col=max(columns),
                header=True,
                text=segment.text,
            ))
    for segment in lower:
        number = leaves.index(segment) + 1
        table.cells.append(Cell('h2', number, header=True, text=segment.text))


def spans_columns(line, leaves):
    """Строка во всю ширину: один отрезок от первого столбца за начало последнего."""
    return any(
        segment.start < leaves[0].end and segment.end > leaves[-1].start
        for segment in segments(line, WIDE_SEGMENT_RE)
    )


def read_body(lines, index, leaves, skip, stops):
    """Строки таблицы до заголовка, новой шапки или двух строк подряд во всю ширину
    (одна такая строка бывает и внутри таблицы, когда столбцы сошлись без промежутка:
    тогда следующая строка — снова в два столбца)."""
    body = []
    while index < len(lines):
        line = lines[index]
        if skip(line):
            index += 1
            continue
        text = line.strip()
        if text and (stops(text) or header_segments(line)):
            break
        if text and spans_columns(line, leaves):
            following = next_text(lines, index + 1, skip)
            if (
                following is None
                or stops(following.strip())
                or len(segments(following)) < 2
                or spans_columns(following, leaves)
            ):
                break
        body.append(line)
        index += 1
    while body and not body[-1].strip():
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    return body, index


def next_text(lines, index, skip):
    for line in lines[index:]:
        if line.strip() and not skip(line):
            return line
    return None


def boundaries(body, leaves):
    """Начало каждого столбца, кроме первого: самый левый отрезок правее названия
    предыдущего столбца в шапке."""
    cuts = []
    for previous in leaves[:-1]:
        starts = [
            segment.start
            for line in body
            for segment in segments(line)
            if segment.start > previous.end
            and (not cuts or segment.start > cuts[-1])
        ]
        if not starts:
            return None
        cuts.append(min(starts))
    return cuts


def cut(line, cuts):
    """Строка → части по столбцам; разрез внутри слова сдвигается к ближайшему пробелу."""
    parts = []
    position = 0
    for boundary in cuts:
        boundary = max(boundary, position)
        if 0 < boundary < len(line) and line[boundary - 1] != ' ' and line[boundary] != ' ':
            spaces = [
                index + 1
                for index in range(
                    max(position, boundary - CUT_REACH),
                    min(len(line), boundary + CUT_REACH),
                )
                if line[index] == ' '
            ]
            if spaces:
                boundary = min(spaces, key=lambda space: abs(space - boundary))
        parts.append(line[position:boundary])
        position = boundary
    parts.append(line[position:])
    return parts


def split_rows(body):
    rows = [[]]
    blank = 0
    for line in body:
        if not line.strip():
            blank += 1
            continue
        if blank >= ROW_GAP and rows[-1]:
            rows.append([])
        blank = 0
        rows[-1].append(line)
    return [row for row in rows if row]


def cell_blocks(parts):
    """Строки ячейки → абзацы. Пустые строки в столбце — межстрочный интервал PDF, не
    граница абзаца; абзац начинает номер пункта после законченной фразы."""
    lines = [part.rstrip() for part in parts if part.strip()]
    groups = []
    for line in lines:
        starts = ITEM_START_RE.match(line.strip())
        if groups and not (starts and PHRASE_END_RE.search(groups[-1][-1])):
            groups[-1].append(line)
        else:
            groups.append([line])
    if not groups:
        return []
    indent = min(len(line) - len(line.lstrip(' ')) for line in lines)
    return [
        layout.make_block(0, [line[indent:] for line in group])
        for group in groups
    ]
