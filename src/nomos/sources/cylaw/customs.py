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
# номер статьи — с буквой или с двумя буквами-числом: 18ΣΤ, 18ΙΑ (91(Ι)/2004)
NUMBER = r'\d+(?:ΣΤ|Ι[Α-Θ]|[Α-Ω])?'
ARTICLE_NUMBER_RE = re.compile(rf'^{NUMBER}\.$')
# номер страницы — отдельной строкой
FURNITURE_RE = re.compile(r'^\d+$')
FIRST_ARTICLE_RE = re.compile(r'^1\.\s')
ARTICLE_RE = re.compile(rf'^({NUMBER})\.')
# знак препинания отдельным словом и насколько он может отстоять от слова перед ним, pt
PUNCTUATION_RE = re.compile(r'^[.,·;:ꞏ∙]$')
PUNCTUATION_DX = 3
PUNCTUATION_DY = 10
# союз в конце строки: фраза продолжается на следующей («στα άρθρα 349 και» / «355»)
CONNECTORS = ('και', 'ή')
# число отдельной строкой сразу под строкой, кончающейся союзом, — текст, не номер
# страницы; насколько ниже, pt (шаг строк около 12,6)
NUMBER_DY = 15
NUMBER_RE = re.compile(r'^\d+$')
# строка кончается союзом и числом, дописанным attach_numbers («349 και 355»): просвет
# под ней — от снятого числа, следующая строка продолжает фразу
DANGLING_NUMBER_RE = re.compile(r'\s(?:και|ή)\s\d+$')
# перечень номеров через перенос строки: «σύμφωνα με τα εδάφια (1) και» / «(2) του άρθρου
# 11Β» — «(2)» не начинает пункт
ENUMERATION_RE = re.compile(r'\(\d+\)\s(?:και|ή)$')
ENUMERATED_RE = re.compile(r'^\(\d+\)\s')
PART_RE = re.compile(r'^ΜΕΡΟΣ\s')
# заголовок Μέρους или Κεφαλαίου, к которому присоединяется название заглавными
HEADING_START_RE = re.compile(r'^(ΜΕΡΟΣ|ΚΕΦΑΛΑΙΟ)\s')
# строка оглавления: «1.     Συνοπτικός τίτλος» или «1     Συνοπτικός τίτλος»
TOC_RE = re.compile(rf'^({NUMBER})\.?\s+(\S.*)$')
# «ΠΡΩΤΟ ΠΑΡΑΡΤΗΜΑ» в оглавлении и по центру — начало приложений
ORDINAL_SCHEDULE_RE = re.compile(r'^[Α-Ω]+ ΠΑΡΑΡΤΗΜΑ$')
# таблица приложения «Προϊόντα | Συντελεστής …»: ставка — «€429,00», «0%», «Ατελώς»;
# просвет перед ставкой, pt, и насколько ставка после просвета бывает левее столбца, pt
RATE_HEADER = 'Συντελεστής'
RATE_RE = re.compile(r'^(€|\d+(,\d+)?%|Ατελώς)')
RATE_GAP = 8
RATE_REACH = 30
# оговорка в столбце товаров («Νοείται ότι, από την 8η Μαρτίου … ισχύει ο συντελεστής …»)
PROVISO_RE = re.compile(r'^Νοείται\s')
# метка строки ставки в раскладке: снимается в pdf_layout, номер строки уходит парсеру
RATE_MARK = ''
# цифра: ссылка на закон «N. 140(I)/2018» — не заголовок
DIGIT_RE = re.compile(r'\d')
# ссылка на изменяющий закон под ставкой
LAW_REF_RE = re.compile(r'^N\. \d+\(I\)/\d{4}$')
# пункт «(β) …» в столбце ставок
POINT_RE = re.compile(r'^\([α-ω]{1,2}\)\s')
# пункт приложения «1) Προϊόντα …» — как «1. Προϊόντα …»
ITEM_PAREN_RE = re.compile(r'^(\d+)\)\s')
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
# номер статьи бывает сдвинут вправо на столько знаков (18.(1) в 91(Ι)/2004)
ARTICLE_SHIFT = 3
# «N.» у края текста, которое не статья: отступ больше, чем у статьи (consolidated.TOP_RE)
NOT_ARTICLE_INDENT = 17
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
    """Оглавление → {номер статьи: заголовок} в порядке статей; первая запись номера.

    Заголовок, перенесённый на следующую строку без просвета, продолжается ею. Записи
    приложений («ΠΡΩΤΟ ΠΑΡΑΡΤΗΜΑ») кончают оглавление статей."""
    found = {}
    last = None
    for margin, body, _, gap in toc_rows:
        text = ' '.join(part for part in (margin, body) if part)
        match = TOC_RE.match(text)
        if ORDINAL_SCHEDULE_RE.match(text) or text == SCHEDULE_HEADING:
            break
        if match:
            last = match.group(1) if match.group(1) not in found else None
            found.setdefault(match.group(1), match.group(2))
        elif PART_RE.match(text) or text.isupper() or gap is not None:
            last = None
        elif last is not None and text:
            found[last] += ' ' + text
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


