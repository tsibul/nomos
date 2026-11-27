"""Текст PDF через pdftotext -layout: строки, блоки, очистка."""

import html
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Block:
    """Группа непустых строк между пустыми: абзац или заголовок."""

    line: int
    indent: int
    text: str


def pdf_lines(path):
    """Строки PDF с сохранённой раскладкой (poppler pdftotext).

    Скан без текстового слоя заменяется выверенной расшифровкой `.txt` в той же
    раскладке: поля слева, текст с отступом. Первый блок расшифровки (до пустой строки) —
    служебная пометка («Расшифровка скана …»), не текст акта: её строки пустые, номера
    строк не сдвигаются.
    """
    if Path(path).suffix == '.txt':
        return drop_header(Path(path).read_text(encoding='utf-8').split('\n'))
    result = subprocess.run(
        ['pdftotext', '-layout', str(path), '-'],
        check=True,
        capture_output=True,
        text=True,
    )
    return [fix_lookalikes(line.replace('\f', '')) for line in result.stdout.split('\n')]


def drop_header(lines):
    """Строки служебной пометки в начале расшифровки → пустые."""
    if not lines or not lines[0].startswith('Расшифровка'):
        return lines
    end = next((index for index, line in enumerate(lines) if not line.strip()), len(lines))
    return [''] * end + lines[end:]


# латинские буквы, набранные в греческом тексте вместо греческих (CyLaw: «αvαφέρovται»)
LOOKALIKES = str.maketrans(
    'ABEHIKMNOPTXYZoveiku',
    'ΑΒΕΗΙΚΜΝΟΡΤΧΥΖονεικυ',
)
LOWER_LOOKALIKES = set('oveiku')
WORD_RE = re.compile(r'[^\W\d_]+')
GREEK_RE = re.compile(r'[\u0370-\u03ff\u1f00-\u1fff]')
LATIN_RE = re.compile(r'[A-Za-z]')
ROMAN_RE = re.compile(r'^([IVX]+|[ivx]+)$')


def fix_lookalikes(line):
    """Латинские двойники греческих букв → греческие: в словах из обоих алфавитов
    («Iαvoυαρίoυ») и в латинских словах из одних двойников со строчной буквой рядом с
    греческим словом («Ev τω», «o Διευθυντής»). Римские цифры и прочая латиница — как есть."""
    words = list(WORD_RE.finditer(line))
    if not words or not GREEK_RE.search(line):
        return line
    parts = []
    last = 0
    for index, match in enumerate(words):
        word = match.group()
        if LATIN_RE.search(word) and is_lookalike(word, words, index):
            parts.append(line[last:match.start()])
            parts.append(word.translate(LOOKALIKES))
            last = match.end()
    parts.append(line[last:])
    return ''.join(parts)


def is_lookalike(word, words, index):
    latin = [letter for letter in word if LATIN_RE.match(letter)]
    if any(letter.translate(LOOKALIKES) == letter for letter in latin):
        return False
    if ROMAN_RE.match(word.replace('ν', 'v')):
        # «(νii)», «(iν)»: римская цифра с греческой ν — номер пункта, разбирает numbering
        return False
    if GREEK_RE.search(word):
        return True
    if not LOWER_LOOKALIKES.intersection(word):
        return False
    neighbours = words[max(index - 1, 0):index] + words[index + 1:index + 2]
    return any(GREEK_RE.search(neighbour.group()) for neighbour in neighbours)


ITEM_START_RE = re.compile(r'^\s*(\(\w{1,5}\)|\d+[^\W\d_]{0,4}\s?\.\s)')
# «39Α.-(1) …» — тире сразу за точкой (118(Ι)/2002); «115-(1)» — без точки
# (Κεφ. 113)
DASHED_START_RE = re.compile(
    r'^\s*\d+[^\W\d_]{0,4}\s?(?:\.[-—–―]|[-—–―]\()'
)


