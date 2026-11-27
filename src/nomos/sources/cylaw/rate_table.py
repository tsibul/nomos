"""Таблица ставок Πρώτο Παράρτημα 19/1963 (Ο περί Χαρτοσήμων Νόμος) в сводке
CyLaw.

Три столбца: «Στοιχείο» (номер позиции), «Είδος Εγγράφου», «Αξία Χαρτοσήμου». В
`pdftotext -layout` столбцы перемешаны: номер и ставка стоят по высоте
посередине своей
строки таблицы, между строками соседних столбцов. Здесь таблица собирается по
координатам
слов (`pdftotext -bbox`) и заменяет свои строки раскладки: позиция — «N.
текст», ставка —
абзацем после строки таблицы, к которой она относится (как в таблицах ставок
акцизов).
"""

import re
from dataclasses import dataclass, field

from . import leginet
from .layout import fix_lookalikes

TITLE = "ΠΡΩΤΟ ΠΑΡΑΡΤΗΜΑ"
HEADER_LAST = "Χαρτοσήμου"
END = "ΔΕΥΤΕΡΟ ΠΑΡΑΡΤΗΜΑ"
HEADER = "Στοιχείο Είδος Εγγράφου Αξία Χαρτοσήμου"
# левые края столбцов, pt: номер правее не кончается, ставка не левее
# начинается
NUMBER_RIGHT = 80
RATE_LEFT = 444
# левый край текста, pt, и ширина знака раскладки
TEXT_LEFT = 87
CHAR_WIDTH = 4.4
TEXT_INDENT = 18
TOP_INDENT = 10
LETTER_INDENT = 15
RATE_SHIFT = 2
HEADER_INDENT = 40
# межстрочный шаг ≈10,5 pt; больше — просвет между строками таблицы
GAP = 12
# равные расстояния до двух строк таблицы, pt
TIE = 1
# колонтитулы страницы: дата и заголовок сверху, адрес и номер страницы снизу
TOP_MARGIN = 30
BOTTOM_MARGIN = 770

MARKER_RE = re.compile(r"^(\([^)\s]{1,4}\)|[ΑΒ]\.)\s")
LETTER_RE = re.compile(r"^[ΑΒ]\.\s")
SENTENCE_END = ".:;)"
EXCEPTION_PREFIX = "Εξαιρ"
PROVISO_PREFIX = "Νοείται"


@dataclass
class Line:
    page: int
    y: float
    x0: float
    text: str


@dataclass
class Row:
    """Строка столбца «Είδος Εγγράφου»: пункт, абзац или заголовок перечня."""

    lines: list
    number: str = ""
    rates: list = field(default_factory=list)

    @property
    def text(self):
        return " ".join(line.text for line in self.lines)

    def distance(self, page, y):
        """Расстояние по высоте от `y` до строки на странице `page`; на другой
        — бесконечно."""
        ys = [line.y for line in self.lines if line.page == page]
        if not ys:
            return float("inf")
        if min(ys) <= y <= max(ys):
            return 0
        return min(abs(y - min(ys)), abs(y - max(ys)))


def table_lines(pages):
    """Строки таблицы → три списка Line: номера, текст, ставки (сверху вниз по
    страницам)."""
    numbers, texts, rates = [], [], []
    state = "before"
    for page_number, words in enumerate(pages):
        for y, parts in leginet.page_lines(words):
            line = " ".join(word for _, _, word in parts)
            if state == "before":
                state = "title" if line.strip() == TITLE else state
                continue
            if state == "title":
                state = (
                    "table" if line.strip().endswith(HEADER_LAST) else state
                )
                continue
            if line.strip() == END:
                return numbers, texts, rates
            if y < TOP_MARGIN or y > BOTTOM_MARGIN:
                continue
            number = [part for part in parts if part[1] < NUMBER_RIGHT]
            text = [
                part
                for part in parts
                if part[1] >= NUMBER_RIGHT and part[0] < RATE_LEFT
            ]
            rate = [part for part in parts if part[0] >= RATE_LEFT]
            for target, words_of in (
                (numbers, number),
                (texts, text),
                (rates, rate),
            ):
                if words_of:
                    text = fix_lookalikes(
                        " ".join(word for _, _, word in words_of)
                    )
                    target.append(Line(page_number, y, words_of[0][0], text))
    raise ValueError("таблица %s не найдена" % TITLE)


def text_rows(lines):
    """Строки столбца текста → строки таблицы. Новая строка — с пункта («(1)»,
    «(α)», «Α.»)
    или с заглавной буквы после конца фразы либо просвета; со строчной —
    продолжение, в том
    числе после просвета (строка с номером или ставкой посередине) и переноса
    страницы."""
    rows = []
    for line in lines:
        if not rows or MARKER_RE.match(line.text):
            rows.append(Row([line]))
            continue
        previous = rows[-1].lines[-1]
        if not line.text[0].isupper():
            rows[-1].lines.append(line)
            continue
        ended = previous.text.rstrip()[-1] in SENTENCE_END
        close = previous.page != line.page or line.y - previous.y <= GAP
        if close and not ended:
            rows[-1].lines.append(line)
            continue
        rows.append(Row([line]))
    return rows


