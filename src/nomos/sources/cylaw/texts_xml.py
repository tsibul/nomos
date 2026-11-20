"""Файлы Nomos-texts: act.xml (метаданные, дерево, редакции, изменения) и <lang>.xml (тексты).

Формат — docs/design/data-model.md, раздел 3.3.
"""

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
    marker: str
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


def assign_uids(info, nodes, path):
    known = existing_uids(path)
    taken = set(known.values())
    uids = {}
    for node in nodes:
        uid = known.get(node.eid)
        if uid is None:
            uid = numbering.make_uid(info.uid, node.eid)
            while uid in taken:
                uid = numbering.make_uid(info.uid, node.eid + '~')
            taken.add(uid)
        uids[node] = uid
    return uids


def write_act(path, info, versions, uids):
    """versions: {node: [history.Version]} в порядке обхода дерева."""
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
    lines.append('  <expression %s />' % attrs([
        ('lang', info.lang),
        ('status', info.status),
        ('source', info.source),
        ('file', '%s.xml' % info.lang),
        ('title', info.title),
        ('short-title', info.short_title),
    ]))
    for kind, target in info.relations:
        lines.append('  <relation %s />' % attrs([('kind', kind), ('to', target)]))
    for amendment in info.amendments:
        lines.append('  <amendment %s>%s</amendment>' % (
            attrs([
                ('marker', amendment.marker),
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
                ('num', node.num),
                ('heading', node.heading),
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


def write_texts(path, info, versions, uids, markup):
    """markup(paragraph) → разметка абзаца без <p>."""
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
            for paragraph in version.paragraphs or []:
                lines.append('    <p>%s</p>' % markup(paragraph))
            for note in version.notes:
                lines.append(
                    '    <note kind="editor_comment" status="open">%s</note>' % escape(note)
                )
            lines.append('  </text>')
    lines.append('</texts>')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def read_act(path):
    return ET.parse(path).getroot()


def read_texts(path):
    """{(uid, key): (тело из <p>, [элементы <note> уровня текста], неизвестен ли текст)}."""
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
        found[key] = ('\n'.join(body), notes, text.get('unknown') == '1')
    return found
