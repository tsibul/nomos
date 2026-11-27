"""Базовый закон из Επίσημη Εφημερίδα (сводки CyLaw нет, 151(Ι)/2024) → строки
в раскладке
сводки CyLaw, которые разбирает consolidated.Parser.

В газете заголовок статьи — на поле слева, рядом со строкой номера статьи; поле
бывает не на
каждой странице, и колонка текста на страницах разная. В сводке CyLaw заголовок
— отдельной
строкой у края, номер статьи — с отступом 12, текст — с отступом 20 и больше.
Ссылки на
законы и Επίσημη Εφημερίδα на поле в текст не попадают.
"""

import re
import subprocess
from collections import Counter
from pathlib import Path

from . import gazette, layout

HEADING_INDENT = 6
ARTICLE_INDENT = 12
TEXT_INDENT = 20
# с этого отступа короткая строка — заголовок группы статей
# (consolidated.is_group_heading):
# формулы и строки таблиц газеты ставятся левее
GROUP_INDENT = 25
# «2. Για …», «59.-(1) Κατά …», «12Α.—(1) …»
ARTICLE_RE = re.compile(r"^\d+[Α-Ω]{0,3}\.(?:\s|[-—–]\s*\(\d+\))")
# метка пункта в начале строки, за ней разрыв: « (ι)   κανόνες …»
FLOATING_LABEL_RE = re.compile(r"^\s*\(\w{1,5}\)\s{2,}(?=\S)")
PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
# поле есть на странице, если колонка текста не левее этой
MARGIN_COLUMN = 10
# строка поля начинается у края, поле не шире этого
MARGIN_INDENT = 4
MARGIN_WIDTH = 30
# на поле заголовок кончается точкой; так же кончается и ссылка на закон — она
# начинается
# с цифры или с «Επίσημη Εφημερίδα»
CITATION_RE = re.compile(r"^(\d|Επίσημη|Εφημερίδα|της Ε\.Ε\.|L \d|σ\.)")
# сколько строк поля может занимать заголовок (пустые строки и перенос страницы
# — не в счёт)
HEADING_LINES = 16


