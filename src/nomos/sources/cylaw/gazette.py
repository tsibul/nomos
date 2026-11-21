"""Изменяющий акт из Επίσημη Εφημερίδα: поля (маргиналии) и тело, статьи и их пункты.

Слева — колонка маргиналий (заголовок статьи), справа — текст. Цитаты «…» — вставляемый
или заменяющий текст; внутри цитаты нумерация не разбирается (это пункты другого акта).
"""

import re

from . import layout
from .tree import Node

# дата в поле, бывает с пробелом вместо точки: «7.12.2007», «6.10 2017.»
DATE_RE = re.compile(r'^\s*\d{1,2}\.\d{1,2}[.\s]\s*\d{4}\.?\s*$')
LABEL_RE = re.compile(r'^(\(\w{1,5}\)|\d+\.)$')
GAP_RE = re.compile(r'\S(\s{3,})\S')
FIRST_ARTICLE_RE = re.compile(r'(?<!\S)(?P<num>[12])\.\s*(Ο|Η|Το|Οι)\s+παρ')
ARTICLE_RE = re.compile(r'^(?P<num>\d+)\.\s*(?:[-—–]\s*)?(?P<rest>.*)$')
POINT_RE = re.compile(r'^\((?P<num>[α-ω]{1,3})\)\s*(?P<rest>.*)$')
# строка поля из одного номера закона: «82(I) του 2020», «25(Ι) του 2022.»
LAW_NUMBER_RE = re.compile(r'^\s*\d+\s*\([ΙI]\)\s+του\s+\d{4}\.?\s*$')
SUBSECTION_RE = re.compile(r'^\(\d+\)\s')
ITEM_RE = re.compile(r'^(«\s*)?(\(\w{1,5}\)|\d+[Α-Ω]{0,3}\.)')
END_RE = re.compile(r'^\s*(_{5,}|[TΤ]υπώθηκε)')
HEADER_RE = re.compile(r'^\s*(Ε\.Ε\.\s*Παρ\.|Αρ\.\s*\d+,|Αριθμός\s+\d+|\d{3,5}\s*$)')
# перенос слова в конце строки: «Τροπο- ποιητικός» → «Τροποποιητικός»
HYPHEN_RE = re.compile(r'([α-ρσ-ωάέήίόύώϊϋΐΰ])- ([α-ωάέήίόύώϊϋΐΰ])')
# «… των εδαφίων (4) και» — перечень номеров продолжается на следующей строке
LABEL_AND_RE = re.compile(r'\(\w{1,5}\)\s+(και|ή)$')
INNER_GAP_RE = re.compile(r'\S(\s{4,})\S')
# газеты, по которым собраны тексты до правил поля внутри цитаты, первого пункта (α),
# цитаты после «:» и перечня «(4) και»: разбираются прежними правилами, их файлы не меняются
LEGACY_GAZETTES = {'cy/law/2025/104_I', 'cy/kdp/2026/168'}
TITLE_RE = re.compile(r'ως (?:ο|η|το|οι) (περί .+? (?:του|των) \d{4})')


def split_line(line, column):
    """Строка → (поле, текст). Короткая строка без разрыва левее текста — поле."""
    if not line.strip():
        return '', ''
    if DATE_RE.match(line) or LAW_NUMBER_RE.match(line):
        return line.strip(), ''
    match = GAP_RE.search(line)
    if match and match.start(1) < column:
        left = line[:match.start(1) + 1].strip()
        right = line[match.end(1) - 1:].strip()
        if LABEL_RE.match(left):
            # «(γ)   με την …» — номер пункта, отбитый пробелами, а не поле
            return '', left + ' ' + right
        return left, right
    indent = len(line) - len(line.lstrip())
    # короткий хвост цитаты («αναλογιών.»·») — текст: в полях кавычек «» нет
    quoted = '«' in line or '»' in line
    if indent < column - 3 and len(line.rstrip()) < column and not quoted:
        return line.strip(), ''
    return '', line.strip()


def text_start(line):
    """Начало текста строки: после поля внутри цитаты, если оно есть, иначе отступ."""
    indent = len(line) - len(line.lstrip())
    match = INNER_GAP_RE.search(line, indent)
    if match and not LABEL_RE.match(line[indent:match.start(1) + 1].strip()):
        return match.end(1) - 1
    return indent


def neighbour(lines, index, step):
    index += step
    while 0 <= index < len(lines):
        if lines[index].strip():
            return lines[index]
        index += step
    return ''


def inner_margin(lines, index, column):
    """Поле внутри цитаты: левее текста соседних строк, отбито пробелами.

    В цитате с отступом ссылки на законы и Επίσημη Εφημερίδα стоят своей колонкой
    («216(Ι) του 2022    (α)   γονική άδεια …»). Возвращает текст без поля или None."""
    line = lines[index]
    indent = len(line) - len(line.lstrip())
    match = INNER_GAP_RE.search(line, indent)
    if indent < column - 3 and (match is None or match.end(1) - 1 < column):
        # поле левее колонки текста, заходящее за неё («15(Ι) του 2024.      άδεια …»)
        return None
    around = [text_start(neighbour(lines, index, step)) for step in (-1, 1)]
    if match is None:
        # строка поля целиком: «Τρίτο (Ι):»
        short = len(line.strip()) <= 30 and '«' not in line and '»' not in line
        if short and indent < around[0] - 8:
            return ''
        return None
    left = line[indent:match.start(1) + 1].strip()
    start = match.end(1) - 1
    if LABEL_RE.match(left) or len(left) > 30 or '«' in left or '»' in left:
        return None
    if start - indent < 8 or not any(abs(start - other) <= 2 for other in around):
        return None
    return line[start:].strip()


