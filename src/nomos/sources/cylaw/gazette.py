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
# «(2) …», «(5)(α) …» — εδάφιο, сразу за номером может идти номер пункта (53(Ι)/2025)
SUBSECTION_RE = re.compile(r'^\(\d+\)(\s|\()')
ITEM_RE = re.compile(r'^(«\s*)?(\(\w{1,5}\)|\d+[Α-Ω]{0,3}\.)')
END_RE = re.compile(r'^\s*(_{5,}|[TΤ]υπώθηκε)')
HEADER_RE = re.compile(r'^\s*(Ε\.Ε\.\s*Παρ\.|Αρ\.\s*\d+,|Αριθμός\s+\d+|\d{3,5}\s*$)')
# шапка первой страницы выпуска: номер акта справа вверху, название газеты, приложение, часть
MASTHEAD_RE = re.compile(
    r'^\s*(N\.\s*\d+\s*\([ΙI]\)/\d{4}'
    r'|Κ\.Δ\.Π\.\s*\d+/\d{4}'
    r'|ΕΠΙΣΗΜΗ ΕΦΗΜΕΡΙΔΑ'
    r'|ΤΗΣ ΚΥΠΡΙΑΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ'
    r'|ΠΑΡΑΡΤΗΜΑ [Α-Ω]+'
    r'|(ΝΟΜΟΘΕΣΙΑ\s*-\s*)?ΜΕΡΟΣ [ΙI]+'
    r'|ΚΑΝΟΝΙΣΤΙΚΕΣ ΔΙΟΙΚΗΤΙΚΕΣ ΠΡΑΞΕΙΣ)\s*$'
)
# перенос слова в конце строки: «Τροπο- ποιητικός» → «Τροποποιητικός»
HYPHEN_RE = re.compile(r'([α-ρσ-ωάέήίόύώϊϋΐΰ])- ([α-ωάέήίόύώϊϋΐΰ])')
# «… των εδαφίων (4) και» — перечень номеров продолжается на следующей строке
LABEL_AND_RE = re.compile(r'\(\w{1,5}\)\s+(και|ή)$')
INNER_GAP_RE = re.compile(r'\S(\s{4,})\S')
# газеты, по которым собраны тексты до правил поля внутри цитаты, первого пункта (α),
# цитаты после «:» и перечня «(4) και»: разбираются прежними правилами, их файлы не меняются
LEGACY_GAZETTES = {'cy/law/2025/104_I', 'cy/kdp/2026/168'}
# цитата закрыта в конце строки: «…του∙», «…ορισμού.»ꞏ και»
QUOTE_CLOSED_RE = re.compile(r'»[.:;·∙\u0387\ua78f]?(\s+(και|ή))?$')
# знак конца фразы перед закрывающей кавычкой: «…εκπρόσωπό του∙»
PUNCT_QUOTE_RE = re.compile(r'([.:;·∙])»$')
# поле слилось с текстом (48(Ι)/2025): «Τροποποίηση 2. Το άρθρο 2 … αλφαβητική του άρθρου»,
# «2 σειρά, … τους: του βασικού νόμου.» — остаток заголовка в конце строк и номер в начале второй
COLLAPSED_FIRST_RE = re.compile(
    r'^(?P<margin>[Α-ΩΆ-Ώ][α-ωά-ώ]+) (?P<body>(?P<num>\d+)\.\s*(Ο|Η|Το|Οι)\s.*?)'
    r'\s(?P<tail>του άρθρου)$'
)
COLLAPSED_SECOND_RE = re.compile(
    r'^(?P<num>\d+[Α-Ω]{0,2}) (?P<body>.*?) (?P<tail>του βασικού νόμου\.)$'
)
# первая строка вставленной статьи: «Заголовок на поле    7. Текст» / «… 4ΑΑ.-(1) Текст»
QUOTED_HEADING_RE = re.compile(r'^«[Α-ΩΆ-Ώ][^«»()]{1,29}$')
QUOTED_ARTICLE_RE = re.compile(r'^\d+[Α-Ω]{0,3}\.(?:-\(\d+\))?\s')
NARROW_GAP_RE = re.compile(r' {2,}(?=\S)')
# номер закона на поле внутри цитаты: строкой целиком или перед текстом через 3+ пробела
LAW_CITATION = r'^\s*\d+\s*(?:\([ΙI]\))?\s+του\s*\d{4}\.?'
LAW_CITATION_RE = re.compile(LAW_CITATION + r'\s*$')
LAW_CITATION_PREFIX_RE = re.compile(LAW_CITATION + r'(?=\s{3,}\S)')
TITLE_RE = re.compile(r'ως (?:ο|η|το|οι) (περί .+? (?:του|των) \d{4})')


