"""Файлы Nomos-texts для актов ЕС: act.xml и en.xml, el.xml.

Формат — docs/design/data-model.md, раздел 3.3. Общие части (атрибуты, uid фрагментов,
чужие элементы act.xml) — из nomos.sources.cylaw.texts_xml.
"""

import re
from dataclasses import dataclass, field

from nomos.sources.cylaw.texts_xml import (
    amendment_line, assign_uids, attrs, foreign_parts, version_line,
)

LANGS = ('en', 'el')
OWN_EXPRESSION_RE = re.compile(r'<expression\b[^>]*\blang="(?:en|el)"')


@dataclass
class ActInfo:
    uid: str
    kind: str
    number: str
    year: str
    celex: str = ''
    eli: str = ''
    date_adopted: object = None
    published_on: object = None
    published_in: str = ''
    in_force_from: object = None
    status: str = 'official_text'
    source: str = 'eurlex'
    # язык → название
    titles: dict = field(default_factory=dict)
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
    marker: str = ''
    note: str = ''


def write_act(path, info, fragments, uids):
    """fragments — editions.Fragment в порядке записи."""
    _, kept = foreign_parts(path, 'en')
    # выражения en и el пишет сборщик; переводы (ru) остаются из прежнего файла
    kept = [element for element in kept if not OWN_EXPRESSION_RE.match(element)]
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append('<act %s>' % attrs([
        ('uid', info.uid),
        ('jurisdiction', 'eu'),
        ('kind', info.kind),
        ('number', info.number),
        ('year', info.year),
        ('celex', info.celex),
        ('eli', info.eli),
        ('date-adopted', info.date_adopted),
        ('published-on', info.published_on),
        ('published-in', info.published_in),
        ('in-force-from', info.in_force_from),
        ('structure-lang', 'en'),
    ]))
    for lang in LANGS:
        lines.append('  <expression %s />' % attrs([
            ('lang', lang),
            ('status', info.status),
            ('source', info.source),
            ('file', '%s.xml' % lang),
            ('title', info.titles.get(lang, '')),
        ]))
    for element in kept:
        lines.append('  ' + element)
    for kind, target in info.relations:
        lines.append('  <relation %s />' % attrs([('kind', kind), ('to', target)]))
    for amendment in info.amendments:
        lines.append(amendment_line(amendment, with_marker=True))
    lines.append('')
    for fragment in fragments:
        lines.append('  <fragment %s>' % attrs([
            ('uid', uids[fragment]),
            ('kind', fragment.kind),
            ('eid', fragment.eid),
            ('parent', fragment.parent_eid),
            ('order-key', fragment.order_key),
        ]))
        for version in fragment.versions:
            lines.append(version_line(version, version.num, version.heading))
        lines.append('  </fragment>')
    lines.append('</act>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_texts(path, act_uid, lang, fragments, uids, mark):
    """mark(fragment, абзацы) → абзацы со ссылками.

    Таблица — блоком без <p>; заголовок на языке текста, если он не совпадает с заголовком
    редакции в act.xml, — в атрибуте heading."""
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append('<texts %s>' % attrs([('act', act_uid), ('lang', lang)]))
    for fragment in fragments:
        for version in fragment.versions:
            paragraphs, heading = version.texts.get(lang, ([], ''))
            if not paragraphs and not heading:
                continue
            if heading == version.heading:
                heading = ''
            lines.append('  <text %s>' % attrs([
                ('fragment', uids[fragment]),
                ('version', version.key),
                ('heading', heading),
            ]))
            for paragraph in mark(fragment, paragraphs):
                if paragraph.startswith('<table'):
                    lines.append('    %s' % paragraph)
                else:
                    lines.append('    <p>%s</p>' % paragraph)
            lines.append('  </text>')
    lines.append('</texts>')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


__all__ = ['ActInfo', 'AmendmentInfo', 'assign_uids', 'write_act', 'write_texts']