def rate_cells(lines):
    """Строки столбца ставок → ячейки: новая ячейка — с заглавной буквы или
    после просвета
    (у ставок с другой страницы — только с заглавной)."""
    cells = []
    for line in lines:
        if cells and not line.text[0].isupper():
            previous = cells[-1][-1]
            if previous.page != line.page or line.y - previous.y <= GAP:
                cells[-1].append(line)
                continue
        cells.append([line])
    return cells


def nearest(rows, page, y, later):
    """Индекс строки таблицы, ближайшей по высоте к `y`; при равных — поздней
    или ранней."""
    best = None
    for index, row in enumerate(rows):
        distance = row.distance(page, y)
        if best is None or distance < best[0] - TIE:
            best = (distance, index)
        elif later and distance <= best[0] + TIE:
            best = (min(best[0], distance), index)
    return best[1]


def is_proviso(row):
    return row.text.startswith(PROVISO_PREFIX)


def starts_inside(row):
    return bool(MARKER_RE.match(row.text)) or is_proviso(row)


def place_numbers(rows, numbers):
    """Номер позиции → её первая строка таблицы: в строке с номером или
    ближайшая к нему.
    Пункт «(1)» и оговорка «Νοείται» позицию не начинают — она в строке перед
    ними."""
    heads = []
    for number in numbers:
        index = nearest(rows, number.page, number.y, later=False)
        earlier = heads[-1] if heads else -1
        while index - 1 > earlier and starts_inside(rows[index]):
            index -= 1
        rows[index].number = number.text
        heads.append(index)
    return heads


def eligible(rows, heads):
    """Строки, к которым может относиться ставка: не оговорка «Νοείται» и не
    изъятие
    («Εξαιρούνται:» и его перечень — до следующей позиции или пункта «Α.»,
    «Β.»)."""
    result = []
    exception = False
    for index, row in enumerate(rows):
        if index in heads or LETTER_RE.match(row.text):
            exception = False
        if row.text.startswith(EXCEPTION_PREFIX):
            exception = True
        result.append(not exception and not is_proviso(row))
    return result


def item_end(heads, index, count):
    """Конец позиции, в которой строка `index`: индекс первой строки следующей
    позиции."""
    return next((head for head in heads if head > index), count)


def place_rates(rows, cells, heads):
    """Ставка → строка таблицы: ближайшая к середине ячейки. Строка-заголовок
    перечня
    («Σύμβαση:») передаёт ставку следующей строке позиции; оговорка и изъятие —
    предыдущей
    строке, к которой ставка может относиться."""
    allowed = eligible(rows, heads)
    for cell in cells:
        first = [line for line in cell if line.page == cell[0].page]
        middle = (first[0].y + first[-1].y) / 2
        index = nearest(rows, cell[0].page, middle, later=True)
        while not allowed[index] and index > 0:
            index -= 1
        following = index + 1
        if (
            rows[index].text.endswith(":")
            and following < item_end(heads, index, len(rows))
            and allowed[following]
        ):
            index = following
        rows[index].rates.append(" ".join(line.text for line in cell))


def indent_of(line):
    return TEXT_INDENT + round((line.x0 - TEXT_LEFT) / CHAR_WIDTH)


def row_indent(row):
    """Отступ первой строки: позиция — у поля; «Α.», «Β.» — левее текста
    позиции, чтобы
    закрыть изъятие предыдущего пункта («Εξαιρούνται:» после «Α.»)."""
    if row.number:
        return TOP_INDENT
    if LETTER_RE.match(row.text):
        return LETTER_INDENT
    return indent_of(row.lines[0])


def layout_lines(rows):
    """Строки таблицы → строки раскладки; второе и третье — индексы первых
    строк абзацев
    таблицы и строк ставок."""
    output = [" " * HEADER_INDENT + HEADER, ""]
    starts = set()
    rates = set()
    for row in rows:
        starts.add(len(output))
        for position, line in enumerate(row.lines):
            if position == 0 and row.number:
                output.append(
                    " " * TOP_INDENT + "%s. %s" % (row.number, line.text)
                )
            elif position == 0:
                output.append(" " * row_indent(row) + line.text)
            else:
                output.append(" " * indent_of(line) + line.text)
        output.append("")
        # ставка глубже своей строки: продолжает её пункт, а не открывает
        # соседний
        indent = row_indent(row) + RATE_SHIFT
        for rate in row.rates:
            starts.add(len(output))
            rates.add(len(output))
            output.append(" " * indent + rate)
            output.append("")
    return output, starts, rates


def rows_of(pages):
    """Страницы PDF → строки таблицы с номерами позиций и ставками."""
    numbers, texts, rates = table_lines(pages)
    rows = text_rows(texts)
    heads = place_numbers(rows, numbers)
    place_rates(rows, rate_cells(rates), heads)
    return rows


def rewrite(lines, path):
    """Строки раскладки сводки → те же строки с таблицей, собранной по
    столбцам;
    второе — номера первых строк абзацев таблицы: это текст позиций, не
    заголовки, хоть
    ставка и коротка перед следующей позицией; третье — номера строк ставок."""
    stripped = [line.strip() for line in lines]
    title = stripped.index(TITLE)
    start = next(
        index
        for index in range(title, len(lines))
        if stripped[index] == "Αξία"
    )
    end = stripped.index(END, start)
    table, starts, rates = layout_lines(rows_of(leginet.pdf_words(path)))
    # номера строк у layout.blocks — с единицы
    return (
        lines[:start] + table + [""] + lines[end:],
        {start + index + 1 for index in starts},
        {start + index + 1 for index in rates},
    )