def attach_numbers(words):
    """Число отдельной строкой под строкой, кончающейся союзом («349 και» / «355»), — её
    продолжение: оно дописывается к союзу, иначе leginet.rows снял бы его как номер
    страницы."""
    fixed = list(words)
    for word in words:
        x0, y, x1, text = word
        if not NUMBER_RE.match(text):
            continue
        if any(other is not word and abs(other[1] - y) <= HEADING_SHIFT for other in words):
            continue
        above = [other for other in words if 0 < y - other[1] <= NUMBER_DY]
        if not above:
            continue
        line_y = max(other[1] for other in above)
        last = max(
            (other for other in above if abs(other[1] - line_y) <= HEADING_SHIFT),
            key=lambda other: other[2],
        )
        if last[3] not in CONNECTORS or last not in fixed:
            continue
        index = fixed.index(last)
        fixed[index] = (last[0], last[1], last[2], last[3] + ' ' + text)
        fixed.remove(word)
    return fixed


def join_dangling(all_rows):
    """Продолжение фразы, оборванной союзом, дописывается к её строке: после «349 και 355»
    (DANGLING_NUMBER_RE) — и через пустую строку, после «εδάφια (1) και» — строка «(2) …»
    без просвета, и «(2)» не начинает пункт. Строка с текстом на поле не дописывается: на
    ней начинается заголовок."""
    result = []
    for row in all_rows:
        margin, body, indent, gap = row
        previous = next((index for index in range(len(result) - 1, -1, -1)
                         if result[index][1]), None)
        before = result[previous][1] if previous is not None else ''
        dangling = gap in (None, '') and DANGLING_NUMBER_RE.search(before)
        enumerated = (
            gap is None and ENUMERATION_RE.search(before) and ENUMERATED_RE.match(body)
        )
        if body and not margin and (dangling or enumerated):
            before = result[previous]
            result[previous] = (before[0], before[1] + ' ' + body, before[2], before[3])
            continue
        result.append(row)
    return result


def mark_articles(all_rows, toc=None):
    """Статья «N.» у левого края текста начинает абзац, даже если просвет перед ней мал.

    Отступ статьи, сдвинутой на знак лишним пробелом («86.- (1)»), — отступ текста. С
    оглавлением `toc` статья — только номер из него, идущий в оглавлении позже предыдущей
    статьи; другие «N.» у края текста (код «8703.» в перечне) — текст с отступом больше."""
    order = list(toc or ())
    reach = ARTICLE_SHIFT if order else 1
    last = -1
    last_number = None
    marked = []
    for margin, body, indent, gap in all_rows:
        article = ARTICLE_RE.match(body)
        if abs(indent - TEXT_INDENT) <= reach and article:
            number = article.group(1)
            if not order or is_next_article(number, order, last, last_number):
                if number in order:
                    last = order.index(number)
                last_number = number
                indent = TEXT_INDENT
                if gap is None:
                    gap = ''
            else:
                indent = NOT_ARTICLE_INDENT
        marked.append((margin, body, indent, gap))
    return marked