def body_column(lines):
    for line in lines:
        match = FIRST_ARTICLE_RE.search(line)
        if match:
            # 102/2026 и 103/2026 нумеруют статьи с 2: номер берётся как в акте
            return match.start(), int(match.group('num'))
    raise ValueError('не найдена статья 1')


class GazetteParser:
    def __init__(self, act_uid):
        self.act_uid = act_uid
        self.legacy = act_uid in LEGACY_GAZETTES
        self.root = Node(kind='act')
        self.preamble = self.root.add(Node(kind='cit', token='cit_1'))
        self.article = None
        self.point = None
        self.heading_open = False
        self.quote_depth = 0
        self.paragraph = []
        self.last_text = ''
        self.expected = 1

    def parse(self, lines):
        column, self.expected = body_column(lines)
        for index, line in enumerate(lines):
            if END_RE.match(line) and self.article is not None:
                break
            if HEADER_RE.match(line):
                continue
            margin, body = split_line(line, column)
            # в преамбуле (шапка газеты до статьи 1) полей внутри цитат нет
            if body and not margin and self.article is not None and not self.legacy:
                inner = inner_margin(lines, index, column)
                if inner is not None:
                    # поле внутри цитаты — не заголовок статьи, в текст не идёт
                    body = inner
            self.feed(margin, body)
        self.flush()
        return self.root

    def feed(self, margin, body):
        if body and self.quote_depth == 0:
            match = ARTICLE_RE.match(body)
            if match and match.group('num') == str(self.expected):
                self.open_article(match.group('num'), margin)
                body = match.group('rest')
                margin = ''
            point = POINT_RE.match(body) if self.article else None
            # первый пункт статьи — (α): «(β)» из цитаты без (α) — не пункт
            first = (
                not self.legacy and self.point is None and point and point.group('num') != 'α'
            )
            if point and not first and self.ends_sentence():
                self.open_point(point.group('num'))
                body = point.group('rest')
        if margin and self.heading_open:
            self.add_heading(margin)
        if not body:
            return
        # «(2) …» вне цитаты после конца предложения — новое εδάφιο, новый абзац
        if self.paragraph and self.quote_depth == 0 and SUBSECTION_RE.match(body):
            if self.ends_sentence():
                self.flush()
        if self.paragraph and self.quote_depth > 0 and ITEM_RE.match(body):
            if self.ends_sentence():
                self.flush()
        opens = (
            not self.legacy
            and self.quote_depth == 0
            and body.startswith('«')
            and self.after_colon()
        )
        self.paragraph.append(body)
        self.quote_depth += body.count('«') - body.count('»')
        if opens and self.quote_depth == 0 and not body.rstrip().endswith('».'):
            # «γονική άδεια» σημαίνει- после «ορισμό:» — кавычка цитаты совпала с
            # кавычкой термина; цитата закрывается позже
            self.quote_depth = 1
        if self.quote_depth < 0:
            self.quote_depth = 0
        if self.quote_depth == 0 and body.endswith(('».', '»:', '»·', '»\u0387', '»;', '»∙')):
            self.flush()

    def after_colon(self):
        last = self.paragraph[-1] if self.paragraph else self.last_text
        return last.rstrip().endswith(':')

    def ends_sentence(self):
        if not self.paragraph:
            return True
        # «και» на своей строке; ano teleia (U+0387) — как «·»
        last = ' ' + self.paragraph[-1].rstrip().replace('\u0387', '·')
        if not self.legacy and LABEL_AND_RE.search(last):
            return False
        return layout.ends_sentence(last)

    def open_article(self, num, margin):
        self.flush()
        self.article = self.root.add(Node(kind='art', num=num))
        self.point = None
        self.expected += 1
        self.heading_open = True
        if margin:
            self.add_heading(margin)

    def add_heading(self, margin):
        heading = self.article.heading
        self.article.heading = (heading + ' ' + margin).strip()
        if self.article.heading.endswith('.'):
            self.heading_open = False

    def open_point(self, num):
        self.flush()
        self.point = self.article.add(Node(kind='pnt', num='(%s)' % num))

    def owner(self):
        if self.point is not None:
            return self.point
        if self.article is not None:
            return self.article
        return self.preamble

    def flush(self):
        if not self.paragraph:
            return
        text = HYPHEN_RE.sub(r'\1\2', layout.join_lines(self.paragraph))
        self.owner().paragraphs.append(text)
        self.last_text = text
        self.paragraph = []


def parse(path, act_uid):
    """PDF изменяющего акта → (дерево, краткий заголовок из ст. 1)."""
    parser = GazetteParser(act_uid)
    root = parser.parse(layout.pdf_lines(path))
    title = ''
    articles = [node for node in root.children if node.kind == 'art']
    if articles:
        match = TITLE_RE.search(articles[0].text())
        if match:
            title = match.group(1)
    return root, title


QUOTE_RE = re.compile(r'«([^«»]*)»')


def quote_markup(markup):
    """«…» → «<q>…</q>» в уже экранированном тексте."""
    return QUOTE_RE.sub(lambda match: '«<q>%s</q>»' % match.group(1), markup)