def blocks(lines, skip=None, dashed=True):
    """Блоки из строк; `skip(line)` — строки колонтитулов, которые выбрасываются.

    После колонтитула блок продолжается (абзац перенесён на страницу), если следующая
    строка не начинает нумерованный пункт. Пустая строка перед колонтитулом посреди фразы
    («… ψηφισθέντος είτε προ» / «είτε κατόπιν …») блок не закрывает.
    `dashed=False` — «39Α.-(1)» пункта не начинает (акты, собранные до 118(Ι)/2002)."""
    found = []
    current = []
    start = 0
    after_skip = False
    for number, line in enumerate(lines, 1):
        if skip and skip(line):
            after_skip = True
            continue
        if after_skip and current and starts_block(line, current[-1], dashed):
            found.append(make_block(start, current))
            current = []
        after_skip = False
        if current and starts_item(line, dashed) and ends_sentence(current[-1]):
            found.append(make_block(start, current))
            current = []
        if not line.strip():
            if (
                current
                and skip
                and continues_after_page(lines[number:], current[-1], skip)
            ):
                continue
            if current:
                found.append(make_block(start, current))
                current = []
            continue
        if not current:
            start = number
        current.append(line)
    if current:
        found.append(make_block(start, current))
    return found


def continues_after_page(rest, previous, skip):
    """За пустой строкой — колонтитул, а после него фраза продолжается со строчной буквы."""
    if ends_sentence(previous):
        return False
    following = [line for line in rest if line.strip()]
    if not following or not skip(following[0]):
        return False
    text = next((line.strip() for line in following if not skip(line)), '')
    return text[:1].islower()


TERM_START_RE = re.compile(r'^\s*("|\'\'|«|“)')


def starts_item(line, dashed=True):
    return bool(ITEM_START_RE.match(line) or (dashed and DASHED_START_RE.match(line)))


def starts_block(line, previous, dashed=True):
    """После колонтитула новый блок — нумерованный пункт или новое определение
    («"φόρος εκροών" …» после законченного «… άρθρο 19˙»)."""
    if starts_item(line, dashed):
        return True
    return bool(TERM_START_RE.match(line)) and ends_sentence(previous)


SENTENCE_END_RE = re.compile(r'([.;:˙·∙\]—–-]|\sή|\sκαι)$')


def ends_sentence(line):
    """Пункт без пустой строки перед ним начинается после законченной фразы."""
    return bool(SENTENCE_END_RE.search(line.rstrip()))


def make_block(start, lines):
    indent = len(lines[0]) - len(lines[0].lstrip(' '))
    return Block(start, indent, join_lines(lines))


def join_lines(lines):
    return re.sub(r'\s+', ' ', ' '.join(line.strip() for line in lines)).strip()


def escape(text):
    return html.escape(text, quote=False)


NOTE_RE = re.compile(r'\[\s*Σ\.\s*Σ\.\s*:?\s*(.*?)\]', re.S)


def inline_markup(text):
    """Экранирование и разметка внутри абзаца: [Σ.Σ.: …] → <note>."""
    parts = []
    position = 0
    for match in NOTE_RE.finditer(text):
        parts.append(escape(text[position:match.start()]))
        parts.append(
            '<note kind="source_comment">%s</note>' % escape(match.group(1).strip())
        )
        position = match.end()
    parts.append(escape(text[position:]))
    return ''.join(parts)


def strip_notes(text, found):
    """Экранирование абзаца без примечаний редакции CyLaw [Σ.Σ.: …]: их текст — в found.

    Остальной текст не меняется; пробел на месте примечания — только между словами."""
    parts = []
    position = 0
    for match in NOTE_RE.finditer(text):
        found.append(' '.join(match.group(1).split()))
        before = text[position:match.start()].rstrip()
        after = text[match.end():]
        parts.append(before)
        rest = after.lstrip()
        if before and rest and rest[0] not in ',.;:·)':
            parts.append(' ')
        position = match.end() + len(after) - len(rest)
    parts.append(text[position:])
    return escape(''.join(parts))
