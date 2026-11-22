"""Файлы Nomos-texts: act.xml (метаданные, дерево, редакции, изменения) и <lang>.xml (тексты).

Формат — docs/design/data-model.md, раздел 3.3.
"""

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from . import numbering


@dataclass
class ActInfo:
    uid: str
    jurisdiction: str
    kind: str
    number: str = ''
    year: str = ''
    published_on: object = None
    published_in: str = ''
    in_force_from: object = None
    structure_lang: str = 'el'
    lang: str = 'el'
    status: str = 'official_text'
    source: str = ''
    title: str = ''
    short_title: str = ''
    relations: list = field(default_factory=list)
    amendments: list = field(default_factory=list)


@dataclass
class AmendmentInfo:
    key: str
    provision: str
    kind: str
    published_on: object = None
    in_force_from: object = None
    applies_from: object = None
    note: str = ''


def attrs(pairs):
    """Атрибуты по порядку; пустые значения пропускаются."""
    parts = []
    for name, value in pairs:
        if value is None or value == '':
            continue
        if hasattr(value, 'isoformat'):
            value = value.isoformat()
        parts.append('%s=%s' % (name, quoteattr(str(value))))
    return ' '.join(parts)


def existing_uids(path):
    """eid → uid из уже записанного act.xml: uid не меняется при пересборке."""
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    return {item.get('eid'): item.get('uid') for item in root.iter('fragment')}


def translated_uids(folder):
    """uid фрагментов, у которых есть текст перевода (все языки, кроме el)."""
    found = set()
    for path in folder.glob('*.xml'):
        if path.name in ('act.xml', 'el.xml', 'collect.xml', 'cylaw-notes.xml'):
            continue
        found.update(uid for uid, _ in read_texts(path))
    return found


def lost_uids(folder, uids):
    """Фрагменты с переводом, которые пересборка убрала из act.xml: [(eid, uid), …].

    uid хранится только в act.xml: фрагмент, исчезнувший при одной сборке и вернувшийся при
    другой, получил бы новый uid, и перевод потерял бы привязку."""
    kept = set(uids.values())
    translated = translated_uids(folder)
    return sorted(
        (eid, uid)
        for eid, uid in existing_uids(folder / 'act.xml').items()
        if uid not in kept and uid in translated
    )


def assign_uids(info, nodes, path):
    known = existing_uids(path)
    taken = set(known.values())
    uids = {}
    for node in nodes:
        uid = known.get(node.eid)
        if uid is None:
            uid = numbering.make_uid(info.uid, node.eid)
            suffix = '~'
            while uid in taken:
                uid = numbering.make_uid(info.uid, node.eid + suffix)
                suffix += '~'
            taken.add(uid)
        uids[node] = uid
    return uids


TAG_RE = re.compile(r'<[^>]+>')

TEXT_BLOCK_RE = re.compile(
    r'^  <text fragment="(?P<uid>[^"]+)" version="(?P<key>[^"]+)"[^>]*>\n'
    r'(?P<body>(?:    .*\n)*?)'
    r'  </text>\n',
    re.M,
)


def plain(markup):
    """Текст абзаца без разметки, пробелы схлопнуты: для сравнения разборов."""
    return ' '.join(html.unescape(TAG_RE.sub('', markup)).split())


def old_texts(path):
    """{uid: {редакция: текст}} из прежнего el.xml в порядке файла; абзацы — через пробел."""
    found = {}
    if not path.exists():
        return found
    for text in ET.parse(path).getroot().iter('text'):
        body = ' '.join(' '.join(''.join(p.itertext()).split()) for p in text.iter('p'))
        if body:
            found.setdefault(text.get('fragment'), {})[text.get('version')] = body
    return found


def same_text(old, new, num):
    """Тексты по редакциям совпадают; номер пункта, бывший в начале текста, ушёл в num."""
    if set(old) != set(new):
        return False
    prefix = (num or '') + ' '
    return all(old[key] in (new[key], prefix + new[key]) for key in old)