def is_next_article(number, order, last, last_number):
    """Номер из оглавления позже предыдущей статьи или статья с буквой после статьи с тем
    же номером, которой в оглавлении нет (80Α после 80 в 91(Ι)/2004)."""
    if number in order:
        return order.index(number) > last
    digits = re.match(r'\d+', number).group()
    return (
        last_number is not None
        and number != digits
        and re.match(r'\d+', last_number).group() == digits
        and number > last_number
    )


def toc_article_headings(all_rows, toc):
    """Заголовки статей из оглавления: {номер строки статьи: заголовок}. На поле сводки
    акцизов заголовки без точек и вперемешку со ссылками на Επίσημη Εφημερίδα της Ε.Ε."""
    headings = {}
    for index, (_, body, indent, gap) in enumerate(all_rows):
        article = ARTICLE_RE.match(body)
        number = article.group(1) if article else None
        if number in toc and indent == TEXT_INDENT and gap is not None:
            headings[index] = toc[number].rstrip('.') + '.'
    return headings


def join_parts(lines):
    """«ΜΕΡΟΣ N» («ΚΕΦΑΛΑΙΟ Β») и название заглавными (по центру, через пустые строки) —
    одна строка, как в сводке CyLaw."""
    joined = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not HEADING_START_RE.match(line.strip()):
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
        and not HEADING_START_RE.match(text)
    )


def uncenter_text(lines):
    """Строка текста статьи правее центра (пункт перечня «– τσιγάρα 800 τεμάχια·», «λιμένα·
    και» после разрыва страницы в 91(Ι)/2004) — не заголовок: заголовок по центру начинается
    с заглавной («Ελεύθερες Ζώνες» в 94(Ι)/2004)."""
    return [
        ' ' * (CENTERED_INDENT - 1) + line.strip()
        if indent_of_line(line) >= CENTERED_INDENT and not line.strip()[0].isupper()
        else line
        for line in lines
    ]


def split_schedule(pages, columns):
    """Страницы до «ΠΑΡΑΡΤΗΜΑ» (или первого «ΠΡΩΤΟ ΠΑΡΑΡΤΗΜΑ») по центру и с него: у
    Παράρτημα нет поля, номера пунктов стоят левее края текста статей."""
    for index, page in enumerate(pages):
        for y, parts in leginet.page_lines(page, columns.line_tolerance):
            text = ' '.join(word for _, _, word in parts)
            if ORDINAL_SCHEDULE_RE.match(text) and parts[0][0] >= columns.center_left - 40:
                above = [word for word in page if word[1] < y - columns.line_tolerance]
                below = [word for word in page if word[1] >= y - columns.line_tolerance]
                return pages[:index] + [above], [below] + pages[index + 1:]
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


def split_schedules(pages, columns):
    """Страницы приложений → [[страницы одного приложения], …]: приложение «ΠΡΩΤΟ
    ΠΑΡΑΡΤΗΜΑ», «ΔΕΥΤΕΡΟ ΠΑΡΑΡΤΗΜΑ» … начинается со своей страницы."""
    groups = []
    for page in pages:
        starts = any(
            ORDINAL_SCHEDULE_RE.match(' '.join(word for _, _, word in parts))
            for _, parts in leginet.page_lines(page, columns.line_tolerance)
        )
        if starts or not groups:
            groups.append([])
        groups[-1].append(page)
    return groups


