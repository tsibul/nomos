"""Сборка акта ЕС из Formex EUR-Lex в XML Nomos-texts.

Сводные редакции и изменяющие акты (полный текст OJ) перечислены в манифесте
`<акт>/collect.xml`; сами файлы — в папке сырых данных (raw/, не в git). Сеть не нужна.
Пишутся act.xml, en.xml и el.xml акта и каждого изменяющего акта; в конце — отчёт.
"""

import datetime
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from nomos.sources.cylaw.history import BASE_KEY
from nomos.sources.eurlex import editions, formex, refs, texts_xml

AMENDED_WORDS = ('is amended', 'are amended', 'τροποποιείται', 'τροποποιούνται')


def day(value):
    return datetime.date.fromisoformat(value) if value else None


def read_manifest(path):
    if not path.exists():
        raise CommandError('нет манифеста %s' % path)
    root = ET.parse(path).getroot()
    act = dict(root.attrib)
    note = root.find('note')
    act['note'] = ' '.join((note.text or '').split()) if note is not None else ''
    act['editions'] = [dict(item.attrib) for item in root.iter('edition')]
    act['amending'] = []
    for item in root.iter('amending'):
        amending = dict(item.attrib)
        note = item.find('note')
        amending['note'] = ' '.join((note.text or '').split()) if note is not None else ''
        act['amending'].append(amending)
    act['ref_fixes'] = [
        dict(item.attrib, reason=' '.join((item.text or '').split()))
        for item in root.iter('ref-fix')
    ]
    return act


def fix_refs(paragraphs, lang, eid, fixes, applied):
    """Цель ссылки исправлена по манифесту (<ref-fix>): текст как в EUR-Lex, а номер
    приложения в нём ошибочен (2006/112, el, ст. 295(1)(5) и 381)."""
    for index, fix in enumerate(fixes):
        if fix['lang'] != lang or fix['fragment'] != eid:
            continue
        pattern = re.compile(r'to="%s(?=[".])' % re.escape(fix['from']))
        fixed = []
        for paragraph in paragraphs:
            paragraph, count = pattern.subn('to="%s' % fix['to'], paragraph)
            applied[index] += count
            fixed.append(paragraph)
        paragraphs = fixed
    return paragraphs


def act_info(manifest, titles):
    return texts_xml.ActInfo(
        uid=manifest['uid'],
        kind=manifest['kind'],
        number=manifest['number'],
        year=manifest['year'],
        celex=manifest.get('celex', ''),
        eli=manifest.get('eli', ''),
        date_adopted=day(manifest.get('date-adopted')),
        published_on=day(manifest.get('published-on')),
        published_in=manifest.get('published-in', ''),
        in_force_from=day(manifest.get('in-force-from')),
        titles=titles,
    )


def base_amendment(info, note=''):
    return texts_xml.AmendmentInfo(
        key=BASE_KEY,
        provision='',
        kind='base',
        published_on=info.published_on,
        in_force_from=info.in_force_from,
        note=note,
    )


def article_of(eid):
    head = eid.split('.')[0]
    return head if head.startswith('art_') else ''


def amended_acts(fragments, own_uid):
    """Статья изменяющего акта → акт, к которому относятся её ссылки без «of …».

    «Regulation (EU) No 904/2010 is amended as follows» — ссылки внутри — на 904/2010;
    «Directive 2006/112/EC is amended» — на директиву; прочие статьи — на сам акт."""
    first = {}
    for fragment in fragments:
        article = article_of(fragment.eid)
        if not article or article in first:
            continue
        paragraphs, _ = fragment.versions[0].texts.get('en', ([], ''))
        if paragraphs:
            first[article] = paragraphs[0]
    found = {}
    for article, text in first.items():
        if not any(word in text for word in AMENDED_WORDS):
            continue
        for number, uid in refs.EU_ACTS.items():
            if number in text and uid != own_uid:
                found[article] = uid
                break
    return found


def known_eids(root_dir, uid):
    """Адреса фрагментов уже собранного акта (act.xml в Nomos-texts) или None."""
    path = root_dir / uid / 'act.xml'
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    return {element.get('eid') for element in root.iter('fragment')}