def follow_texts(info, uids, new, old, nums, eids):
    """uid идёт за текстом, а не за eid.

    Новый разбор переносит текст на другое место (пункт «42.—(1)(α)» стал своим фрагментом
    art_42.par_1.pnt_a): фрагмент, текст которого совпал с прежним текстом другого uid,
    получает этот uid, и перевод остаётся при тексте. Меняются только фрагменты, у которых
    текст по eid не совпал; прежний владелец uid получает новый.

    new: {node: {редакция: текст}}; old: {uid: {редакция: текст}}; nums: {node: номер};
    eids: {uid: прежний eid}."""
    moved = [node for node, uid in uids.items() if new[node] and old.get(uid, {}) != new[node]]
    holder = {uid: node for node, uid in uids.items()}
    free = {
        uid: texts for uid, texts in old.items()
        if uid not in holder or (holder[uid] in moved or not new[holder[uid]])
    }
    taken = set(uids.values()) | set(old)
    for node in moved:
        found = [uid for uid, texts in free.items() if same_text(texts, new[node], nums[node])]
        if len(found) > 1:
            # одинаковый текст в разных местах («ή» в трёх пунктах прил. 10): ближайший по eid
            found = closest(found, node.eid, eids)
        if len(found) != 1 or found[0] == uids[node]:
            continue
        uid = found[0]
        del free[uid]
        previous = holder.get(uid)
        if previous is not None and previous is not node:
            fresh = numbering.make_uid(info.uid, previous.eid)
            suffix = '~'
            while fresh in taken:
                fresh = numbering.make_uid(info.uid, previous.eid + suffix)
                suffix += '~'
            taken.add(fresh)
            uids[previous] = fresh
            holder[fresh] = previous
        uids[node] = uid
        holder[uid] = node
    return uids


def closest(found, eid, eids):
    """uid, прежний eid которых делит с eid самое длинное начало по частям; один или все."""
    parts = eid.split('.')

    def common(uid):
        count = 0
        for mine, theirs in zip(parts, eids.get(uid, '').split('.')):
            if mine != theirs:
                break
            count += 1
        return count

    best = max(common(uid) for uid in found)
    return [uid for uid in found if common(uid) == best]


def joined(first, second):
    """Тексты, которые даёт склейка двух абзацев через перенос страницы."""
    found = {first + ' ' + second, first + second}
    if first.endswith('-'):
        found.add(first[:-1] + second)
    return found


def merged_uids(uids, new, old, lost):
    """Фрагменты, слитые новым разбором с предыдущим текстом: [(uid предыдущего, uid слитого)].

    Абзац, разорванный переносом страницы, прежний разбор делил на два фрагмента; новый
    склеивает их. Слитый — потерянный uid, чей текст по всем редакциям продолжает текст
    предыдущего по el.xml фрагмента."""
    node_by_uid = {uid: node for node, uid in uids.items()}
    order = list(old)
    found = []
    for uid in lost:
        if uid not in old:
            continue
        index = order.index(uid)
        if index == 0:
            continue
        previous = order[index - 1]
        node = node_by_uid.get(previous)
        if node is None:
            continue
        texts = old[uid]
        if all(
            key in old[previous] and new[node].get(key) in joined(old[previous][key], texts[key])
            for key in texts
        ):
            found.append((previous, uid))
    return found


def headed_uids(uids, headings, old, lost):
    """Фрагменты, ставшие заголовком следующего: [(uid потерянного, uid нового, редакция)].

    Прежний разбор принимал заголовок пункта за абзац («Πρόσβαση … κ.τ.λ. εκδηλώσεις.»
    в конце п. 3 прил. 13 95(Ι)); новый ставит его заголовком следующего фрагмента.
    headings — {узел: {редакция: заголовок}}."""
    node_by_uid = {uid: node for node, uid in uids.items()}
    order = list(old)
    found = []
    for uid in lost:
        if uid not in old or len(old[uid]) != 1:
            continue
        (key, text), = old[uid].items()
        index = order.index(uid)
        for following in order[index + 1:index + 2] + [None]:
            # следующий по el.xml или, если у него нет текста, первый узел с этим заголовком
            nodes = [node_by_uid.get(following)] if following else list(headings)
            target = next(
                (node for node in nodes if node and headings.get(node, {}).get(key) == text),
                None,
            )
            if target is not None:
                found.append((uid, uids[target], key))
                break
    return found