def rate_left(pages, columns):
    """Левый край столбца ставок таблицы приложения: самый частый левый край ставки
    («€429,00», «0%», «Ατελώς»), отделённой от слов перед ней просветом; None — таблицы нет."""
    lefts = Counter()
    started = False
    for page in pages:
        for _, parts in leginet.page_lines(page, columns.line_tolerance):
            started = started or any(RATE_HEADER in word for _, _, word in parts)
            if not started:
                continue
            for previous, (x0, _, word) in zip([None] + parts, parts):
                if RATE_RE.match(word) and (previous is None or x0 - previous[1] >= RATE_GAP):
                    lefts[round(x0)] += 1
    if not lefts:
        return None
    return lefts.most_common(1)[0][0] - 2


def split_rate(parts, left):
    """Слова строки таблицы → (столбец товаров, столбец ставок). Ставка начинается с
    первого слова у столбца ставок или со ставки после просвета чуть левее его (короткое
    «Ατελώς.» в 91(Ι)/2004, Τέταρτο Παράρτημα)."""
    text = ' '.join(word for _, _, word in parts)
    capitals = text.replace('(', '').replace(')', '').isupper()
    if capitals and (parts[0][0] < left or not DIGIT_RE.search(text)):
        # заголовок Μέρους таблицы по центру, и его вторая строка «(ΛΟΙΠΑ)»; ссылка
        # «N. 140(I)/2018» в столбце ставок — тоже заглавными, но у столбца и с цифрами
        return parts, []
    for index, (x0, _, word) in enumerate(parts):
        previous = parts[index - 1] if index else None
        after_gap = previous is None or x0 - previous[1] >= RATE_GAP
        if x0 >= left and not after_gap:
            # без просвета у столбца ставок строка идёт во всю ширину: текст, не ставка
            # (оговорка «Νοείται …» Τέταρτο Παράρτημα, примечания под Πρώτο Παράρτημα)
            return parts, []
        if x0 >= left or (RATE_RE.match(word) and after_gap and x0 >= left - RATE_REACH):
            return parts[:index], parts[index:]
    return parts, []


def schedule_rows(pages, columns):
    """Строки приложения, как leginet.rows. В таблице «Προϊόντα | Συντελεστής» ряд — строки
    без просвета; текст столбца ставок идёт после текста товара отдельным абзацем."""
    left = rate_left(pages, columns)
    result = []
    in_table = False
    for words in pages:
        block = []
        previous = None
        for y, parts in leginet.page_lines(words, columns.line_tolerance):
            line = ' '.join(text for _, _, text in parts)
            if columns.furniture.match(line):
                continue
            in_table = in_table or (left is not None and RATE_HEADER in line)
            gap = None
            if previous is None:
                gap = leginet.PAGE_BREAK
            elif y - previous > columns.gap:
                gap = ''
            previous = y
            if gap is not None:
                result.extend(table_rows(block, columns, in_table))
                block = []
            product, rate = split_rate(parts, left) if in_table else (parts, [])
            block.append((gap, product, rate))
        result.extend(table_rows(block, columns, in_table))
    return result