def split_line(line, column, previous=''):
    """Строка → (поле, текст). Короткая строка без разрыва левее текста — поле.

    `previous` — предыдущая непустая строка: короткая строка, продолжающая её фразу, — текст
    (см. continues_text)."""
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
        if continues_text(previous, indent):
            return '', line.strip()
        return line.strip(), ''
    return '', line.strip()


def continues_text(previous, indent):
    """Строка продолжает фразу строки выше: на странице без полей текст начинается с края
    («… που εφαρμόζει την εκεί προβλε-» / «πόμενη επιλογή.», 104(Ι)/2025, Πρώτο Παράρτημα).

    Строка выше — длинная строка текста без поля (без разрыва), с тем же отступом, фраза в ней
    не закончена."""
    if not previous.strip() or GAP_RE.search(previous.strip()):
        return False
    above = len(previous) - len(previous.lstrip())
    text = previous.strip()
    return abs(above - indent) <= 3 and len(text) >= 40 and text[-1] not in '.:;·˙'


def restore_collapsed_margin(lines, column):
    """Строки со слившимся полем → поле до колонки текста и текст, как в обычной раскладке."""
    restored = []
    index = 0
    while index < len(lines):
        first = COLLAPSED_FIRST_RE.match(lines[index])
        second = None
        if first and index + 1 < len(lines):
            second = COLLAPSED_SECOND_RE.match(lines[index + 1])
        if first is None or second is None or not lines[index + 1].startswith(first.group('num')):
            restored.append(lines[index])
            index += 1
            continue
        width = max(column, len(first.group('margin')) + 4)
        restored.append(first.group('margin').ljust(width) + first.group('body'))
        margin = first.group('tail') + ' ' + second.group('num')
        restored.append(margin.ljust(max(width, len(margin) + 4)) + second.group('body'))
        restored.append(second.group('tail'))
        index += 2
    return restored


def text_start(line):
    """Начало текста строки: после поля внутри цитаты, если оно есть, иначе отступ."""
    indent = len(line) - len(line.lstrip())
    match = INNER_GAP_RE.search(line, indent)
    if match is None:
        return indent
    left = line[indent:match.start(1) + 1].strip()
    # «(δ)   χρηματική   επιβάρυνση     για» — пробелы выключки после номера пункта, не поле
    if LABEL_RE.match(left) or ITEM_RE.match(left):
        return indent
    return match.end(1) - 1


def neighbour(lines, index, step):
    index += step
    while 0 <= index < len(lines):
        if lines[index].strip():
            return lines[index]
        index += step
    return ''


def margin_run_start(lines, index):
    """Начало текста над столбцом строк поля: «Ιατροτεχνολογικών», «Προϊόντων και» под
    строкой «Συμβούλιο      εσωτερικούς κανονισμούς.» — сравнение с первой строкой не из поля."""
    line = lines[index]
    indent = len(line) - len(line.lstrip())
    step = index - 1
    while step >= 0:
        above = lines[step]
        if above.strip():
            above_indent = len(above) - len(above.lstrip())
            whole = INNER_GAP_RE.search(above, above_indent) is None
            if not (whole and len(above.strip()) <= 30 and abs(above_indent - indent) <= 3):
                return text_start(above)
        step -= 1
    return indent