def move_to_headings(folder, triples):
    """Перевод фрагмента, ставшего заголовком, — в атрибут heading записи нового фрагмента.

    Текст записи (абзацы без разметки) становится заголовком; запись потерянного убирается,
    её примечания переходят в запись нового. Запись нового создаётся, если её нет."""
    moves = {(uid, key): target for uid, target, key in triples}
    for path in sorted(folder.glob('*.xml')):
        if path.name in ('act.xml', 'el.xml', 'collect.xml', 'cylaw-notes.xml'):
            continue
        source = path.read_text(encoding='utf-8')
        blocks = {
            (match.group('uid'), match.group('key')): match
            for match in TEXT_BLOCK_RE.finditer(source)
        }
        headings = {}
        drop = {}
        for (uid, key), target in moves.items():
            match = blocks.get((uid, key))
            if match is None:
                continue
            body = match.group('body')
            paragraphs = re.findall(r'^    <p>(.*)</p>$', body, re.M)
            heading = ' '.join(re.sub(r'<[^>]+>', '', item) for item in paragraphs)
            heading = heading.replace('"', '&quot;')
            rest = ''.join(
                line for line in body.splitlines(keepends=True)
                if not line.startswith('    <p>')
            )
            attributes = re.search(r'^  <text [^>]*?((?: translator="[^"]*")?'
                                   r'(?: review="[^"]*")?)>', match.group(0)).group(1)
            headings[(target, key)] = (heading, rest, attributes)
            drop[(uid, key)] = (target, key)
        if not drop:
            continue

        def rewrite(match):
            key = (match.group('uid'), match.group('key'))
            if key in drop:
                target = drop[key]
                if target in blocks or target not in headings:
                    return ''
                # записи нового фрагмента нет: новая запись на месте убранной
                heading, rest, attributes = headings.pop(target)
                return '  <text fragment="%s" version="%s" heading="%s"%s>\n%s  </text>\n' % (
                    target[0], target[1], heading, attributes, rest,
                )
            if key not in headings:
                return match.group(0)
            heading, rest, _ = headings.pop(key)
            head, body = match.group(0).split('\n', 1)
            head = re.sub(r' heading="[^"]*"', '', head)
            head = head.replace(
                ' version="%s"' % key[1], ' version="%s" heading="%s"' % (key[1], heading), 1,
            )
            return head + '\n' + rest + body

        result = TEXT_BLOCK_RE.sub(rewrite, source)
        path.write_text(result, encoding='utf-8')


def merge_translations(folder, pairs):
    """Перевод слитого фрагмента дописывается к переводу предыдущего: первый абзац — в конец
    последнего абзаца через пробел, прочее — следом. Запись слитого убирается.

    Перевод не меняется, только соединяется так же, как el; записи без пары остаются."""
    into = dict((child, parent) for parent, child in pairs)
    for path in sorted(folder.glob('*.xml')):
        if path.name in ('act.xml', 'el.xml', 'collect.xml', 'cylaw-notes.xml'):
            continue
        source = path.read_text(encoding='utf-8')
        blocks = {
            (match.group('uid'), match.group('key')): match
            for match in TEXT_BLOCK_RE.finditer(source)
        }
        tails = {}
        drop = set()
        for (uid, key), match in blocks.items():
            parent = into.get(uid)
            if parent is None or (parent, key) not in blocks:
                continue
            tails.setdefault((parent, key), []).append(match.group('body'))
            drop.add((uid, key))
        if not drop:
            continue

        def rewrite(match):
            key = (match.group('uid'), match.group('key'))
            if key in drop:
                return ''
            body = match.group('body')
            for tail in tails.get(key, []):
                body = join_bodies(body, tail)
            start = match.start('body') - match.start()
            end = match.end('body') - match.start()
            text = match.group(0)
            return text[:start] + body + text[end:]

        path.write_text(TEXT_BLOCK_RE.sub(rewrite, source), encoding='utf-8')


def join_bodies(body, tail):
    """Строки записи перевода: первый <p> хвоста — в конец последнего <p> тела."""
    lines = body.splitlines(keepends=True)
    rest = tail.splitlines(keepends=True)
    if (
        lines and rest
        and lines[-1].rstrip('\n').endswith('</p>')
        and rest[0].startswith('    <p>')
    ):
        last = lines[-1].rstrip('\n')[:-len('</p>')]
        first = rest[0][len('    <p>'):]
        lines[-1] = last + ' ' + first
        rest = rest[1:]
    return ''.join(lines + rest)


# элементы act.xml, которые порождает сборщик; прочие сохраняются как есть
OWN_TAGS = {'expression', 'relation', 'amendment', 'fragment'}


def foreign_parts(path, lang):
    """Из уже записанного act.xml: чужие атрибуты своего выражения и чужие элементы.

    Выражения других языков (переводы) и элементы, которых сборщик не пишет,
    переносятся в новый файл без изменений."""
    if not path.exists():
        return {}, []
    root = ET.parse(path).getroot()
    own_extra = {}
    kept = []
    for element in root:
        element.tail = None
        if element.tag == 'expression' and element.get('lang') == lang:
            own_extra = dict(element.attrib)
            continue
        if element.tag not in OWN_TAGS or element.tag == 'expression':
            kept.append(ET.tostring(element, encoding='unicode').strip())
    return own_extra, kept


