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
            published_on=act.published_on,
            in_force_from=op.valid_from,
            applies_from=op.applies_from,
            note=op.note,
        ))
    return info


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


def expired_version(day, base_ref):
    return history.Version(
        key=day.isoformat(),
        amendment=base_ref,
        valid_from=day,
        applies_from=day,
        state='repealed',
        paragraphs=[],
    )


def write(folder, info, versions, notes=(), marker=None):
    """act.xml, el.xml и cylaw-notes.xml; примечания редакции CyLaw в загружаемое не идут.

    marker — разметка ссылок (только у закона); его found: {(uid, редакция): [цели]}."""
    act_path = folder / 'act.xml'
    uids = texts_xml.assign_uids(info, versions.keys(), act_path)
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


def mark_translation(folder, marker):
    """Ссылки в ru.xml закона теми же правилами; строки отчёта."""
    lines = ['ссылок el: %d; не размечено el: %s' % (
        sum(len(found) for found in marker.found.values()), skipped_text(marker.skipped),
    )]
    path = folder / 'ru.xml'
    if not path.exists():
        return lines
    eid_by_uid = {uid: eid for eid, uid in texts_xml.existing_uids(folder / 'act.xml').items()}
    skipped, differences, total = refs.mark_translation(
        path, marker.targets, eid_by_uid, marker.found,
    )
    lines.append('ссылок ru: %d; не размечено ru: %s' % (total, skipped_text(skipped)))
    lines.append('текстов, где цели el и ru расходятся: %d' % len(differences))
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
        tree, _, notes = consolidated.parse(root_dir / manifest['file'], info.uid)
        act_notes, other_notes = split_notes(notes, acts)
        base_ref = '%s#%s' % (info.uid, BASE_KEY)
        versions = history.collect(tree, acts, base_ref, report)
        apply_remarks(versions, remarks, base_ref, report)
        marker = refs.Marker(refs.Targets(node.eid for node in versions), refs.EL)
        inline = write(folder, info, versions, other_notes, marker)
        report.extend(mark_translation(folder, marker))
        self.stdout.write('%s: фрагментов %d' % (info.uid, len(versions)))
        report.append('примечаний CyLaw в cylaw-notes.xml: %d к закону, %d [Σ.Σ.], %d у '
                      'изменяющих актов (в их папках)' % (
                          len(other_notes), inline, len(act_notes)))

        for amending in acts:
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
            write(
                root_dir / amending.uid,
                amending_info(amending, info.uid, title),
                amending_versions,
                [act_notes[amending.uid]] if amending.uid in act_notes else [],
            )
            provisions = {node.eid for node in amending_versions}
            for op in amending.ops:
                if op.provision not in provisions:
                    report.append('%s: положения нет в разборе акта' % op.ref)

        for target, text, _ in remarks:
            report.append('%s: %s' % (target, text))
        self.stdout.write('Отчёт (%d):' % len(report))
        for line in report:
            self.stdout.write('  ' + line)