def pages(path):
    """Страницы PDF: списки строк в раскладке pdftotext."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        raise_labels(
            [layout.fix_lookalikes(line) for line in page.split("\n")]
        )
        for page in result.stdout.split("\f")
    ]


def raise_labels(lines):
    """Метка пункта посреди абзаца → в его первую строку. pdftotext ставит
    метку по
    вертикали в середину абзаца, если он длиннее строк метки (151(Ι)/2024, ст.
    2 «(ι)»):
    строки абзаца выше и ниже метки начинаются там же, где текст после неё,
    абзац отбит
    пустыми строками, строк выше метки не больше, чем ниже, плюс одна."""
    result = list(lines)
    for index, line in enumerate(result):
        match = FLOATING_LABEL_RE.match(line)
        if not match:
            continue
        above = continuation_count(result, index, -1, match.end())
        below = continuation_count(result, index, 1, match.end())
        first = index - above
        if (
            not above
            or above > below + 1
            or first == 0
            or result[first - 1].strip()
        ):
            continue
        result[index] = " " * match.end() + line[match.end() :]
        result[first] = line[: match.end()] + result[first][match.end() :]
    return result


def continuation_count(lines, index, step, indent):
    """Сколько строк подряд от index (вверх или вниз) — продолжение абзаца без
    метки."""
    count = 0
    position = index + step
    while 0 <= position < len(lines):
        line = lines[position]
        if (
            not line.strip()
            or indent_of(line) != indent
            or FLOATING_LABEL_RE.match(line)
        ):
            break
        count += 1
        position += step
    return count


def is_furniture(line):
    # шапка первой страницы (MASTHEAD_RE) — до «Προοίμιο» и отбрасывается
    # вместе с ним;
    # «ΜΕΡΟΣ Ι» по центру — текст закона
    return bool(PAGE_NUMBER_RE.match(line) or gazette.HEADER_RE.match(line))


def indent_of(line):
    return len(line) - len(line.lstrip())


def text_start(line):
    """Начало текста строки: после поля у края, если оно есть."""
    indent = indent_of(line)
    match = gazette.GAP_RE.search(line)
    if indent < MARGIN_INDENT and match and match.start(1) < MARGIN_WIDTH:
        return match.end(1)
    return indent


def page_column(lines):
    """Колонка текста страницы: самое частое начало текста длинных строк."""
    starts = Counter(
        text_start(line) for line in lines if len(line.strip()) >= 60
    )
    return starts.most_common(1)[0][0] if starts else 0


def split(line, column):
    """Строка → (поле, начало текста, текст). Поле — у края, левее колонки
    текста, отбито
    разрывом в 3+ пробела; строка поля без текста — короткая строка у края."""
    indent = indent_of(line)
    if column < MARGIN_COLUMN or indent >= MARGIN_INDENT:
        return "", indent, line.strip()
    match = gazette.GAP_RE.search(line)
    if match and match.start(1) < column:
        left = line[: match.start(1) + 1].strip()
        if not gazette.LABEL_RE.match(left):
            start = match.end(1)
            return left, start, line[start:].rstrip()
    if not match and len(line.strip()) < 40:
        # «Υπουργό», «Νόμου.»
        return line.strip(), column, ""
    return "", indent, line.strip()


def rows(pages_):
    """(поле, отступ текста от колонки, текст) по всем страницам; пустая строка
    — ('', 0, '')."""
    result = []
    for page in pages_:
        lines = [line for line in page if not is_furniture(line)]
        column = page_column(lines)
        for line in lines:
            if not line.strip():
                result.append(("", 0, ""))
                continue
            margin, start, text = split(line, column)
            result.append(
                (margin, max(start - column, 0) if text else 0, text)
            )
    return result


def heading_at(rows_, index):
    """Заголовок на поле от строки index: куски поля до точки; ссылки на законы
    — не он."""
    parts = []
    for margin, _, text in rows_[index:]:
        if len(parts) == HEADING_LINES:
            break
        if not margin:
            if text or parts:
                # строка текста без поля: заголовок кончился (или его нет)
                if parts and not text:
                    continue
                break
            continue
        if CITATION_RE.match(margin):
            break
        parts.append(margin)
        if margin.endswith("."):
            break
    heading = gazette.HYPHEN_RE.sub(r"\1\2", " ".join(parts))
    return heading.rstrip(".").strip()


def consolidated_lines(path):
    """Строки закона из газеты (PDF) в раскладке сводки CyLaw, начиная с
    «Προοίμιο»."""
    return convert(pages(path))


def convert(pages_):
    """Страницы газеты (списки строк) → строки в раскладке сводки CyLaw."""
    all_rows = rows(pages_)
    start = next(
        (
            index
            for index, row in enumerate(all_rows)
            if row[0].startswith("Προοίμιο")
        ),
        None,
    )
    if start is None:
        raise ValueError("нет «Προοίμιο» на поле")
    lines = [" " * HEADING_INDENT + "Προοίμιο", ""]
    in_preamble = True
    for index in range(start, len(all_rows)):
        margin, offset, text = all_rows[index]
        if not text:
            if not margin:
                lines.append("")
            continue
        if ARTICLE_RE.match(text) and offset <= 8:
            in_preamble = False
            heading = heading_at(all_rows, index)
            lines.append("")
            if heading:
                lines.append(" " * HEADING_INDENT + heading)
                lines.append("")
            lines.append(" " * ARTICLE_INDENT + text)
            continue
        if in_preamble:
            # «Η Βουλή των Αντιπροσώπων ψηφίζει…» с красной строки — абзац
            # преамбулы
            offset = 0
        lines.append(" " * min(TEXT_INDENT + offset, GROUP_INDENT - 1) + text)
    return lines


def write_lines(path, target):
    Path(target).write_text(
        "\n".join(consolidated_lines(path)) + "\n", encoding="utf-8"
    )