def inner_margin(lines, index, column):
    """Поле внутри цитаты: левее текста соседних строк, отбито пробелами.

    В цитате с отступом ссылки на законы и Επίσημη Εφημερίδα стоят своей колонкой
    («216(Ι) του 2022    (α)   γονική άδεια …»). Возвращает текст без поля или None."""
    line = lines[index]
    if LAW_CITATION_RE.match(line):
        # «22 του 1985» — номер закона на поле, и без «(Ι)»
        return ''
    cited = LAW_CITATION_PREFIX_RE.match(line)
    if cited:
        # «208(Ι) του 2015.   Νόμου…», «96(Ι) του 2018.     (β) …» — при любом выравнивании
        return line[cited.end():].strip()
    indent = len(line) - len(line.lstrip())
    match = INNER_GAP_RE.search(line, indent)
    if match is None:
        # строка поля целиком: «Τρίτο (Ι):»; столбец поля цитаты бывает и левее колонки
        # текста акта (60(Ι)/2026, ст. 33Γ: поле на 19, колонка 24, текст цитаты на 42)
        run_start = margin_run_start(lines, index)
        if indent < run_start - 8:
            for gap in NARROW_GAP_RE.finditer(line, indent):
                left = line[indent:gap.start()]
                if LABEL_RE.match(left) or ITEM_RE.match(left):
                    # «(ii)   στην περίπτωση …» — номер пункта, не поле
                    return None
                if abs(gap.end() - run_start) <= 2:
                    # «και σφράγιση των   επιχείρηση-» — узкий разрыв, текст по колонке выше
                    return line[gap.end():].strip()
        short = len(line.strip()) <= 30 and '«' not in line and '»' not in line
        if short and indent < run_start - 8:
            return ''
    if indent < column - 3 and (match is None or match.end(1) - 1 < column):
        # поле левее колонки текста, заходящее за неё («15(Ι) του 2024.      άδεια …»)
        return None
    around = [text_start(neighbour(lines, index, step)) for step in (-1, 1)]
    if match is None:
        return None
    left = line[indent:match.start(1) + 1].strip()
    start = match.end(1) - 1
    if QUOTED_HEADING_RE.match(left) and QUOTED_ARTICLE_RE.match(line[start:].lstrip()):
        # «Συλλογή και    4ΑΑ.-(1) Η συλλογή…» — заголовок вставленной статьи на поле
        return '«' + line[start:].strip()
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
        if not self.legacy:
            lines = restore_collapsed_margin(lines, column)
        previous = ''
        for index, line in enumerate(lines):
            if END_RE.match(line) and self.article is not None:
                break
            if HEADER_RE.match(line):
                continue
            if self.article is None and MASTHEAD_RE.match(line):
                continue
            margin, body = split_line(line, column, previous)
            if line.strip():
                previous = line
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
        if body and self.quote_depth > 0 and self.unclosed_quote(margin, body):
            # кавычка не закрыта в самой газете (242(Ι)/2025, ст. 3: «…(€450.000)·»)
            self.quote_depth = 0
        if body and self.quote_depth == 0:
            match = ARTICLE_RE.match(body)
            if match and self.is_next_article(int(match.group('num')), margin):
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
        closed = QUOTE_CLOSED_RE.search(body.rstrip())
        if opens and self.quote_depth == 0 and not closed:
            # «γονική άδεια» σημαίνει- после «ορισμό:» — кавычка цитаты совпала с
            # кавычкой термина; цитата закрывается позже
            self.quote_depth = 1
        if self.quote_depth < 0:
            self.quote_depth = 0
        if self.quote_depth == 0 and body.endswith(
            ('».', '»:', '»·', '»\u0387', '»\ua78f', '»;', '»∙')
        ):
            self.flush()

    def unclosed_quote(self, margin, body):
        """Внутри «цитаты» начинается следующая статья самого акта: номер статьи по порядку,
        заголовок на поле без кавычки, прежняя фраза закончена."""
        if self.legacy or self.article is None:
            return False
        heading = margin.strip()
        if not heading or heading.startswith('«'):
            return False
        match = ARTICLE_RE.match(body)
        if not match or int(match.group('num')) != self.expected:
            return False
        return self.ends_sentence()

    def is_next_article(self, num, margin):
        """Следующая статья; закон может пропустить номер (244(Ι)/2025: за 13 идёт 16) —
        тогда номер не дальше двух вперёд и у статьи заголовок на поле."""
        if num == self.expected:
            return True
        skipped = self.expected < num <= self.expected + 2
        return skipped and not self.legacy and bool(margin.strip())

    def after_colon(self):
        last = self.paragraph[-1] if self.paragraph else self.last_text
        return last.rstrip().endswith(':')

    def ends_sentence(self):
        if not self.paragraph:
            return True
        if self.legacy:
            return layout.ends_sentence(' ' + self.paragraph[-1].rstrip().replace('\u0387', '·'))
        # «και» на своей строке; ano teleia (U+0387) и «ꞏ» (U+A78F) — как «·»
        last = self.paragraph[-1].rstrip().replace('\u0387', '·').replace('\ua78f', '·')
        last = ' ' + PUNCT_QUOTE_RE.sub(r'\1', last)
        if LABEL_AND_RE.search(last):
            return False
        return layout.ends_sentence(last)

    def open_article(self, num, margin):
        self.flush()
        self.article = self.root.add(Node(kind='art', num=num))
        self.point = None
        self.expected = int(num) + 1
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