def previous_links(path):
    """Связи и изменения из прежнего act.xml изменяющего акта.

    Изменяющий акт может менять несколько актов (2020/285 — директиву и 904/2010); сборка
    одного из них не должна стирать изменения, записанные сборкой другого."""
    if not path.exists():
        return [], []
    root = ET.parse(path).getroot()
    relations = [(item.get('kind'), item.get('to')) for item in root.iter('relation')]
    amendments = []
    for item in root.iter('amendment'):
        amendments.append(texts_xml.AmendmentInfo(
            key=item.get('key'),
            provision=item.get('provision', ''),
            kind=item.get('kind'),
            published_on=day(item.get('published-on')),
            in_force_from=day(item.get('in-force-from')),
            applies_from=day(item.get('applies-from')),
            marker=item.get('marker', ''),
            note=' '.join((item.text or '').split()),
        ))
    return relations, amendments


def merge_links(info, path):
    """Добавить к связям и изменениям info прежние, которых сборка не породила."""
    relations, amendments = previous_links(path)
    for relation in relations:
        if relation not in info.relations:
            info.relations.append(relation)
    # порядок не зависит от того, какой из изменяемых актов собран последним
    info.relations.sort()
    new = {amendment.key: amendment for amendment in info.amendments}
    merged = []
    for amendment in amendments:
        fresh = new.pop(amendment.key, None)
        if fresh is None:
            merged.append(amendment)
            continue
        # пометка — из манифеста собираемого акта; без неё — прежняя
        if not fresh.note:
            fresh.note = amendment.note
        merged.append(fresh)
    merged.extend(new.values())
    info.amendments = merged


def write(folder, info, fragments, known, defaults=None, fixes=(), report=None):
    """act.xml, en.xml, el.xml; число ссылок по языкам."""
    defaults = defaults or {}
    applied = [0] * len(fixes)
    uids = texts_xml.assign_uids(info, fragments, folder / 'act.xml')
    texts_xml.write_act(folder / 'act.xml', info, fragments, uids)
    counts = {}
    for lang in texts_xml.LANGS:
        linker = refs.Linker(lang, info.uid, known)

        def mark(fragment, paragraphs, lang=lang, linker=linker):
            default = defaults.get(article_of(fragment.eid))
            marked = linker.mark(paragraphs, fragment.eid, default)
            return fix_refs(marked, lang, fragment.eid, fixes, applied)

        texts_xml.write_texts(folder / ('%s.xml' % lang), info.uid, lang, fragments, uids, mark)
        counts[lang] = linker.found
    if report is not None:
        for fix, count in zip(fixes, applied):
            report.append('ссылка исправлена %s %s: %s → %s — %d' % (
                fix['lang'], fix['fragment'], fix['from'], fix['to'], count,
            ))
    return counts


def ref_summary(found):
    outside = sum(1 for target in found if '#' in target)
    return '%d (внутри акта %d, в других актах %d)' % (len(found), len(found) - outside, outside)