def table_rows(block, columns, in_table=True):
    """Ряд таблицы (или абзац вне её) → строки: столбец товаров, затем ставки.

    Оговорка «Νοείται …» в столбце товаров таблицы — свой абзац. Если ряд начинается
    ставкой («€429,00 …»), оговорки под товаром идут после ставки: они меняют ставку и
    продолжаются на следующей странице, не перемешиваясь с ней (Πρώτο Παράρτημα
    91(Ι)/2004); такая ставка помечена RATE_MARK (см. pdf_layout). Ссылка «N. 140(I)/2018»
    под ставкой — в конце текста товара: текст ставки, уже переведённый, не меняется. Вне
    таблицы (`in_table` ложно) абзац не делится."""
    rates = [' '.join(word for _, _, word in rate) for _, _, rate in block if rate]
    references = [body for body in rates if LAW_REF_RE.match(body)]
    rates = [body for body in rates if body not in references]
    with_rate = in_table and bool(rates) and bool(RATE_RE.match(rates[0]))
    # строки — (текст, отступ, начало абзаца)
    products = []
    provisos = []
    for _, product, _ in block:
        if not product:
            continue
        _, body, left = leginet.split(product, columns)
        body = ITEM_PAREN_RE.sub(r'\1. ', body)
        indent = leginet.indent_of(left, columns)
        if in_table and PROVISO_RE.match(body):
            provisos.append((body, indent, True))
        elif provisos:
            provisos.append((body, indent, False))
        else:
            products.append((body, indent, False))
    products.extend((body, TEXT_INDENT, False) for body in references)
    marked = []
    for index, body in enumerate(rates):
        if not index and with_rate:
            body += RATE_MARK
        # «(β) €6,00 …» под «(α) …» в столбце ставок (Μπύρα, Δεύτερο Παράρτημα)
        marked.append((body, TEXT_INDENT, not index or bool(POINT_RE.match(body))))
    if with_rate:
        lines = products + marked + provisos
    else:
        lines = products + provisos + marked
    gap = block[0][0] if block else None
    rows = []
    if gap == leginet.PAGE_BREAK and provisos and lines[0] is provisos[0]:
        # оговорка вверху страницы — новый абзац, не продолжение ставки на прошлой
        rows.append(('', '', TEXT_INDENT, gap))
        gap = ''
    for body, indent, starts in lines:
        rows.append(('', body, indent, gap if not rows else ('' if starts else None)))
    return rows


def pdf_lines(path):
    return pdf_layout(path)[0]


def pdf_layout(path):
    """Строки раскладки и номера (с единицы, как у layout.blocks) строк ставок таблиц
    приложений, за которыми идёт пункт «N. …»: ставка — абзац своего пункта, не заголовок
    следующего (consolidated Parser.rate_lines)."""
    lines = []
    marked = []
    for line in marked_lines(path):
        if line.endswith(RATE_MARK):
            marked.append(len(lines))
            line = line[:-len(RATE_MARK)]
        lines.append(line)
    rate_lines = {index + 1 for index in marked if before_item(lines, index)}
    return lines, rate_lines


def before_item(lines, index):
    """За абзацем, который начинается строкой index, следует пункт «N. …»."""
    index += 1
    while index < len(lines) and lines[index].strip():
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index < len(lines) and bool(ARTICLE_RE.match(lines[index].strip()))


def marked_lines(path):
    pages = [attach_punctuation(words) for words in leginet.pdf_words(path)]
    columns = columns_of(pages)
    body_pages, schedule_pages = split_schedule(pages, columns)
    body_pages = [attach_numbers(words) for words in body_pages]
    raw_rows = join_dangling(leginet.rows(path, columns, body_pages))
    start = text_start(mark_articles(raw_rows))
    toc = toc_headings(raw_rows[:start])
    all_rows = mark_articles(raw_rows[start:], toc)
    headings = leginet.article_headings(all_rows, toc, across_gaps=True)
    toc_texts = [' '.join((margin, body)).strip() for margin, body, _, _ in raw_rows[:start]]
    if any(ORDINAL_SCHEDULE_RE.match(text) for text in toc_texts):
        # оглавление сводки акцизов: заголовки на поле без точек и со ссылками на Ε.Ε.
        headings = toc_article_headings(all_rows, toc)
    lines = uncenter_text(
        leginet.body_lines(all_rows, toc, across_gaps=True, headings=headings)
    )
    for pages_of_one in split_schedules(schedule_pages, columns):
        # заголовки статей из оглавления к пунктам Παράρτημα не относятся
        rows = schedule_rows(pages_of_one, schedule_columns(pages_of_one, columns))
        margin, body, indent, _ = rows[0]
        rows[0] = (margin, body, indent, '')
        lines += center_headings(leginet.body_lines(rows, {}, across_gaps=True))
    return [TEXT_HEADING, ''] + close_before_centered(join_parts(lines))
