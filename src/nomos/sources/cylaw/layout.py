"""Текст PDF через pdftotext -layout: строки, блоки, очистка."""

import html
import re
import subprocess
from dataclasses import dataclass


@dataclass
class Block:
    """Группа непустых строк между пустыми: абзац или заголовок."""

    line: int
    indent: int
    text: str


def pdf_lines(path):
    """Строки PDF с сохранённой раскладкой (poppler pdftotext)."""
    result = subprocess.run(
        ['pdftotext', '-layout', str(path), '-'],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.replace('\f', '') for line in result.stdout.split('\n')]


ITEM_START_RE = re.compile(r'^\s*(\(\w{1,5}\)|\d+[^\W\d_]{0,3}\s?\.\s)')


def blocks(lines, skip=None):
    """Блоки из строк; `skip(line)` — строки колонтитулов, которые выбрасываются.

    После колонтитула блок продолжается (абзац перенесён на страницу), если следующая
    строка не начинает нумерованный пункт."""
    found = []
    current = []
    start = 0
    after_skip = False
    for number, line in enumerate(lines, 1):
        if skip and skip(line):
            after_skip = True
            continue
        if after_skip and current and starts_block(line, current[-1]):
            found.append(make_block(start, current))
            current = []
        after_skip = False
        if current and ITEM_START_RE.match(line) and ends_sentence(current[-1]):
            found.append(make_block(start, current))
            current = []
        if not line.strip():
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


TERM_START_RE = re.compile(r'^\s*("|\'\'|«|“)')


def starts_block(line, previous):
    """После колонтитула новый блок — нумерованный пункт или новое определение
    («"φόρος εκροών" …» после законченного «… άρθρο 19˙»)."""
    if ITEM_START_RE.match(line):
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