class Command(BaseCommand):
    help = 'Собрать акт ЕС из Formex EUR-Lex в XML Nomos-texts'

    def add_arguments(self, parser):
        parser.add_argument('act', help='адрес акта, например eu/reg/2010/904')
        parser.add_argument(
            '--texts-dir',
            default=os.environ.get('NOMOS_TEXTS_DIR', ''),
            help='папка Nomos-texts (по умолчанию NOMOS_TEXTS_DIR)',
        )
        parser.add_argument(
            '--raw-dir',
            default=os.environ.get('NOMOS_RAW_DIR', str(Path(settings.BASE_DIR).parent / 'raw')),
            help='папка сырых данных (по умолчанию NOMOS_RAW_DIR или raw/ проекта)',
        )

    def handle(self, act, texts_dir, raw_dir, **options):
        if not texts_dir:
            raise CommandError('не задана папка текстов: --texts-dir или NOMOS_TEXTS_DIR')
        root_dir = Path(texts_dir)
        raw = Path(raw_dir)
        manifest = read_manifest(root_dir / act / 'collect.xml')
        report = []

        amending = {}
        for item in manifest['amending']:
            amending[item['celex']] = {
                'uid': item['uid'],
                'in_force_from': item.get('in-force-from'),
                'published_on': item.get('published-on'),
            }
        consolidated = []
        for item in manifest['editions']:
            en = self.parse(raw / item['en'], 'en', report)
            el = self.parse(raw / item['el'], 'el', report)
            consolidated.append(editions.Edition(
                start=en.info.get('START.DATE', item['start']),
                end=en.info.get('END.DATE', ''),
                source_end=en.info.get('SOURCE.END', ''),
                en=en,
                el=el,
            ))
        if not consolidated:
            raise CommandError('в манифесте нет сводных редакций')
        history = editions.History(manifest['uid'], consolidated, amending, report)
        fragments = history.build()
        latest = consolidated[-1]
        info = act_info(manifest, {
            'en': latest.en.info.get('title', ''),
            'el': latest.el.info.get('title', ''),
        })
        info.amendments.append(base_amendment(info, manifest['note']))

        parsed = []
        for item in manifest['amending']:
            en = self.parse(raw / item['en'], 'en', report)
            el = self.parse(raw / item['el'], 'el', report)
            parsed.append((item, en, el))

        known = {}
        for uid in refs.EU_ACTS.values():
            eids = known_eids(root_dir, uid)
            if eids is not None:
                known[uid] = eids
        known[info.uid] = {fragment.eid for fragment in fragments}
        amending_fragments = {}
        for item, en, el in parsed:
            edition = editions.Edition(start='', end='', source_end='', en=en, el=el)
            own = editions.History(item['uid'], [edition], {}, report).build()
            for fragment in own:
                version = fragment.versions[0]
                version.valid_from = item.get('in-force-from')
                version.known_from = item.get('published-on')
            amending_fragments[item['uid']] = own
            known[item['uid']] = {fragment.eid for fragment in own}

        counts = write(
            root_dir / info.uid, info, fragments, known,
            fixes=manifest['ref_fixes'], report=report,
        )
        versions = sum(len(fragment.versions) for fragment in fragments)
        self.stdout.write('%s: фрагментов %d, редакций %d' % (info.uid, len(fragments), versions))
        for lang, found in counts.items():
            self.stdout.write('  ссылок %s: %s' % (lang, ref_summary(found)))

        for item, en, el in parsed:
            uid = item['uid']
            own = amending_fragments[uid]
            amending_info = act_info(item, {
                'en': en.info.get('title', ''),
                'el': el.info.get('title', ''),
            })
            amending_info.status = 'official_text'
            amending_info.relations.append(('amends', info.uid))
            amending_info.amendments.append(base_amendment(amending_info, item.get('note', '')))
            used = history.used.get(item['celex'], {})
            for (provision, kind), (marker, applies_from) in sorted(used.items()):
                if provision not in known[uid]:
                    report.append('%s: положения %s нет в разборе OJ' % (uid, provision))
                amending_info.amendments.append(texts_xml.AmendmentInfo(
                    key='%s:%s' % (provision, kind),
                    provision=provision,
                    kind=kind,
                    published_on=amending_info.published_on,
                    in_force_from=amending_info.in_force_from,
                    applies_from=day(applies_from),
                    marker=marker,
                ))
            merge_links(amending_info, root_dir / uid / 'act.xml')
            defaults = amended_acts(own, uid)
            counts = write(root_dir / uid, amending_info, own, known, defaults)
            self.stdout.write('%s: фрагментов %d, изменений %d' % (
                uid, len(own), len(amending_info.amendments),
            ))
            for lang, found in counts.items():
                self.stdout.write('  ссылок %s: %s' % (lang, ref_summary(found)))

        self.stdout.write('Отчёт (%d):' % len(report))
        for line in report:
            self.stdout.write('  ' + line)

    def parse(self, path, lang, report):
        if not path.exists():
            raise CommandError('нет файла %s' % path)
        document = formex.parse(path, lang)
        report.extend('%s: %s' % (path.name, line) for line in document.report)
        return document
