"""Загрузка Nomos-texts в БД: все act.xml и тексты языков, БД пересобирается
целиком."""

import datetime
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from nomos.acts import grouping
from nomos.acts.models import (
    Act,
    ActRelation,
    Amendment,
    Expression,
    Fragment,
    FragmentText,
    FragmentVersion,
    NavNode,
    Reference,
)
from nomos.sources.cylaw import history, texts_xml
from nomos.sources.models import Source
from nomos.sources.translation_check import missing
from nomos.translate.models import Note

# код → поля Source
SOURCES = {
    # условия cylaw.org: копирование — при указании источника (отчёт юриста,
    # раздел 6);
    # точный адрес страницы условий не выяснен — корень сайта
    "cylaw": {
        "name": "CyLaw — неофициальная сводная редакция",
        "url": "https://www.cylaw.org",
        "publisher": "ΚΙΝΟΠ",
        "terms_url": "https://www.cylaw.org",
        "attribution": (
            "Сводная редакция CyLaw (www.cylaw.org, ΚΙΝΟΠ), неофициальная"
        ),
    },
    # leginet.eu: на каждой странице PDF «Copyright © Leginet Ltd … All rights
    # reserved»;
    # условия использования не выяснены (125(Ι)/1999)
    "leginet": {
        "name": "Leginet — неофициальная сводная редакция",
        "url": "https://www.leginet.eu",
        "publisher": "Leginet Ltd",
        "attribution": (
            "Сводная редакция Leginet (www.leginet.eu), неофициальная"
        ),
    },
    # сайт Государственной типографии, где выходит газета
    # (docs/research/vat-sources.md)
    "cy-gazette": {
        "name": "Официальная газета Республики Кипр",
        "url": "https://www.mof.gov.cy/mof/gpo/",
    },
    # правовое уведомление EUR-Lex: сводные тексты — CC BY 4.0, указать
    # источник и изменения;
    # сводный текст документальный, аутентичен только текст в OJ
    "eurlex": {
        "name": "EUR-Lex — сводный текст (не аутентичен; аутентичен OJ)",
        "publisher": "Publications Office of the European Union",
        "license": "CC BY 4.0",
        "terms_url": "https://eur-lex.europa.eu/content/legal-notice/legal-notice.html",
        "attribution": (
            "© European Union, https://eur-lex.europa.eu; сводный текст без "
            "юридической силы,"
            " аутентичен текст в Official Journal; CC BY 4.0"
        ),
    },
    # сайт Τμήμα Φορολογίας на gov.cy; условия использования не выяснены
    "cy-tax": {
        "name": "Τμήμα Φορολογίας (Tax Department), gov.cy",
        "url": "https://www.gov.cy/mof-tax/documents/ypovoli-tropopoiisi-diloseon-f-p-a/",
        "publisher": "Τμήμα Φορολογίας Κύπρου",
        "attribution": "Τμήμα Φορολογίας Κύπρου, www.gov.cy",
    },
    # сводки Τμήμα Τελωνείων (Таможенный департамент) — 94(Ι)/2004, 91(Ι)/2004;
    # условия
    # использования на страницах законов не найдены; в сводке: «Η ενοποίηση
    # αυτή δεν έχει
    # επίσημο χαρακτήρα» — Τμήμα Τελωνείων не отвечает за ошибки и пропуски
    "cy-customs": {
        "name": "Τμήμα Τελωνείων (Customs Department), сводная редакция",
        "url": "https://www.mof.gov.cy/mof/customs/customs.nsf",
        "publisher": "Τμήμα Τελωνείων Κύπρου",
        "attribution": (
            "Τμήμα Τελωνείων Κύπρου, сводная редакция, неофициальная"
        ),
    },
    # бланки Υπηρεσίες Κοινωνικών Ασφαλίσεων (cy/form/yka/…); адрес — из самих
    # бланков,
    # условия использования не выяснены
    "cy-sis": {
        "name": "Υπηρεσίες Κοινωνικών Ασφαλίσεων (Social Insurance Services)",
        "url": "https://www.mlsi.gov.cy/sid",
        "publisher": "Υπηρεσίες Κοινωνικών Ασφαλίσεων Κύπρου",
        "attribution": (
            "Υπηρεσίες Κοινωνικών Ασφαλίσεων Κύπρου, www.mlsi.gov.cy/sid"
        ),
    },
    # собственный перевод проекта, не внешний сайт (LICENSE-CONTENT)
    "nomos": {
        "name": "Неофициальный перевод",
        "publisher": "Nomos",
        "license": "CC BY 4.0",
        "terms_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Неофициальный перевод проекта Nomos, CC BY 4.0",
    },
}


# <ref id="r1" to="art_5.par_2">: to — eid в том же акте, «uid акта#eid» или
# «uid акта»
REF_RE = re.compile(r"<ref\b([^>]*)>")
REF_ATTR_RE = re.compile(r'\b(id|to)="([^"]*)"')


