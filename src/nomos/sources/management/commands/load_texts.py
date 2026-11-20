"""Загрузка Nomos-texts в БД: все act.xml и тексты языков, БД пересобирается целиком."""

import datetime
import hashlib
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from nomos.acts.models import (
    Act,
    ActRelation,
    Amendment,
    Expression,
    Fragment,
    FragmentText,
    FragmentVersion,
)
from nomos.sources.cylaw import history, texts_xml
from nomos.sources.models import Source
from nomos.translate.models import Note

SOURCES = {
    'cylaw': 'CyLaw — сводные тексты законов Кипра',
    'cy-gazette': 'Επίσημη Εφημερίδα της Δημοκρατίας',
}


def date(value):
    if not value:
        return None
    return datetime.date.fromisoformat(value)


def text_hash(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


class Loader:
    def __init__(self, root_dir, stdout):
        self.root_dir = root_dir
        self.stdout = stdout
        self.acts = {}
        self.fragments = {}
        self.amendments = {}
        self.elements = []

    def run(self):
        paths = sorted(
            path for path in self.root_dir.rglob('act.xml')
            if 'sources' not in path.relative_to(self.root_dir).parts
        )
        sources = {
            code: Source.objects.get_or_create(code=code, defaults={'name': name})[0]
            for code, name in SOURCES.items()
        }
        for path in paths:
            self.load_act(path, sources)
        for element in self.elements:
            self.load_amendments(element)
        for element in self.elements:
            self.load_relations(element)
        for path, element in zip(paths, self.elements):
            self.load_versions(path, element)
        self.stdout.write(
            'актов %d, фрагментов %d, редакций %d, текстов %d, примечаний %d' % (
                Act.objects.count(),
                Fragment.objects.count(),
                FragmentVersion.objects.count(),
                FragmentText.objects.count(),
                Note.objects.count(),
            )
        )

    def load_act(self, path, sources):
        element = texts_xml.read_act(path)
        act = Act.objects.create(
            uid=element.get('uid'),
            jurisdiction=element.get('jurisdiction'),
            kind=element.get('kind'),
            number=element.get('number', ''),
            year=element.get('year', ''),
            published_on=date(element.get('published-on')),
            published_in=element.get('published-in', ''),
            in_force_from=date(element.get('in-force-from')),
            structure_lang=element.get('structure-lang', ''),
        )
        self.acts[act.uid] = act
        self.elements.append(element)
        for item in element.iter('expression'):
            Expression.objects.create(
                act=act,
                lang=item.get('lang'),
                status=item.get('status'),
                source=sources.get(item.get('source')),
                title=item.get('title', ''),
                short_title=item.get('short-title', ''),
                file=item.get('file', ''),
            )
        for item in element.iter('fragment'):
            parent = item.get('parent')
            fragment = Fragment.objects.create(
                uid=item.get('uid'),
                act=act,
                parent=self.fragments.get((act.uid, parent)) if parent else None,
                kind=item.get('kind'),
                eid=item.get('eid'),
                order_key=item.get('order-key', ''),
            )
            self.fragments[(act.uid, fragment.eid)] = fragment

    def load_amendments(self, element):
        act = self.acts[element.get('uid')]
        for item in element.iter('amendment'):
            provision = item.get('provision')
            amendment = Amendment.objects.create(
                amending_act=act,
                amending_fragment=self.fragments.get((act.uid, provision)),
                kind=item.get('kind'),
                published_on=date(item.get('published-on')),
                in_force_from=date(item.get('in-force-from')),
                applies_from=date(item.get('applies-from')),
                key=item.get('key'),
                note=item.text or '',
            )
            self.amendments['%s#%s' % (act.uid, amendment.key)] = amendment

    def load_relations(self, element):
        act = self.acts[element.get('uid')]
        for item in element.iter('relation'):
            target = self.acts.get(item.get('to'))
            if target is None:
                raise CommandError('%s: нет акта %s' % (act.uid, item.get('to')))
            ActRelation.objects.create(from_act=act, to_act=target, kind=item.get('kind'))

    def load_versions(self, path, element):
        act = self.acts[element.get('uid')]
        expressions = {expression.lang: expression for expression in act.expressions.all()}
        texts = {
            lang: texts_xml.read_texts(path.parent / expression.file)
            for lang, expression in expressions.items()
        }
        for item in element.iter('fragment'):
            fragment = self.fragments[(act.uid, item.get('eid'))]
            for version_item in item.iter('version'):
                ref = version_item.get('amendment')
                if ref not in self.amendments:
                    raise CommandError('%s: нет изменения %s' % (fragment.eid, ref))
                version = FragmentVersion.objects.create(
                    fragment=fragment,
                    key=version_item.get('key'),
                    amendment=self.amendments[ref],
                    num=version_item.get('num', ''),
                    heading=version_item.get('heading', ''),
                    state=version_item.get('state') or FragmentVersion.State.ACTIVE,
                    valid_from=date(version_item.get('valid-from')),
                    applies_from=date(version_item.get('applies-from')),
                    known_from=(
                        date(version_item.get('known-from')) or history.BASE_KNOWN_FROM
                    ),
                )
                for lang, expression in expressions.items():
                    found = texts[lang].get((fragment.uid, version.key))
                    if found is not None:
                        self.load_text(version, expression, *found)

    def load_text(self, version, expression, body, notes, unknown):
        text = FragmentText.objects.create(
            version=version,
            expression=expression,
            body=body,
            text_hash=text_hash(body),
        )
        for note in notes:
            Note.objects.create(
                target=text,
                kind=note.get('kind'),
                origin=note.get('origin', Note.Origin.HUMAN),
                status=note.get('status', Note.Status.OPEN),
                body=(note.text or '').strip(),
            )


class Command(BaseCommand):
    help = 'Пересобрать БД из XML Nomos-texts'

    def add_arguments(self, parser):
        parser.add_argument(
            '--texts-dir',
            default=os.environ.get('NOMOS_TEXTS_DIR', ''),
            help='папка Nomos-texts (по умолчанию NOMOS_TEXTS_DIR)',
        )

    def handle(self, texts_dir, **options):
        if not texts_dir:
            raise CommandError('не задана папка текстов: --texts-dir или NOMOS_TEXTS_DIR')
        with transaction.atomic():
            # редакции защищают изменения от удаления: сначала они
            FragmentVersion.objects.all().delete()
            Amendment.objects.all().delete()
            Act.objects.all().delete()
            Loader(Path(texts_dir), self.stdout).run()
