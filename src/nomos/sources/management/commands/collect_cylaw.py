"""Сборка закона Кипра из PDF в XML Nomos-texts.

Сводный текст CyLaw и изменяющие акты из Επίσημη Εφημερίδα перечислены в манифесте
`<акт>/collect.xml`. Пишутся act.xml и el.xml закона и каждого изменяющего акта;
в конце — отчёт о том, что не снято однозначно.
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from nomos.sources.cylaw import consolidated, gazette, history, layout, texts_xml

BASE_MARKER = 'base'


def base_version(info):
    return history.Version(
        key=history.BASE_KEY,
        amendment='%s#%s' % (info.uid, BASE_MARKER),
    )


def base_amendment(info):
    return texts_xml.AmendmentInfo(
        marker=BASE_MARKER,
        provision='',
        kind='base',
        published_on=info.published_on,
        in_force_from=info.in_force_from,
    )


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
        if op.marker in seen:
            continue
        seen.add(op.marker)
        info.amendments.append(texts_xml.AmendmentInfo(
            marker=op.marker,
            provision=op.provision,
            kind=op.kind if op.kind != 'add' else 'replace',
            published_on=act.published_on,
            in_force_from=op.valid_from,
            applies_from=op.applies_from,
            note=op.note,
        ))
    return info


def write(folder, info, versions):
    act_path = folder / 'act.xml'
    uids = texts_xml.assign_uids(info, versions.keys(), act_path)
    texts_xml.write_act(act_path, info, versions, uids)
    texts_xml.write_texts(folder / 'el.xml', info, versions, uids, layout.inline_markup)


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
        tree, _ = consolidated.parse(root_dir / manifest['file'], info.uid)
        base_ref = '%s#%s' % (info.uid, BASE_MARKER)
        versions = history.collect(tree, acts, base_ref, report)
        write(folder, info, versions)
        self.stdout.write('%s: фрагментов %d' % (info.uid, len(versions)))

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
            )
            provisions = {node.eid for node in amending_versions}
            for op in amending.ops:
                if op.provision not in provisions:
                    report.append('%s: положения нет в разборе акта' % op.ref)

        for target, text in remarks:
            report.append('%s: %s' % (target, text))
        self.stdout.write('Отчёт (%d):' % len(report))
        for line in report:
            self.stdout.write('  ' + line)