def write_act(path, info, versions, uids):
    """versions: {node: [history.Version]} в порядке обхода дерева."""
    own_extra, kept = foreign_parts(path, info.lang)
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append('<act %s>' % attrs([
        ('uid', info.uid),
        ('jurisdiction', info.jurisdiction),
        ('kind', info.kind),
        ('number', info.number),
        ('year', info.year),
        ('published-on', info.published_on),
        ('published-in', info.published_in),
        ('in-force-from', info.in_force_from),
        ('structure-lang', info.structure_lang),
    ]))
    own = [
        ('lang', info.lang),
        ('status', info.status),
        ('source', info.source),
        ('file', '%s.xml' % info.lang),
        ('title', info.title),
        ('short-title', info.short_title),
    ]
    # чего сборщик не знает (например short-title), берётся из прежнего файла
    own = [(name, value or own_extra.get(name, '')) for name, value in own]
    names = {name for name, _ in own}
    own += [(name, value) for name, value in own_extra.items() if name not in names]
    lines.append('  <expression %s />' % attrs(own))
    for element in kept:
        lines.append('  ' + element)
    for kind, target in info.relations:
        lines.append('  <relation %s />' % attrs([('kind', kind), ('to', target)]))
    for amendment in info.amendments:
        lines.append('  <amendment %s>%s</amendment>' % (
            attrs([
                ('key', amendment.key),
                ('provision', amendment.provision),
                ('kind', amendment.kind),
                ('published-on', amendment.published_on),
                ('in-force-from', amendment.in_force_from),
                ('applies-from', amendment.applies_from),
            ]),
            escape(amendment.note),
        ))
    lines.append('')
    for node, node_versions in versions.items():
        lines.append('  <fragment %s>' % attrs([
            ('uid', uids[node]),
            ('kind', node.kind),
            ('eid', node.eid),
            ('parent', node.parent_eid),
            ('order-key', node.order_key),
        ]))
        for version in node_versions:
            lines.append('    <version %s />' % attrs([
                ('key', version.key),
                ('num', node.num if version.num is None else version.num),
                ('heading', node.heading if version.heading is None else version.heading),
                ('amendment', version.amendment),
                ('state', '' if version.state == 'active' else version.state),
                ('valid-from', version.valid_from),
                ('applies-from', version.applies_from),
                ('known-from', version.known_from),
            ]))
        lines.append('  </fragment>')
    lines.append('</act>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_texts(path, info, versions, uids, markup, refs=None):
    """markup(paragraph) → разметка абзаца без <p>.

    refs(node, version, абзацы) → абзацы со ссылками: разметка на весь текст сразу."""
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append('<texts %s>' % attrs([('act', info.uid), ('lang', info.lang)]))
    for node, node_versions in versions.items():
        for version in node_versions:
            if not version.paragraphs and not version.notes:
                continue
            unknown = '1' if version.paragraphs is None else ''
            lines.append('  <text %s>' % attrs([
                ('fragment', uids[node]),
                ('version', version.key),
                ('unknown', unknown),
            ]))
            paragraphs = [markup(paragraph) for paragraph in version.paragraphs or []]
            if refs is not None:
                paragraphs = refs(node, version, paragraphs)
            for paragraph in paragraphs:
                lines.append('    <p>%s</p>' % paragraph)
            for note in version.notes:
                lines.append(
                    '    <note kind="editor_comment" status="open">%s</note>' % escape(note)
                )
            lines.append('  </text>')
    lines.append('</texts>')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_notes(path, info, notes, inline=()):
    """Примечания редакции CyLaw: хранятся, в БД не грузятся.

    notes — тексты без привязки; inline — (uid фрагмента, редакция, текст) из [Σ.Σ.: …]."""
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append('<notes %s>' % attrs([('act', info.uid), ('lang', info.lang)]))
    for note in notes:
        lines.append('  <note kind="source_comment">%s</note>' % escape(note))
    for uid, key, note in inline:
        lines.append('  <note %s>%s</note>' % (
            attrs([('kind', 'source_comment'), ('fragment', uid), ('version', key)]),
            escape(note),
        ))
    lines.append('</notes>')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def act_paths(root):
    """Все act.xml папки текстов по порядку, кроме копий источников (папки sources)."""
    return sorted(
        path for path in root.rglob('act.xml')
        if 'sources' not in path.relative_to(root).parts
    )


def read_act(path):
    return ET.parse(path).getroot()


def read_texts(path):
    """{(uid, key): (тело из <p>, [<note> уровня текста], неизвестен ли текст, заголовок)}."""
    found = {}
    if not path.exists():
        return found
    for text in ET.parse(path).getroot().iter('text'):
        body = []
        notes = []
        for child in text:
            child.tail = None
            if child.tag == 'note':
                notes.append(child)
            else:
                body.append(ET.tostring(child, encoding='unicode'))
        key = (text.get('fragment'), text.get('version'))
        found[key] = (
            '\n'.join(body),
            notes,
            text.get('unknown') == '1',
            text.get('heading', ''),
        )
    return found