def date(value):
    if not value:
        return None
    return datetime.date.fromisoformat(value)


def text_hash(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Loader:
    def __init__(self, root_dir, stdout, skip_incomplete=False):
        self.root_dir = root_dir
        self.stdout = stdout
        # загружать только акты с полным ru (NOMOS_PUBLIC_ONLY); uid
        # пропущенных
        self.skip_incomplete = skip_incomplete
        self.skipped = set()
        self.acts = {}
        self.fragments = {}
        self.amendments = {}
        self.elements = []
        # (текст, id, to): цели ищутся, когда загружены фрагменты всех актов
        self.references = []

    def run(self):
        paths = texts_xml.act_paths(self.root_dir)
        if self.skip_incomplete:
            paths = self.complete_paths(paths)
        sources = {
            code: Source.objects.update_or_create(code=code, defaults=fields)[
                0
            ]
            for code, fields in SOURCES.items()
        }
        for path in paths:
            self.load_act(path, sources)
        for element in self.elements:
            self.load_amendments(element)
        for element in self.elements:
            self.load_relations(element)
        for path, element in zip(paths, self.elements):
            self.load_versions(path, element)
        self.load_references()
        self.load_nav()
        self.stdout.write(
            "актов %d, фрагментов %d, редакций %d, текстов %d, примечаний %d, "
            "ссылок %d"
            % (
                Act.objects.count(),
                Fragment.objects.count(),
                FragmentVersion.objects.count(),
                FragmentText.objects.count(),
                Note.objects.count(),
                Reference.objects.count(),
            )
        )

    def complete_paths(self, paths):
        """act.xml актов с полным ru; пропущенные — в self.skipped, их число —
        в вывод."""
        found = []
        for path in paths:
            element = texts_xml.read_act(path)
            if missing(element, path.parent) == []:
                found.append(path)
            else:
                self.skipped.add(element.get("uid"))
        self.stdout.write(
            "не загружено актов без полного ru: %d" % len(self.skipped)
        )
        return found

    def load_act(self, path, sources):
        element = texts_xml.read_act(path)
        act = Act.objects.create(
            uid=element.get("uid"),
            jurisdiction=element.get("jurisdiction"),
            kind=element.get("kind"),
            number=element.get("number", ""),
            year=element.get("year", ""),
            published_on=date(element.get("published-on")),
            published_in=element.get("published-in", ""),
            in_force_from=date(element.get("in-force-from")),
            structure_lang=element.get("structure-lang", ""),
            date_adopted=date(element.get("date-adopted")),
            celex=element.get("celex", ""),
            eli=element.get("eli", ""),
        )
        self.acts[act.uid] = act
        self.elements.append(element)
        for item in element.iter("expression"):
            Expression.objects.create(
                act=act,
                lang=item.get("lang"),
                status=item.get("status"),
                source=sources.get(item.get("source")),
                title=item.get("title", ""),
                short_title=item.get("short-title", ""),
                file=item.get("file", ""),
                public=item.get("public") == "true",
                url=item.get("url", ""),
            )
        for item in element.iter("fragment"):
            parent = item.get("parent")
            fragment = Fragment.objects.create(
                uid=item.get("uid"),
                act=act,
                parent=self.fragments.get((act.uid, parent))
                if parent
                else None,
                kind=item.get("kind"),
                eid=item.get("eid"),
                order_key=item.get("order-key", ""),
            )
            self.fragments[(act.uid, fragment.eid)] = fragment

    def load_amendments(self, element):
        act = self.acts[element.get("uid")]
        for item in element.iter("amendment"):
            provision = item.get("provision")
            amendment = Amendment.objects.create(
                amending_act=act,
                amending_fragment=self.fragments.get((act.uid, provision)),
                kind=item.get("kind"),
                published_on=date(item.get("published-on")),
                in_force_from=date(item.get("in-force-from")),
                applies_from=date(item.get("applies-from")),
                key=item.get("key"),
                marker=item.get("marker", ""),
                note=item.text or "",
            )
            self.amendments["%s#%s" % (act.uid, amendment.key)] = amendment

    def load_relations(self, element):
        act = self.acts[element.get("uid")]
        for item in element.iter("relation"):
            target = self.acts.get(item.get("to"))
            if target is None and item.get("to") in self.skipped:
                continue
            if target is None:
                raise CommandError(
                    "%s: нет акта %s" % (act.uid, item.get("to"))
                )
            ActRelation.objects.create(
                from_act=act, to_act=target, kind=item.get("kind")
            )

    def load_nav(self):
        """Каркас меню из nav.xml; затем — акты, которые в меню не попали, для
        проверки."""
        path = self.root_dir / "nav.xml"
        if not path.exists():
            self.stdout.write("нет nav.xml: меню пусто")
            return
        self.nav_order = 0
        for element in ET.parse(path).getroot():
            self.load_nav_node(element, None)
        missing = NavNode.objects.exclude(act_uid="").filter(act__isnull=True)
        for node in missing:
            self.stdout.write("в меню, но не загружен: %s" % node.act_uid)
        loose = grouping.unattached()
        self.stdout.write("актов без связи с основным актом: %d" % len(loose))
        for act in loose:
            self.stdout.write("  %s" % act.uid)

    def load_nav_node(self, element, parent):
        # <changes> раздела «Аналитика» — страницы на дату, пока не делаются
        if element.tag not in ("section", "item"):
            return
        self.nav_order += 1
        uid = element.get("act", "")
        node = NavNode.objects.create(
            parent=parent,
            key=element.get("key", ""),
            label=element.get("label", ""),
            order=self.nav_order,
            act_uid=uid,
            act=self.acts.get(uid),
        )
        for child in element:
            self.load_nav_node(child, node)

    def load_versions(self, path, element):
        act = self.acts[element.get("uid")]
        expressions = {
            expression.lang: expression for expression in act.expressions.all()
        }
        texts = {
            lang: texts_xml.read_texts(path.parent / expression.file)
            for lang, expression in expressions.items()
        }
        for item in element.iter("fragment"):
            fragment = self.fragments[(act.uid, item.get("eid"))]
            for version_item in item.iter("version"):
                ref = version_item.get("amendment")
                if ref not in self.amendments:
                    raise CommandError(
                        "%s: нет изменения %s" % (fragment.eid, ref)
                    )
                version = FragmentVersion.objects.create(
                    fragment=fragment,
                    key=version_item.get("key"),
                    amendment=self.amendments[ref],
                    num=version_item.get("num", ""),
                    heading=version_item.get("heading", ""),
                    state=version_item.get("state")
                    or FragmentVersion.State.ACTIVE,
                    valid_from=date(version_item.get("valid-from")),
                    applies_from=date(version_item.get("applies-from")),
                    known_from=(
                        date(version_item.get("known-from"))
                        or history.BASE_KNOWN_FROM
                    ),
                )
                for lang, expression in expressions.items():
                    found = texts[lang].get((fragment.uid, version.key))
                    if found is not None:
                        self.load_text(version, expression, *found)

    def load_text(self, version, expression, body, notes, unknown, heading):
        text = FragmentText.objects.create(
            version=version,
            expression=expression,
            body=body,
            heading=heading,
            text_hash=text_hash(body),
        )
        for match in REF_RE.finditer(body):
            found = dict(REF_ATTR_RE.findall(match.group(1)))
            self.references.append(
                (text, found.get("id", ""), found.get("to", ""))
            )
        for note in notes:
            Note.objects.create(
                target=text,
                kind=note.get("kind"),
                origin=note.get("origin", Note.Origin.HUMAN),
                status=note.get("status", Note.Status.OPEN),
                body=(note.text or "").strip(),
            )

    def load_references(self):
        fragments = {
            (act_uid, eid): fragment_id
            for fragment_id, act_uid, eid in Fragment.objects.values_list(
                "id", "act__uid", "eid"
            )
        }
        for text, anchor, target in self.references:
            act_uid = text.version.fragment.act.uid
            eid = target
            if "/" in target:
                act_uid, _, eid = target.partition("#")
            act = self.acts.get(act_uid)
            fragment_id = fragments.get((act_uid, eid)) if eid else None
            Reference.objects.create(
                from_text=text,
                anchor=anchor,
                to_act=act,
                to_fragment_id=fragment_id,
                resolved=act is not None
                and (not eid or fragment_id is not None),
            )


def add_texts_dir(parser):
    """Параметр --texts-dir команды: папка Nomos-texts, по умолчанию
    NOMOS_TEXTS_DIR."""
    parser.add_argument(
        "--texts-dir",
        default=os.environ.get("NOMOS_TEXTS_DIR", ""),
        help="папка Nomos-texts (по умолчанию NOMOS_TEXTS_DIR)",
    )


def texts_root(texts_dir):
    """Папка текстов из параметра --texts-dir; не задана — ошибка команды."""
    if not texts_dir:
        raise CommandError(
            "не задана папка текстов: --texts-dir или NOMOS_TEXTS_DIR"
        )
    return Path(texts_dir)


class Command(BaseCommand):
    help = "Пересобрать БД из XML Nomos-texts"

    def add_arguments(self, parser):
        add_texts_dir(parser)

    def handle(self, texts_dir, **options):
        root = texts_root(texts_dir)
        with transaction.atomic():
            # редакции защищают изменения от удаления: сначала они
            FragmentVersion.objects.all().delete()
            NavNode.objects.all().delete()
            Amendment.objects.all().delete()
            Act.objects.all().delete()
            Loader(root, self.stdout, settings.NOMOS_PUBLIC_ONLY).run()
