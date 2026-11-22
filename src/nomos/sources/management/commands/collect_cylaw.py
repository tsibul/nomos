"""Сборка закона Кипра из PDF в XML Nomos-texts.

Сводный текст CyLaw и изменяющие акты из Επίσημη Εφημερίδα перечислены в манифесте
`<акт>/collect.xml`. Пишутся act.xml и el.xml закона и каждого изменяющего акта;
в конце — отчёт о том, что не снято однозначно.
"""

import datetime
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from nomos.sources.cylaw import consolidated, gazette, history, layout, refs, texts_xml
from nomos.sources.cylaw import acts as acts_index

BASE_KEY = history.BASE_KEY


def base_version(info):
    return history.Version(
        key=history.BASE_KEY,
        amendment='%s#%s' % (info.uid, BASE_KEY),
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


def act_reference(text):
    """«104(Ι)/2025», «Κ.Δ.Π. 102/2026» → «104(I)/2025», «102/2026» без пробелов."""
    return ''.join(text.replace('Ι', 'I').split())


def split_notes(notes, acts):
    """Примечания CyLaw «Σημείωση N του Ν. …» → ({uid акта: текст}, прочие).

    Примечание относится к акту, чей номер стоит в его заголовке (до «Έναρξη»)."""
    own = {}
    other = []
    for note in notes:
        heading = act_reference(note.split('Έναρξη')[0].split('Αναδρομική')[0])
        for act in acts:
            if '%s/%s' % (act_reference(act.number), act.year) in heading:
                own[act.uid] = (own.get(act.uid, '') + ' ' + note).strip()
                break
        else:
            other.append(note)
    return own, other


def law_info(manifest):
    info = texts_xml.ActInfo(
        uid=manifest['uid'],
        jurisdiction=manifest['jurisdiction'],
        kind=manifest['kind'],
        number=manifest['number'],
        year=manifest['year'],
        source=manifest['source'],
        title=manifest['title'],
        # регламент или приказ, собранный из газеты: дата публикации и закон-основание
        published_on=history.parse_date(manifest.get('published')),
        published_in=manifest.get('gazette', ''),
        in_force_from=history.parse_date(manifest.get('in-force') or manifest.get('published')),
        relations=[('based_on', manifest['based-on'])] if manifest.get('based-on') else [],
    )
    info.amendments.append(base_amendment(info))
    return info


def amending_info(act, law_uid, title):
    info = texts_xml.ActInfo(
        uid=act.uid,
        jurisdiction='cy',
        kind=act.kind,
        number=act.number,
        year=act.year,
        published_on=act.published_on,
        published_in=act.published_in,
        in_force_from=act.in_force_from,
        source='cy-gazette',
        title=title,
        relations=[('amends', law_uid)],
    )
    info.amendments.append(base_amendment(info))
    seen = set()
    for op in act.ops:
        if op.key in seen:
            continue
        seen.add(op.key)
        info.amendments.append(texts_xml.AmendmentInfo(
            key=op.key,
            provision=op.provision,
            kind=op.kind if op.kind != 'add' else 'replace',
            published_on=op.known_from,
            in_force_from=op.valid_from,
            applies_from=op.applies_from,
            note=op.note,
        ))
    for renumber in act.renumbered:
        info.amendments.append(texts_xml.AmendmentInfo(
            key=renumber.key,
            provision=renumber.provision,
            kind=history.CORRECT,
            published_on=renumber.correction.published_on,
            in_force_from=act.in_force_from,
            note='%s: αναρίθμηση σε άρθρο %s.' % (
                renumber.correction.published_in, renumber.num),
        ))
    return info


def corrected_version(amending, version, key, published_on):
    """Редакция положения изменяющего акта по его исправлению (Διόρθωση)."""
    return history.Version(
        key=version.valid_from.isoformat() + history.CORRECTED_SUFFIX,
        amendment='%s#%s' % (amending.uid, key),
        valid_from=version.valid_from,
        known_from=published_on,
        paragraphs=list(version.paragraphs),
    )


def apply_corrections(amending, amending_versions, report):
    """Исправления текста и номеров статей самого изменяющего акта."""
    by_eid = {node.eid: (node, versions) for node, versions in amending_versions.items()}
    for renumber in amending.renumbered:
        if renumber.provision not in by_eid:
            report.append('%s#%s: положения нет в разборе акта' % (amending.uid, renumber.key))
            continue
        node, versions = by_eid[renumber.provision]
        version = corrected_version(
            amending, versions[-1], renumber.key, renumber.correction.published_on,
        )
        version.num = renumber.num
        versions.append(version)
    for op in amending.ops:
        if op.kind != history.CORRECT:
            continue
        found = [
            (node, versions)
            for eid, (node, versions) in by_eid.items()
            if history.is_inside(eid, op.provision)
            and '\n'.join(versions[-1].paragraphs).count(op.old) == 1
        ]
        if len(found) != 1:
            report.append('%s: в тексте акта «%s» найдено в %d положениях' % (
                op.ref, op.old, len(found)))
            continue
        node, versions = found[0]
        version = corrected_version(amending, versions[-1], op.key, op.known_from)
        version.paragraphs = history.split(
            '\n'.join(version.paragraphs).replace(op.old, op.new))
        versions.append(version)


def apply_remarks(versions, remarks, base_ref, report):
    """Сомнения и сроки из манифеста: примечания и редакции «утратил силу»."""
    by_eid = {node.eid: node_versions for node, node_versions in versions.items()}
    for target, text, attrib in remarks:
        if target not in by_eid:
            report.append('%s: фрагмента нет в сводном тексте' % target)
            continue
        if attrib.get('kind') == 'doubt':
            for version in by_eid[target]:
                version.notes.append(text)
        if attrib.get('until'):
            day = history.parse_date(attrib['until']) + datetime.timedelta(days=1)
            for eid, node_versions in by_eid.items():
                if eid == target or eid.startswith(target + '.'):
                    node_versions.append(expired_version(day, base_ref))


def known_from_publication(versions, published_on, base_ref):
    """Акт, опубликованный после 01.01.2024 (приказ ΚΔΠ 378/2025), известен с публикации."""
    if published_on is None or published_on <= history.BASE_KNOWN_FROM:
        return
    for node_versions in versions.values():
        for version in node_versions:
            if version.amendment == base_ref:
                version.known_from = max(version.known_from, published_on)


def expired_version(day, base_ref):
    return history.Version(
        key=day.isoformat(),
        amendment=base_ref,
        valid_from=day,
        applies_from=day,
        state='repealed',
        paragraphs=[],
    )


def plain_texts(node_versions):
    """{редакция: текст без разметки и примечаний}: для сверки с прежним el.xml."""
    return {
        version.key: ' '.join(
            texts_xml.plain(layout.strip_notes(paragraph, []))
            for paragraph in version.paragraphs
        )
        for version in node_versions
        if version.paragraphs
    }


def write(folder, info, versions, notes=(), marker=None):
    """act.xml, el.xml и cylaw-notes.xml; примечания редакции CyLaw в загружаемое не идут.

    marker — разметка ссылок; его found: {(uid, редакция): [цели]}."""
    act_path = folder / 'act.xml'
    uids = texts_xml.assign_uids(info, versions.keys(), act_path)
    previous = texts_xml.old_texts(folder / 'el.xml')
    current = {node: plain_texts(node_versions) for node, node_versions in versions.items()}
    nums = {node: node.num for node in versions}
    eids = {uid: eid for eid, uid in texts_xml.existing_uids(act_path).items()}
    uids = texts_xml.follow_texts(info, uids, current, previous, nums, eids)
    lost = texts_xml.lost_uids(folder, uids)
    merged = texts_xml.merged_uids(uids, current, previous, [uid for _, uid in lost])
    joined = {uid for _, uid in merged}
    lost = [(eid, uid) for eid, uid in lost if uid not in joined]
    headings = {
        node: {version.key: version.heading or node.heading for version in node_versions}
        for node, node_versions in versions.items()
    }
    headed = texts_xml.headed_uids(uids, headings, previous, [uid for _, uid in lost])
    moved = {uid for uid, _, _ in headed}
    lost = [(eid, uid) for eid, uid in lost if uid not in moved]
    if lost:
        raise CommandError('%s: пересборка убирает фрагменты с переводом: %s' % (
            info.uid, ', '.join('%s (%s)' % item for item in lost),
        ))
    texts_xml.merge_translations(folder, merged)
    texts_xml.move_to_headings(folder, headed)
    texts_xml.write_act(act_path, info, versions, uids)
    inline = []
    for node, node_versions in versions.items():
        for version in node_versions:
            for paragraph in version.paragraphs or []:
                found = []
                layout.strip_notes(paragraph, found)
                inline.extend((uids[node], version.key, note) for note in found)
    mark = None
    if marker is not None:
        def mark(node, version, paragraphs):
            marked, found = marker.mark_text(paragraphs, node.eid)
            marker.found[(uids[node], version.key)] = found
            return marked
    texts_xml.write_texts(
        folder / 'el.xml', info, versions, uids,
        lambda paragraph: layout.strip_notes(paragraph, []),
        mark,
    )
    if notes or inline:
        texts_xml.write_notes(folder / 'cylaw-notes.xml', info, notes, inline)
    return len(inline)


def mark_translation(folder, marker, prefix=''):
    """Ссылки в ru.xml акта теми же правилами; строки отчёта."""
    lines = ['%sссылок el: %d; не размечено el: %s' % (
        prefix,
        sum(len(found) for found in marker.found.values()),
        skipped_text(marker.skipped),
    )]
    path = folder / 'ru.xml'
    if not path.exists():
        return lines
    eid_by_uid = {uid: eid for eid, uid in texts_xml.existing_uids(folder / 'act.xml').items()}
    mentions = marker.mentions.translated() if marker.mentions is not None else None
    skipped, differences, total = refs.mark_translation(
        path, marker.targets, eid_by_uid, marker.found, marker.act, marker.implicit, mentions,
    )
    lines.append('%sссылок ru: %d; не размечено ru: %s' % (
        prefix, total, skipped_text(skipped),
    ))
    lines.append('%sтекстов, где цели el и ru расходятся: %d' % (prefix, len(differences)))
    for eid, key, el_targets, ru_targets in differences[:5]:
        lines.append('  %s (%s): el %s, ru %s' % (eid, key, el_targets, ru_targets))
    return lines


def skipped_text(skipped):
    return ', '.join('%s — %d' % item for item in sorted(skipped.items())) or 'нет'


class Command(BaseCommand):
    help = 'PDF закона и изменяющих актов → XML в Nomos-texts по манифесту collect.xml'

    def add_arguments(self, parser):
        parser.add_argument('act', help='адрес акта, например cy/law/2000/95_I')
        parser.add_argument(
            '--texts-dir',
            default=os.environ.get('NOMOS_TEXTS_DIR', ''),
            help='папка Nomos-texts (по умолчанию NOMOS_TEXTS_DIR)',
        )

    def handle(self, act, texts_dir, **options):
        if not texts_dir:
            raise CommandError('не задана папка текстов: --texts-dir или NOMOS_TEXTS_DIR')
        root_dir = Path(texts_dir)
        folder = root_dir / act
        manifest, acts, remarks = history.read_manifest(folder / 'collect.xml')
        report = []

        info = law_info(manifest)
        if manifest.get('layout') == 'amending':
            self.collect_amending(root_dir, manifest['file'], folder, info, remarks, report)
            return
        tree, _, notes = consolidated.parse(
            root_dir / manifest['file'],
            info.uid,
            manifest['source'],
            history.read_splits(folder / 'collect.xml'),
        )
        history.apply_source_fixes(
            tree, history.read_source_fixes(folder / 'collect.xml'), report,
        )
        act_notes, other_notes = split_notes(notes, acts)
        base_ref = '%s#%s' % (info.uid, BASE_KEY)
        versions = history.collect(tree, acts, base_ref, report)
        apply_remarks(versions, remarks, base_ref, report)
        known_from_publication(versions, info.published_on, base_ref)
        # сводка новее 01.01.2024 без изменяющих актов (91(Ι)/2004 на октябрь 2026): её
        # текст известен только с даты сводки
        consolidated_on = history.parse_date(manifest.get('consolidated'))
        known_from_publication(versions, consolidated_on, base_ref)
        index = acts_index.Index.read(root_dir)
        marker = refs.Marker(
            refs.Targets(node.eid for node in versions), refs.EL,
            mentions=acts_index.Mentions(index, 'el', info.uid),
        )
        inline = write(folder, info, versions, other_notes, marker)
        report.extend(mark_translation(folder, marker))
        self.stdout.write('%s: фрагментов %d' % (info.uid, len(versions)))
        report.append('примечаний CyLaw в cylaw-notes.xml: %d к закону, %d [Σ.Σ.], %d у '
                      'изменяющих актов (в их папках)' % (
                          len(other_notes), inline, len(act_notes)))

        for amending in acts:
            if not amending.file:
                # текста акта нет: только сведения и изменения, без фрагментов
                write(
                    root_dir / amending.uid,
                    amending_info(amending, info.uid, amending.title),
                    {},
                )
                report.append('%s: текста акта нет, изменения — по манифесту' % amending.uid)
                continue
            amending_tree, title = gazette.parse(root_dir / amending.file, amending.uid)
            amending_versions = {}
            for node in amending_tree.walk():
                if node.kind == 'act':
                    continue
                version = base_version(amending)
                version.valid_from = amending.in_force_from
                version.known_from = amending.published_on
                version.paragraphs = list(node.paragraphs)
                amending_versions[node] = [version]
            apply_corrections(amending, amending_versions, report)
            # ссылки на базовый закон; у ΚΔΠ своих статей нет — без указания акта тоже он
            amending_marker = refs.Marker(
                marker.targets, refs.EL, info.uid, implicit=amending.kind != 'law',
                mentions=acts_index.Mentions(index, 'el', amending.uid),
            )
            write(
                root_dir / amending.uid,
                amending_info(amending, info.uid, title),
                amending_versions,
                [act_notes[amending.uid]] if amending.uid in act_notes else [],
                amending_marker,
            )
            report.extend(mark_translation(
                root_dir / amending.uid, amending_marker, amending.uid + ': ',
            ))
            provisions = {node.eid for node in amending_versions}
            for op in amending.ops:
                if op.provision not in provisions:
                    report.append('%s: положения нет в разборе акта' % op.ref)

        self.write_report(remarks, report)

    def collect_amending(self, root_dir, file, folder, info, remarks, report):
        """Изменяющий акт, чьего базового закона в базе нет (layout="amending"):
        PDF газеты разбирается как изменяющий акт, ссылки — только на акты из указателя."""
        tree, title = gazette.parse(root_dir / file, info.uid)
        if not info.title:
            info.title = title
        versions = {}
        for node in tree.walk():
            if node.kind == 'act':
                continue
            version = base_version(info)
            version.valid_from = info.in_force_from
            version.known_from = info.published_on
            version.paragraphs = list(node.paragraphs)
            versions[node] = [version]
        base_ref = '%s#%s' % (info.uid, BASE_KEY)
        apply_remarks(versions, remarks, base_ref, report)
        index = acts_index.Index.read(root_dir)
        marker = refs.Marker(
            refs.Targets([]), refs.EL,
            mentions=acts_index.Mentions(index, 'el', info.uid),
        )
        write(folder, info, versions, [], marker)
        report.extend(mark_translation(folder, marker))
        self.stdout.write('%s: фрагментов %d' % (info.uid, len(versions)))
        self.write_report(remarks, report)

    def write_report(self, remarks, report):
        for target, text, _ in remarks:
            report.append('%s: %s' % (target, text))
        self.stdout.write('Отчёт (%d):' % len(report))
        for line in report:
            self.stdout.write('  ' + line)
