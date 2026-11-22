import datetime
import filecmp
import os
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from nomos.acts.models import (
    ActRelation, Amendment, Expression, Fragment, FragmentText, FragmentVersion,
    Reference,
)
from nomos.translate.models import Note

from .cylaw import (
    acts, columns, consolidated, customs, gazette, history, layout, leginet, numbering, refs,
    texts_xml,
)
from .management.commands import collect_cylaw
from .cylaw.tree import Node

LAW = 'cy/law/2000/95_I'
TEXTS_DIR = Path(os.environ.get('NOMOS_TEXTS_DIR', '/nonexistent'))


def make_act(uid, published, in_force=None):
    return history.Amending(
        uid=uid,
        kind='kdp',
        number='',
        year='',
        file='',
        published_on=published,
        published_in='',
        in_force_from=in_force or published,
    )


def make_op(act, kind, target='sch_6.par_13', **fields):
    op = history.Op(act=act, provision='art_2', kind=kind, target=target, **fields)
    op.valid_from = op.valid_from or act.in_force_from
    return op


def paragraph(text):
    root = Node(kind='act')
    schedule = root.add(Node(kind='sch', num='6'))
    node = schedule.add(Node(kind='par', num='13'))
    node.paragraphs = [text]
    return node


class ReverseTests(SimpleTestCase):
    def test_replace_twice_back_to_base(self):
        """Определение «αναδιάρθρωση»: 9 → 10 лет (167/2024), 10 → 11 лет (223/2025)."""
        node = paragraph('εντός έντεκα (11) ετών')
        first = make_act('cy/law/2024/167_I', datetime.date(2024, 12, 31))
        second = make_act('cy/law/2025/223_I', datetime.date(2025, 12, 12))
        ops = [
            make_op(first, 'replace', old='εντός εννέα (9) ετών', new='εντός δέκα (10) ετών'),
            make_op(second, 'replace', old='δέκα (10) ετών', new='έντεκα (11) ετών'),
        ]
        report = []
        versions = history.build_versions(node, ops, 'law#base', report)
        self.assertEqual(report, [])
        self.assertEqual(
            [(version.key, version.paragraphs) for version in versions],
            [
                ('base', ['εντός εννέα (9) ετών']),
                ('2024-12-31', ['εντός δέκα (10) ετών']),
                ('2025-12-12', ['εντός έντεκα (11) ετών']),
            ],
        )

    def test_replace_without_old_text_is_unknown(self):
        node = paragraph('νέο κείμενο')
        act = make_act('cy/kdp/2026/103', datetime.date(2026, 2, 27), datetime.date(2026, 9, 1))
        report = []
        versions = history.build_versions(node, [make_op(act, 'replace')], 'law#base', report)
        self.assertIsNone(versions[0].paragraphs)
        self.assertTrue(versions[0].notes)
        self.assertEqual(len(report), 1)


class TemporaryInsertTests(SimpleTestCase):
    def test_two_periods_same_text(self):
        """ΚΔΠ 337/2025 на 2026 год и ΚΔΠ 362/2026 на 2027 год вставляют тот же пункт."""
        node = paragraph('Αυγά')
        first = make_act('cy/kdp/2025/337', datetime.date(2025, 11, 21), datetime.date(2026, 1, 1))
        second = make_act('cy/kdp/2026/362', datetime.date(2026, 10, 16), datetime.date(2027, 1, 1))
        ops = [
            make_op(first, 'insert', until=datetime.date(2026, 12, 31), same=True),
            make_op(second, 'insert', until=datetime.date(2027, 12, 31)),
        ]
        versions = history.build_versions(node, ops, 'law#base', [])
        self.assertEqual(
            [(version.key, version.state, version.paragraphs) for version in versions],
            [
                ('2026-01-01', 'active', ['Αυγά']),
                ('2027-01-01', 'active', ['Αυγά']),
                ('2028-01-01', 'repealed', []),
            ],
        )


class SlugTests(SimpleTestCase):
    def test_greek_term(self):
        self.assertEqual(numbering.slug('«αναδιάρθρωση»'), 'anadiarthrwsh')


@skipUnless((TEXTS_DIR / LAW / 'collect.xml').exists(), 'нет Nomos-texts с законом о НДС')
class VatLawTests(TestCase):
    """Сборка и загрузка закона о НДС из Nomos-texts (NOMOS_TEXTS_DIR)."""

    @classmethod
    def setUpTestData(cls):
        # сборка пишет файлы — в копию, а не в рабочее дерево Nomos-texts
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        call_command('collect_cylaw', LAW, texts_dir=str(cls.copy), stdout=output)
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def test_rebuild_keeps_files_and_translation(self):
        """Пересборка и две загрузки подряд: файлы как в git, русский перевод на месте."""
        changed = [
            str(path.relative_to(self.copy))
            for path in self.copy.rglob('*')
            if path.is_file()
            and not filecmp.cmp(path, TEXTS_DIR / path.relative_to(self.copy), shallow=False)
        ]
        self.assertEqual(changed, [])
        russian = Expression.objects.get(act__uid=LAW, lang='ru')
        self.assertEqual(russian.source.code, 'nomos')
        self.assertEqual(russian.source.name, 'Неофициальный перевод')
        self.assertTrue(FragmentText.objects.filter(expression__lang='ru').exists())

    def test_rebuild_keeps_uids(self):
        """Множество uid 95(Ι)/2000 после пересборки — прежнее."""
        before = set(texts_xml.existing_uids(TEXTS_DIR / LAW / 'act.xml').values())
        after = set(texts_xml.existing_uids(self.copy / LAW / 'act.xml').values())
        self.assertEqual(after, before)

    def test_short_titles_kept(self):
        short = dict(
            Expression.objects.filter(act__uid=LAW).values_list('lang', 'short_title')
        )
        self.assertEqual(short, {'el': 'Ο περί ΦΠΑ Νόμος', 'ru': 'Закон о НДС'})

    def test_no_cylaw_editorial_notes_loaded(self):
        """Тексты редакции CyLaw («Σημείωση…», [Σ.Σ.: …]) остаются в cylaw-notes.xml."""
        self.assertFalse(Amendment.objects.filter(note__contains='Σημείωση').exists())
        self.assertFalse(FragmentText.objects.filter(body__contains='source_comment').exists())
        self.assertFalse(Note.objects.filter(kind=Note.Kind.SOURCE_COMMENT).exists())

    def test_titles_without_hyphen_breaks(self):
        titles = Expression.objects.filter(title__regex=r'[α-ω]- [α-ω]')
        self.assertEqual(list(titles.values_list('title', flat=True)), [])

    def test_references_resolved(self):
        """Ссылки закона есть в el и ru, все цели найдены, есть и в прежних редакциях."""
        found = Reference.objects.filter(from_text__version__fragment__act__uid=LAW)
        self.assertTrue(found.filter(from_text__expression__lang='el').exists())
        self.assertTrue(found.filter(from_text__expression__lang='ru').exists())
        self.assertFalse(found.filter(resolved=False).exists())
        self.assertTrue(found.exclude(from_text__version__key=history.BASE_KEY).exists())
        eids = found.filter(
            from_text__expression__lang='el',
            from_text__version__fragment__eid='art_11.par_5',
            from_text__version__key=history.BASE_KEY,
        ).values_list('to_fragment__eid', flat=True)
        self.assertIn('sch_7', list(eids))

    def test_cross_act_references_resolved(self):
        """Изменяющие акты ссылаются на статьи базового закона: el и ru, цели найдены."""
        found = Reference.objects.exclude(
            from_text__version__fragment__act__uid=LAW,
        ).filter(to_act__uid=LAW)
        self.assertTrue(found.filter(from_text__expression__lang='el').exists())
        self.assertTrue(found.filter(from_text__expression__lang='ru').exists())
        self.assertFalse(found.filter(resolved=False).exists())
        # без фрагмента — ссылки на закон целиком («ο περί ΦΠΑ Νόμος»)
        articles = found.exclude(to_fragment=None)
        self.assertTrue(articles.exists())
        self.assertFalse(articles.exclude(to_fragment__act__uid=LAW).exists())

    def test_public_only_on_ru_of_laws_kdp_and_eu(self):
        """public — только у ru-выражений кипрских законов, ΚΔΠ и актов ЕС.

        Отметки могут и не стоять. У el/en и у руководства cy/instr/... отметки нет.
        """
        public = Expression.objects.filter(public=True)
        self.assertFalse(public.exclude(lang='ru').exists())
        self.assertFalse(
            public.exclude(act__uid__startswith='cy/law/')
            .exclude(act__uid__startswith='cy/kdp/')
            .exclude(act__uid__startswith='eu/')
            .exists()
        )

    @override_settings(DJANGO_VITE={'default': {'dev_mode': True}}, NOMOS_PUBLIC_ONLY=True)
    def test_anonymous_reads_public_eu_acts_in_russian(self):
        """Акт ЕС с публичным переводом: по-русски открыт, en/el — 404, оригинал — ELI."""
        marked = Expression.objects.filter(public=True, act__uid__startswith='eu/')
        if not marked.exists():
            self.skipTest('отметок public у актов ЕС ещё нет')
        for expression in marked.select_related('act'):
            act = expression.act
            response = self.client.get('/%s/ru/' % act.uid, follow=True)
            self.assertEqual(response.status_code, 200, act.uid)
            self.assertContains(response, expression.title)
            self.assertContains(response, 'href="%s"' % act.eli)
            for lang in ('en', 'el'):
                response = self.client.get('/%s/%s/' % (act.uid, lang))
                self.assertEqual(response.status_code, 404, (act.uid, lang))

    def active_eids(self, prefix, day):
        found = set()
        fragments = Fragment.objects.filter(act__uid=LAW, eid__startswith=prefix)
        for fragment in fragments:
            version = fragment.version_on(day)
            if version is not None and version.state == 'active':
                found.add(fragment.eid)
        return found

    def test_article_45_paragraphs_7g_7d(self):
        """Пункты 7Γ и 7Δ ст. 45 введены законом 10(I)/2024 от 23.02.2024."""
        before = self.active_eids('art_45.', datetime.date(2024, 1, 1))
        today = self.active_eids('art_45.', datetime.date(2026, 11, 20))
        self.assertEqual(today - before, {'art_45.par_7G', 'art_45.par_7D'})
        self.assertEqual(before - today, set())

    def version(self, eid, day):
        return Fragment.objects.get(act__uid=LAW, eid=eid).version_on(day)

    def test_schedule_12_item_6_periods(self):
        """82/2026 вставляет с 1.4.2026, 167/2026 отменяет с 30.4.2026, 363/2026 — новый."""
        eid = 'sch_12.sub_6'
        self.assertIsNone(self.version(eid, datetime.date(2026, 3, 31)))
        self.assertEqual(self.version(eid, datetime.date(2026, 4, 1)).state, 'active')
        self.assertIsNone(self.version(eid, datetime.date(2026, 5, 1)))
        self.assertEqual(self.version(eid, datetime.date(2026, 11, 20)).state, 'active')

    def test_table_a_item_7_expires(self):
        eid = 'sch_5.tbl_A.par_7.sub_3'
        self.assertEqual(self.version(eid, datetime.date(2024, 5, 30)).state, 'active')
        self.assertIsNone(self.version(eid, datetime.date(2024, 5, 31)))

    def test_order_keys_unique_among_siblings(self):
        pairs = Fragment.objects.filter(act__uid=LAW).values_list('parent_id', 'order_key')
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_table_a_item_14_doubt_note(self):
        """Пункт 14 Πίνακας Α: 167/2026 против 337/2025 и 362/2026 — примечание о сомнении."""
        notes = Note.objects.filter(
            target__version__fragment__act__uid=LAW,
            target__version__fragment__eid='sch_5.tbl_A.par_14',
            body__contains='1.5–31.12.2026',
        )
        self.assertTrue(notes.exists())

    def test_items_after_list_in_unnumbered_paragraph(self):
        """Пункт уровня владельца безномерного абзаца не входит в его список;
        заголовок на полях не склеивается со списком изменяющих законов."""
        found = set(Fragment.objects.filter(act__uid=LAW).values_list('eid', flat=True))
        for eid in ('art_9.par_14D', 'art_9.par_15', 'art_13ST.par_2', 'art_13ST.par_9'):
            self.assertIn(eid, found)
        self.assertNotIn('art_2.par_7.sub_1', found)
        heading = Fragment.objects.get(act__uid=LAW, eid='art_3').versions.first().heading
        self.assertEqual(heading, 'Έννοια του όρου επιχείρηση')

    def test_nested_lists(self):
        """Списки внутри пунктов Пятого и Седьмого Παραρτήματος не всплывают наверх."""
        found = set(
            Fragment.objects.filter(act__uid=LAW).values_list('eid', flat=True)
        )
        for eid in (
            'sch_5.tbl_G.par_3.sub_9.pnt_iv.sub_5.sub_1',
            'sch_5.tbl_G.par_3.sub_9.pnt_vii',
            'sch_5.tbl_G.par_3.sub_17.sub_9',
            'sch_7.tbl_B.par_3.pnt_a.pnt_iv',
            'sch_5.tbl_B.par_13',
        ):
            self.assertIn(eid, found)
        self.assertNotIn('sch_5.tbl_G.par_3.sub_18', found)


class ManifestTextsTests(SimpleTestCase):
    def test_delete_before_reinsert_keeps_manifest_text(self):
        """sch_12.sub_6: вставка 82/2026, отмена 167/2026, новая вставка 363/2026."""
        node = paragraph('νέο')
        first = make_act('cy/kdp/2026/82', datetime.date(2026, 2, 20), datetime.date(2026, 4, 1))
        second = make_act('cy/kdp/2026/167', datetime.date(2026, 3, 27))
        third = make_act('cy/kdp/2026/363', datetime.date(2026, 10, 16))
        ops = [
            make_op(first, 'insert', texts={'sch_6.par_13': ['παλαιό']}),
            make_op(second, 'delete', valid_from=datetime.date(2026, 4, 30)),
            make_op(third, 'insert'),
        ]
        report = []
        versions = history.build_versions(node, ops, 'law#base', report)
        self.assertEqual(report, [])
        self.assertEqual(
            [(version.key, version.state, version.paragraphs) for version in versions],
            [
                ('2026-04-01', 'active', ['παλαιό']),
                ('2026-04-30', 'repealed', []),
                ('2026-10-16', 'active', ['νέο']),
            ],
        )

    def test_op_texts_for_descendant(self):
        element = history.ET.fromstring(
            '<op target="a"><p>один\n  абзац</p><p for="a.sub_1">другой</p></op>'
        )
        self.assertEqual(history.op_texts(element), {'a': ['один абзац'], 'a.sub_1': ['другой']})


class InheritanceTests(SimpleTestCase):
    def test_only_nearest_ancestor_insert(self):
        """Вставка пункта и более поздняя вставка его подпункта: подпункт — по своей."""
        root = Node(kind='act')
        schedule = root.add(Node(kind='sch', num='6'))
        item = schedule.add(Node(kind='par', num='25'))
        item.paragraphs = ['Πίνακας']
        point = item.add(Node(kind='pnt', num='(δ)'))
        point.paragraphs = ['σημείο']
        sub = point.add(Node(kind='sub', num='(1)'))
        sub.paragraphs = ['υποσημείο']
        early = make_act('cy/kdp/2025/337', datetime.date(2025, 11, 21), datetime.date(2026, 1, 1))
        late = make_act('cy/kdp/2026/168', datetime.date(2026, 3, 27))
        early.ops = [make_op(early, 'insert', target=item.eid)]
        late.ops = [make_op(late, 'insert', target=point.eid)]
        versions = history.collect(root, [early, late], 'law#base', [])
        self.assertEqual([version.key for version in versions[sub]], ['2026-03-27'])
        self.assertEqual([version.key for version in versions[item]], ['2026-01-01'])


class RemarkTests(SimpleTestCase):
    def test_doubt_and_until(self):
        node = paragraph('κείμενο')
        child = node.add(Node(kind='sub', num='(1)'))
        versions = {
            node: [history.Version(key='base', amendment='law#base')],
            child: [history.Version(key='base', amendment='law#base')],
        }
        remarks = [
            (node.eid, 'сомнение', {'kind': 'doubt'}),
            (node.eid, 'срок', {'until': '2024-04-30'}),
            ('sch_9', 'нет', {}),
        ]
        report = []
        collect_cylaw.apply_remarks(versions, remarks, 'law#base', report)
        self.assertEqual(versions[node][0].notes, ['сомнение'])
        self.assertEqual(
            [(version.key, version.state) for version in versions[child]],
            [('base', 'active'), ('2024-05-01', 'repealed')],
        )
        self.assertEqual(report, ['sch_9: фрагмента нет в сводном тексте'])


class BeforeTests(SimpleTestCase):
    """<before>: прежний текст целиком — по прежней сводной редакции."""

    def test_replace_and_delete_take_text_from_manifest(self):
        node = paragraph('νέο κείμενο')
        act = make_act('cy/law/2025/243_I', datetime.date(2025, 12, 31),
                       datetime.date(2026, 1, 1))
        element = history.ET.fromstring(
            '<op target="sch_6.par_13"><before><p>παλαιό\n  κείμενο</p><p>δεύτερο</p>'
            '</before></op>'
        )
        before = history.op_before(element)
        self.assertEqual(before, {'sch_6.par_13': ['παλαιό κείμενο', 'δεύτερο']})
        for kind, state, paragraphs in (
            ('replace', 'active', ['νέο κείμενο']),
            ('delete', 'repealed', []),
        ):
            report = []
            versions = history.build_versions(
                node, [make_op(act, kind, before=before)], 'law#base', report,
            )
            self.assertEqual(report, [])
            self.assertEqual(
                [(version.key, version.state, version.paragraphs) for version in versions],
                [
                    ('base', 'active', ['παλαιό κείμενο', 'δεύτερο']),
                    ('2026-01-01', state, paragraphs),
                ],
            )


PAD = ' ' * 12


def parse_body(*lines, act_uid='cy/law/1978/4'):
    """Тело сводного текста после «Προοίμιο»: строки с отступом текста."""
    rows = ['Προοίμιο', '', PAD + 'Επειδή.']
    for line in lines:
        rows += ['', PAD + line]
    return consolidated.Parser(act_uid).parse(rows)


class ConsolidatedTests(SimpleTestCase):
    def test_article_paragraph_point_in_one_line(self):
        """«51Α. (1)(α)» и «42.―(1)» (U+2015): статья, εδάφιο и пункт в одной строке."""
        root = parse_body('42.―(1)(α) Πρόσωπο:', '(β) άλλο.', '51Α. (1)(α) Ένα.', '(β) Δύο.')
        texts = {node.eid: node.paragraphs for node in root.walk() if node.eid}
        self.assertEqual(texts['art_42.par_1'], [])
        self.assertEqual(texts['art_42.par_1.pnt_a'], ['Πρόσωπο:'])
        self.assertEqual(texts['art_42.par_1.pnt_b'], ['άλλο.'])
        self.assertEqual(texts['art_51A.par_1.pnt_a'], ['Ένα.'])

    def test_definitions_in_article_without_subsections(self):
        """Ст. 2 без εδαφίων (89(Ι)/2001): определения — абзацы самой статьи."""
        root = parse_body(
            '2. Στον παρόντα Νόμο, εκτός αν από το κείμενο προκύπτει διαφορετική έννοια-',
            '«Οργανισμός» σημαίνει τον Οργανισμό Ασφάλισης Υγείας·',
            '«Υπουργός» σημαίνει τον Υπουργό Υγείας.',
            '3. Άλλο.',
        )
        article = next(node for node in root.walk() if node.eid == 'art_2')
        consolidated.mark_definitions(article)
        eids = [node.eid for node in root.walk() if node.eid.startswith('art_2')]
        self.assertEqual(eids, ['art_2', 'art_2.def_organismos', 'art_2.def_ypoyrgos'])

    def test_article_2_without_definitions(self):
        """Ст. 2 без εδαφίων и без терминов (приказ ΚΔΠ 392/2024) — как есть."""
        root = parse_body('2. Οι βασικές αποδοχές ορίζονται.', '3. Άλλο.')
        article = next(node for node in root.walk() if node.eid == 'art_2')
        consolidated.mark_definitions(article)
        self.assertFalse(any(node.kind == 'def' for node in article.walk()))
        self.assertIn('αποδοχές', article.text())

    def test_doubled_greek_numbering_is_own_level(self):
        """«(αα)», «(ββ)» под «(i)» — свой уровень, а не продолжение «(α)»."""
        root = parse_body('7.-(1)(α) α:', '(i) πρώτο:', '(αα) ένα·', '(ββ) δύο·', '(ii) β.',
                          '(β) γ.')
        eids = [node.eid for node in root.walk() if node.eid.startswith('art_7')]
        self.assertEqual(eids, [
            'art_7',
            'art_7.par_1',
            'art_7.par_1.pnt_a',
            'art_7.par_1.pnt_a.pnt_i',
            'art_7.par_1.pnt_a.pnt_i.pnt_aa',
            'art_7.par_1.pnt_a.pnt_i.pnt_bb',
            'art_7.par_1.pnt_a.pnt_ii',
            'art_7.par_1.pnt_b',
        ])

    def test_schedule_heading_masculine(self):
        """«ΠΡΩΤΟΣ ΠΙΝΑΚΑΣ» (59(Ι)/2010) — приложение, как «ΠΡΩΤΟ ΠΑΡΑΡΤΗΜΑ»."""
        for heading in ('ΠΡΩΤΟ ΠΑΡΑΡΤΗΜΑ', 'ΔΕΚΑΤΟ ΤΡΙΤΟ ΠΑΡΑΡΤΗΜΑ', 'ΠΡΩΤΟΣ ΠΙΝΑΚΑΣ',
                        'ΤΕΤΑΡΤΟΣ ΠΙΝΑΚΑΣ'):
            self.assertIsNotNone(consolidated.SCHEDULE_RE.match(heading), heading)
        self.assertIsNone(consolidated.SCHEDULE_RE.match('ΠΙΝΑΚΑΣ'))

    def test_schedule_heading_roman(self):
        """«ΠΙΝΑΚΑΣ ΙΙ» по центру (ΚΔΠ 289/2010) — приложение sch_2 без «ΠΑΡΑΡΤΗΜΑΤΑ»."""
        lines = [
            'Προοίμιο',
            '',
            '      Το Υπουργικό Συμβούλιο εκδίδει τους ακόλουθους Κανονισμούς.',
            '',
            'Ερμηνεία.',
            '',
            '      2.-(1) Στους παρόντες Κανονισμούς «Νόμος» σημαίνει τον Νόμο.',
            '',
            '                                   ΠΙΝΑΚΑΣ ΙΙ',
            '',
            '                                   (Κανονισμός 17)',
            '',
            '      Μήνες εισφορών — Ημερομηνία',
            '',
            '      1. Ιανουάριος-Μάρτιος — 10 επόμενου Μαΐου',
        ]
        root = consolidated.Parser('x').parse(lines)
        schedule = root.children[-1]
        self.assertEqual(schedule.token, 'sch_2')
        self.assertEqual(schedule.heading, 'ΠΙΝΑΚΑΣ ΙΙ (Κανονισμός 17)')
        # шапка таблицы — текст приложения, а не заголовок пункта
        self.assertEqual(schedule.paragraphs, ['Μήνες εισφορών — Ημερομηνία'])
        self.assertEqual(schedule.children[0].heading, '')

    def test_history_with_heading(self):
        self.assertTrue(consolidated.is_history('Ιστορικό Τροποποιήσεων 97(I)/2017 44(I)/2018'))
        self.assertTrue(consolidated.is_history('95(I)/2000 10(I)/2024'))
        self.assertFalse(consolidated.is_history('Ιστορικό Τροποποιήσεων'))

    def test_page_break_in_sentence(self):
        """Пустая строка, колонтитул и строчная буква: тот же абзац; заглавная — новый."""
        header = 'https://www.cylaw.org/nomoi/enop/non-ind/1978_1_4/full.html'
        blocks = layout.blocks(
            ['    ψηφισθέντος είτε προ', '', header, '    είτε κατόπιν.', '',
             '    Νοείται ότι άλλο', '', header, '    Άλλος.'],
            skip=consolidated.is_page_furniture,
        )
        self.assertEqual(
            [block.text for block in blocks],
            ['ψηφισθέντος είτε προ είτε κατόπιν.', 'Νοείται ότι άλλο', 'Άλλος.'],
        )

    def test_heading_ends_with_abbreviation(self):
        """Заголовок с точкой после «κ.τ.λ.» — заголовок; обычная фраза с точкой — нет."""
        following = layout.Block(2, 12, '4. Υπηρεσίες.')
        heading = layout.Block(1, 12, 'Πρόσβαση σε αθλητικές κ.τ.λ. εκδηλώσεις.')
        phrase = layout.Block(1, 12, 'Πρόσβαση σε αθλητικές εκδηλώσεις.')
        self.assertTrue(consolidated.looks_like_heading(heading, following))
        self.assertFalse(consolidated.looks_like_heading(phrase, following))

    def test_split_blocks_from_manifest(self):
        """`<split before>`: заключительные слова отделены от пункта, отступ — открывшего список."""
        blocks = [
            layout.Block(1, 12, '(10) Όταν-'),
            layout.Block(2, 16, '(β) δεν ικανοποιείται,και η παρουσία των αγαθών.'),
        ]
        result = consolidated.split_blocks(blocks, ['και η παρουσία'])
        self.assertEqual(
            [(block.indent, block.text) for block in result],
            [
                (12, '(10) Όταν-'),
                (16, '(β) δεν ικανοποιείται,'),
                (12, 'και η παρουσία των αγαθών.'),
            ],
        )
        with self.assertRaises(ValueError):
            consolidated.split_blocks(blocks, ['Λείπει'])

    def test_part_left_of_centre_in_earlier_act(self):
        """«ΜΕΡΟΣ ΙΙΙ …» с отступом 13 — Часть и в актах, собранных до 118(Ι) (59(Ι)/2010)."""
        parser = consolidated.Parser('cy/law/2010/59_I')
        block = layout.Block(1, 13, 'ΜΕΡΟΣ ΙΙΙ ΠΑΡΟΧΕΣ')
        self.assertTrue(parser.is_part(block))


def column_lines(*rows):
    """Строки pdftotext -layout: каждая — [(позиция, текст), …]."""
    lines = []
    for row in rows:
        line = ''
        for position, text in row:
            line = line.ljust(position) + text
        lines.append(line)
    return lines


def table_stops(text):
    return text.startswith('ΜΕΡΟΣ')


class ColumnsTests(SimpleTestCase):
    """Таблицы в столбцах приложений 59(Ι)/2010: столбцы по координате x."""

    def extract(self, lines):
        return columns.extract(lines, 0, lambda line: False, table_stops)

    def test_two_columns(self):
        lines = column_lines(
            [(40, 'Ύψος παροχής')],
            [(20, 'Βασικό'), (56, 'Συμπληρωματικό')],
            [],
            [(20, '60% του ποσού των'), (57, '50% της σύνταξης')],
            [(20, 'βασικών αποδοχών:'), (57, 'χηρείας.')],
            [(20, '(α) για τον ένα.')],
            [],
            [(20, 'ΜΕΡΟΣ ΙΙ')],
        )
        result, tables = self.extract(lines)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table.caption, 'Ύψος παροχής')
        cells = {cell.token: cell for cell in table.cells}
        self.assertEqual(list(cells), ['cel_h1_1', 'cel_h1_2', 'cel_1_1', 'cel_1_2'])
        self.assertEqual(cells['cel_h1_2'].text, 'Συμπληρωματικό')
        self.assertEqual(
            [block.text for block in cells['cel_1_1'].blocks],
            ['60% του ποσού των βασικών αποδοχών:', '(α) για τον ένα.'],
        )
        self.assertEqual(
            [block.text for block in cells['cel_1_2'].blocks], ['50% της σύνταξης χηρείας.'],
        )
        marks = [line.strip() for line in result if line.strip()]
        self.assertEqual(marks, [columns.MARK + '1', 'ΜΕΡΟΣ ΙΙ'])

    def test_two_level_header(self):
        """Название слева — на оба ряда шапки, верхнее над двумя столбцами — на оба."""
        lines = column_lines(
            [(20, 'Περίπτωση'), (59, 'Ύψος')],
            [(45, 'Βασικό'), (66, 'Συμπληρωματικό')],
            [],
            [(20, '(α) Μισθωτός'), (45, '60%'), (66, '40%')],
            [],
            [],
            [(20, '(β) Άλλος'), (45, '50%')],
            [],
            [(20, 'ΜΕΡΟΣ ΙΙ')],
        )
        _, tables = self.extract(lines)
        tokens = [cell.token for cell in tables[0].cells]
        self.assertEqual(tokens, [
            'cel_h1-2_1', 'cel_h1_2-3', 'cel_h2_2', 'cel_h2_3',
            'cel_1_1', 'cel_1_2', 'cel_1_3', 'cel_2_1', 'cel_2_2',
        ])

    def test_header_without_columns_below(self):
        """Строка, похожая на шапку, без текста под её столбцами — не таблица."""
        lines = column_lines(
            [(20, 'Εισόδημα'), (56, 'Συντελεστής')],
            [],
            [(20, 'Για κάθε ευρώ.')],
            [(20, 'ΜΕΡΟΣ ΙΙ')],
        )
        result, tables = self.extract(lines)
        self.assertEqual(tables, [])
        self.assertEqual(result, lines)


    def test_stacked_rate_table(self):
        """118(Ι) Παράρτημα 2: название столбца в строках через пустую, ряд — строка;
        строка перед таблицей и после неё — обычный текст."""
        lines = column_lines(
            [(7, 'β) Από το φορολογικό έτος 2008 και μετέπειτα:')],
            [],
            [(56, 'Φορολογικός')],
            [(21, 'Φορολογητέο Εισόδημα')],
            [(56, 'Συντελεστής')],
            [],
            [(7, 'Για κάθε ευρώ μέχρι €19.500'), (60, '0%')],
            [(7, 'Για κάθε ευρώ πάνω από €19.500 μέχρι €28.000'), (60, '20%')],
            [],
            [(11, 'Νοείται ότι για τους σκοπούς της παρούσας παραγράφου.')],
        )
        result, tables = columns.extract(
            lines, 0, lambda line: False, table_stops, stacked=True,
        )
        self.assertEqual(len(tables), 1)
        cells = {
            cell.token: cell.text or cell.blocks[0].text for cell in tables[0].cells
        }
        self.assertEqual(cells, {
            'cel_h1_1': 'Φορολογητέο Εισόδημα',
            'cel_h1_2': 'Φορολογικός Συντελεστής',
            'cel_1_1': 'Για κάθε ευρώ μέχρι €19.500',
            'cel_1_2': '0%',
            'cel_2_1': 'Για κάθε ευρώ πάνω από €19.500 μέχρι €28.000',
            'cel_2_2': '20%',
        })
        texts = [line.strip() for line in result if line.strip()]
        self.assertEqual(texts, [
            'β) Από το φορολογικό έτος 2008 και μετέπειτα:',
            columns.MARK + '1',
            'Νοείται ότι για τους σκοπούς της παρούσας παραγράφου.',
        ])


class SplitNotesTests(SimpleTestCase):
    def test_note_goes_to_act_by_number(self):
        act = make_act('cy/law/2025/104_I', datetime.date(2025, 6, 25))
        act.number = '104(I)'
        act.year = '2025'
        own, other = collect_cylaw.split_notes(
            ['Σημείωση 1 του Ν. 104(Ι)/2025 Έναρξη ισχύος 1.1.2025', 'Σημείωση 2 του Ν. 1/2001'],
            [act],
        )
        self.assertEqual(list(own), ['cy/law/2025/104_I'])
        self.assertEqual(other, ['Σημείωση 2 του Ν. 1/2001'])


def gazette_lines(*rows):
    """Строки газеты: (поле, текст) → строка с текстом с колонки 22."""
    return [' ' + margin.ljust(21) + body for margin, body in rows]


class GazetteTests(SimpleTestCase):
    def parse(self, *rows):
        return gazette.GazetteParser('cy/law/2025/1_I').parse(gazette_lines(*rows))

    def test_points_after_ano_teleia_and_lone_kai(self):
        root = self.parse(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
            ('Τροποποίηση', '2. Το άρθρο 5 τροποποιείται ως ακολούθως:'),
            ('του άρθρου 5.', ''),
            ('', '(α) με τη διαγραφή του εδαφίου (1Β)\u0387'),
            ('', '(β) με τη διαγραφή του εδαφίου (3)·'),
            ('', 'και'),
            ('', '(γ) με τη διαγραφή του εδαφίου (4).'),
        )
        article = root.children[2]
        self.assertEqual([point.num for point in article.children], ['(α)', '(β)', '(γ)'])

    def test_unclosed_quote_before_next_article(self):
        """Кавычка не закрыта в газете (242(Ι)/2025): следующая статья с полем — всё же статья."""
        root = self.parse(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
            ('Τροποποίηση', '2. Το άρθρο 5 τροποποιείται με την αντικατάσταση της φράσης'),
            ('του άρθρου 5.', '«τριακοσίων ευρώ» με τη φράση «τετρακοσίων ευρώ.'),
            ('Τροποποίηση', '3. Το άρθρο 6 τροποποιείται με τη διαγραφή του εδαφίου (2).'),
            ('του άρθρου 6.', ''),
        )
        self.assertEqual([node.num for node in root.children[1:]], ['1', '2', '3'])

    def test_issue_masthead_is_not_preamble(self):
        """Шапка первой страницы выпуска (239(Ι)/2025) в преамбулу не идёт."""
        lines = [
            ' ' * 90 + 'N. 1(I)/2025',
            '',
            ' ' * 24 + 'ΕΠΙΣΗΜΗ ΕΦΗΜΕΡΙΔΑ',
            ' ' * 22 + 'ΤΗΣ ΚΥΠΡΙΑΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ',
            ' ' * 32 + 'ΠΑΡΑΡΤΗΜΑ ΠΡΩΤΟ',
            ' ' * 34 + 'ΝΟΜΟΘΕΣΙΑ - ΜΕΡΟΣ Ι',
            '',
            ' Αριθμός 5070' + ' ' * 20 + 'Τετάρτη, 31 Δεκεμβρίου 2025' + ' ' * 20 + '1247',
            '',
            '   Ο περί Δοκιμής Νόμος του 2025 εκδίδεται με δημοσίευση.',
        ]
        lines += gazette_lines(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
        )
        root = gazette.GazetteParser('cy/law/2025/1_I').parse(lines)
        self.assertEqual(
            root.children[0].paragraphs,
            ['Ο περί Δοκιμής Νόμος του 2025 εκδίδεται με δημοσίευση.'],
        )

    def test_law_number_in_margin_is_not_text(self):
        root = self.parse(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('', ''),
            ('', '                  82(I) του 2020'),
            ('', '                   25(Ι) του 2022.'),
        )
        self.assertEqual(len(root.children[1].paragraphs), 1)

    def test_margin_crossing_text_column_inside_quote(self):
        """«15(Ι) του 2024.» заходит за колонку текста (15/2024): поле, а не текст."""
        lines = gazette_lines(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
            ('Προσθήκη.', '2. Μετά το άρθρο 102 προστίθεται το ακόλουθο νέο άρθρο:'),
            ('', '«103. Οι διατάξεις του περί Δοκιμής Νόμου'),
            ('', '      του 2024, τυγχάνουν εφαρμογής και σε ασφαλισμένη που βρίσκεται σε'),
        )
        lines += [
            ' ' * 8 + '15(Ι) του 2024.     άδεια μητρότητας κατά την ημερομηνία έναρξης',
            ' ' * 28 + 'της ισχύος του Νόμου.».',
        ]
        root = gazette.GazetteParser('cy/law/2025/1_I').parse(lines)
        text = ' '.join(root.children[2].paragraphs)
        self.assertIn('βρίσκεται σε άδεια μητρότητας', text)
        self.assertNotIn('15(Ι)', text)

    def test_legacy_gazette_keeps_old_rules(self):
        """104(Ι)/2025 и ΚΔΠ 168/2026 собраны прежними правилами: «(β)» без (α) — пункт,
        поле внутри цитаты не ищется."""
        lines = gazette_lines(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
            ('Τροποποίηση', '2. Το άρθρο 6 τροποποιείται ως ακολούθως:'),
            ('', '(β) με την προσθήκη της φράσης «και των αναλογιών».'),
        )
        new = gazette.GazetteParser('cy/law/2025/1_I').parse(lines)
        old = gazette.GazetteParser('cy/law/2025/104_I').parse(lines)
        self.assertEqual(new.children[2].children, [])
        self.assertEqual([point.token for point in old.children[2].children], ['pnt_b'])

    def test_quote_ends_with_ano_teleia(self):
        root = self.parse(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('Τροποποίηση', '2. Το άρθρο 6 τροποποιείται ως ακολούθως:'),
            ('', '(α) με την αντικατάσταση της φράσης «κατά τη διενέργεια» με τη φράση'),
            ('', '«των αναλογιών.»\u0387'),
            ('', '(β) με τη διαγραφή του εδαφίου (2).'),
        )
        points = root.children[2].children
        self.assertEqual([point.num for point in points], ['(α)', '(β)'])
        self.assertTrue(points[0].paragraphs[0].endswith('αναλογιών.»\u0387'))

    def test_quote_closed_at_line_end_and_middle_dot(self):
        """«‘όρος᾽ σημαίνει …∙» — цитата закрыта; «»ꞏ» (U+A78F) — конец фразы (53/2025)."""
        root = self.parse(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('Τροποποίηση', '2. Το άρθρο 2 τροποποιείται ως ακολούθως:'),
            ('', '(α) με την προσθήκη του ακόλουθου νέου όρου και του ορισμού του:'),
            ('', '«‘αρμόδια αρχή᾽ σημαίνει τον Υπουργό∙»'),
            ('', '(β) με την αντικατάσταση της λέξης «Επίτροπος» με τη λέξη «αρχή»\ua78f'),
            ('', '(γ) με την κατάργηση του όρου «Γραφείο».'),
            ('Τροποποίηση', '3. Ο βασικός νόμος τροποποιείται.'),
        )
        articles = [node for node in root.children if node.kind == 'art']
        self.assertEqual([article.num for article in articles], ['1', '2', '3'])
        points = articles[1].children
        self.assertEqual([point.num for point in points], ['(α)', '(β)', '(γ)'])

    def test_subsections_outside_quote(self):
        root = self.parse(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('Έναρξη', '2.-(1) Ο παρών Νόμος τίθεται σε ισχύ κατά τη δημοσίευσή του.'),
            ('', '(2) Το άρθρο 1 τίθεται σε ισχύ την 1η Ιανουαρίου 2026.'),
        )
        self.assertEqual(len(root.children[2].paragraphs), 2)

    def test_transcription_of_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '49_I.txt'
            path.write_text('α\nβ\n', encoding='utf-8')
            self.assertEqual(layout.pdf_lines(path), ['α', 'β', ''])

    def test_transcription_header_dropped(self):
        """Пометка «Расшифровка скана …» до пустой строки — не текст; строки на месте."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '52.txt'
            path.write_text('Расшифровка скана 52.pdf\nв раскладке.\n\nα\n', encoding='utf-8')
            self.assertEqual(layout.pdf_lines(path), ['', '', '', 'α', ''])

    def test_short_line_continuing_phrase_is_text(self):
        """Страница без полей: короткая строка с края после длинной незаконченной — текст."""
        previous = 'σε σχέση με τις μικρές επιχειρήσεις που εφαρμόζει την εκεί προβλε-'
        self.assertEqual(
            gazette.split_line('πόμενη επιλογή.', 22, previous),
            ('', 'πόμενη επιλογή.'),
        )
        # после законченной фразы или строки с полем — поле
        self.assertEqual(
            gazette.split_line('Ερμηνεία.', 22, previous[:-1] + '.'),
            ('Ερμηνεία.', ''),
        )
        self.assertEqual(
            gazette.split_line('Ερμηνεία.', 22, 'Συνοπτικός      1. Ο παρών Νόμος θα αναφέρεται'),
            ('Ερμηνεία.', ''),
        )

    def test_subsection_followed_by_point_number(self):
        """«(5)(α) …» после законченной фразы — новый абзац, как «(5) …» (53(Ι)/2025)."""
        root = self.parse(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
            ('Τροποποίηση', '2.-(1) Οι έρευνες δεν δημοσιεύονται.'),
            ('', '(2)(α) Η διαδικασία είναι κατάλληλη.'),
        )
        self.assertEqual(
            root.children[2].paragraphs,
            ['(1) Οι έρευνες δεν δημοσιεύονται.', '(2)(α) Η διαδικασία είναι κατάλληλη.'],
        )


class OrderKeyTests(SimpleTestCase):
    def test_siblings_of_different_kinds_keep_document_order(self):
        """art_2 и prt_II под одним родителем не получают один ключ."""
        root = Node(kind='act')
        keys = [
            root.add(Node(kind=kind, num=num)).order_key
            for kind, num in (('art', '1'), ('art', '2'), ('prt', 'II'), ('art', '3'), ('sch', '1'))
        ]
        self.assertEqual(keys, sorted(set(keys)))
        self.assertEqual(len(keys), 5)


REF_EIDS = [
    'art_5', 'art_5.par_1', 'art_5.par_2', 'art_6', 'art_6.par_2', 'art_8', 'art_9',
    'art_9.par_4', 'art_9.par_5', 'art_9.par_10', 'art_11A', 'art_13A', 'art_13B',
    'art_13ST', 'art_13ST.par_1',
    'art_13ST.par_1.pnt_a', 'art_58', 'art_72', 'sch_1', 'sch_1.prt_III', 'sch_5',
    'sch_5.tbl_G', 'sch_10', 'sch_10.par_1A', 'sch_13',
]


class RefsTests(SimpleTestCase):
    """Разметка ссылок: только существующие цели, без ссылок на себя и на другие акты."""

    def marked(self, text, language=refs.EL, own='art_1'):
        marker = refs.Marker(refs.Targets(REF_EIDS), language)
        paragraphs, found = marker.mark_text([text], own)
        return paragraphs[0], found, marker.skipped

    def test_greek_forms(self):
        cases = {
            'του άρθρου 5': ['art_5'],
            'τα άρθρα 8 και 9': ['art_8', 'art_9'],
            'των άρθρων 58 μέχρι 72': ['art_58', 'art_72'],
            'του άρθρου 9(4), (5) ή (10)': ['art_9.par_4', 'art_9.par_5', 'art_9.par_10'],
            'του άρθρου 11Α και του άρθρου 13ΣΤ(1)(α)': ['art_11A', 'art_13ST.par_1.pnt_a'],
            'το εδάφιο (2) του άρθρου 6': ['art_6.par_2'],
            # без «του» перед «άρθρου»: пункт статьи 6, не пункт своей статьи
            'του εδαφίου (2) άρθρου 6': ['art_6.par_2'],
            'στο Πέμπτο Παράρτημα': ['sch_5'],
            'του Μέρους ΙΙΙ του Πρώτου Παραρτήματος': ['sch_1.prt_III'],
            'του Πίνακα Γ του Πέμπτου Παραρτήματος': ['sch_5.tbl_G'],
            'του Δέκατου Τρίτου Παραρτήματος': ['sch_13'],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.marked(text)[1], expected)

    def test_markup_keeps_text(self):
        text, found, _ = self.marked('τα άρθρα 8 και 9 του παρόντος Νόμου')
        self.assertEqual(
            text,
            'τα <ref id="r1" to="art_8">άρθρα 8</ref> και <ref id="r2" to="art_9">9</ref>'
            ' του παρόντος Νόμου',
        )
        self.assertEqual(refs.strip_own_refs(text), 'τα άρθρα 8 και 9 του παρόντος Νόμου')
        self.assertEqual(self.marked(text)[0], text)

    def test_russian_forms(self):
        cases = {
            'статья 5': ['art_5'],
            'в статьях 8 и 9': ['art_8', 'art_9'],
            'пункта (2) статьи 6': ['art_6.par_2'],
            'статьёй 13СТ': ['art_13ST'],
            'статьям 13А и 13Б': ['art_13A', 'art_13B'],
            'статей 11А или 13ΣΤ(1)': ['art_11A', 'art_13ST.par_1'],
            'Пятое приложение': ['sch_5'],
            'параграфа 1Α Десятого приложения': ['sch_10.par_1A'],
            'таблицы Γ Пятого приложения': ['sch_5.tbl_G'],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.marked(text, refs.RU)[1], expected)

    def test_russian_point_letters(self):
        """Кириллическая буква подпункта — по месту в алфавите: (в) — (γ), (е) — (στ)."""
        eids = [
            'art_43', 'art_43.par_1', 'art_43.par_1.pnt_g', 'art_43.par_1.pnt_st',
            'art_43.par_1.pnt_i', 'art_43.par_1.pnt_bb',
        ]
        cases = {
            'пункт (1)(в) статьи 43': ['art_43.par_1.pnt_g'],
            'пункта (1)(е) статьи 43': ['art_43.par_1.pnt_st'],
            'пункта (1)(к) статьи 43': ['art_43.par_1.pnt_i'],
            'пункта (1)(бб) статьи 43': ['art_43.par_1.pnt_bb'],
            # такого подпункта нет — ближайший предок
            'пункта (1)(а) статьи 43': ['art_43.par_1'],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                marker = refs.Marker(refs.Targets(eids), refs.RU)
                self.assertEqual(marker.mark_text([text], 'art_1')[1], expected)

    def test_not_marked(self):
        cases = [
            ('του παρόντος άρθρου', refs.EL),
            ('το παρόν Παράρτημα', refs.EL),
            ('του άρθρου 5 του περί Φορολογίας του Εισοδήματος Νόμου', refs.EL),
            ('του άρθρου 25 του Νόμου του 1990', refs.EL),
            ('του άρθρου 10Α αυτού', refs.EL),
            ('статьи 33 Закона о подоходном налоге', refs.RU),
            ('на основании его статьи 10Α', refs.RU),
            ('настоящей статьи', refs.RU),
        ]
        for text, language in cases:
            with self.subTest(text=text):
                self.assertEqual(self.marked(text, language)[1], [])

    def test_self_reference_not_marked(self):
        _, found, skipped = self.marked('του άρθρου 5(2)', own='art_5.par_2')
        self.assertEqual(found, [])
        self.assertEqual(skipped['ссылка на себя или своего предка'], 1)
        self.assertEqual(self.marked('του άρθρου 5', own='art_5.par_2')[1], [])
        self.assertEqual(self.marked('του άρθρου 5(1)', own='art_5.par_2')[1], ['art_5.par_1'])

    def test_missing_target_not_marked(self):
        _, found, skipped = self.marked('του άρθρου 88 και του Ενδέκατου Παραρτήματος')
        self.assertEqual(found, [])
        self.assertEqual(skipped['цели нет в законе'], 2)
        # пункта нет — ссылка на статью
        self.assertEqual(self.marked('του άρθρου 6(7)')[1], ['art_6'])

    def test_bare_paragraph_of_own_article(self):
        """«εδάφιο (2)» без номера статьи — пункт своей статьи, если он есть."""
        cases = [
            ('του εδαφίου (2) πιο πάνω', refs.EL, ['art_5.par_2']),
            ('τα εδάφια (1) και (2)', refs.EL, ['art_5.par_1', 'art_5.par_2']),
            ('пункта (2) выше', refs.RU, ['art_5.par_2']),
            ('Пункт (1) применяется', refs.RU, ['art_5.par_1']),
            ('Без ущерба для пункта (2) Правила могут', refs.RU, ['art_5.par_2']),
            # аббревиатура организации после номера — не уточнение (89(Ι) utsih6efbt)
            ('в силу пункта (2) HIO вправе', refs.RU, ['art_5.par_2']),
        ]
        for text, language, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.marked(text, language, 'art_5')[1], expected)

    def test_inner_schedule_items(self):
        """Пункты своей части приложения без её названия (95(Ι), Πρώτο Παράρτημα)."""
        eids = [
            'sch_1', 'sch_1.prt_I', 'sch_1.prt_I.sct_1', 'sch_1.prt_I.sct_1.par_1',
            'sch_1.prt_I.sct_1.par_1.sub_1', 'sch_1.prt_I.sct_1.par_1.sub_1.pnt_a',
            'sch_1.prt_I.sct_1.par_1.sub_1.pnt_b', 'sch_1.prt_I.sct_1.par_1.sub_2',
            'sch_1.prt_I.sct_1.par_1.sub_2.pnt_a', 'sch_1.prt_I.sct_1.par_1.sub_2.pnt_b',
            'sch_1.prt_I.sct_1.par_3', 'sch_1.prt_I.sct_1.par_5',
            'sch_1.prt_I.sct_1.par_5.sub_2', 'sch_1.prt_I.sct_1.par_5.sub_2.pnt_a',
            'sch_1.prt_I.sct_1.par_5.sub_2.pnt_e',
        ]
        own = 'sch_1.prt_I.sct_1.par_3'
        cases = [
            (
                'στις παραγράφους 1(1)(α) και (β) και (2)(α) και (β) πιο πάνω.',
                [
                    'sch_1.prt_I.sct_1.par_1.sub_1.pnt_a',
                    'sch_1.prt_I.sct_1.par_1.sub_1.pnt_b',
                    'sch_1.prt_I.sct_1.par_1.sub_2.pnt_a',
                    'sch_1.prt_I.sct_1.par_1.sub_2.pnt_b',
                ],
            ),
            (
                'στις υποπαραγράφους (α) έως (ε) της παραγράφου (2) του σημείου 5, εκτός',
                [
                    'sch_1.prt_I.sct_1.par_5.sub_2.pnt_a',
                    'sch_1.prt_I.sct_1.par_5.sub_2.pnt_e',
                    'sch_1.prt_I.sct_1.par_5.sub_2',
                ],
            ),
            # пункта нет — к предку не сводится
            ('της υποπαραγράφου (γ) της παραγράφου (2) του σημείου 5,', [
                'sch_1.prt_I.sct_1.par_5.sub_2',
            ]),
            # без «πιο πάνω» и вне приложения — не размечается
            ('της παραγράφου 1(1)(α) του Νόμου', []),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                marker = refs.Marker(refs.Targets(eids), refs.EL)
                self.assertEqual(marker.mark_text([text], own)[1], expected)
        marker = refs.Marker(refs.Targets(eids), refs.EL)
        self.assertEqual(marker.mark_text([cases[0][0]], 'art_5')[1], [])

    def test_bare_paragraph_not_marked(self):
        cases = [
            ('του εδαφίου (7)', refs.EL, 'art_5.par_1', 'пункта нет в своей статье'),
            ('του εδαφίου (2) του άρθρου 6', refs.EL, 'art_5.par_1', None),
            ('του εδαφίου (2) αυτού', refs.EL, 'art_5.par_1',
             'пункт с уточнением (другая статья или акт)'),
            ('пункт (2) той же статьи', refs.RU, 'art_5.par_1',
             'пункт с уточнением (другая статья или акт)'),
            ('пункта (2) Закона о подоходном налоге', refs.RU, 'art_5.par_1',
             'пункт с уточнением (другая статья или акт)'),
            ('пункта (2) Ν. 95(Ι)/2000', refs.RU, 'art_5.par_1',
             'пункт с уточнением (другая статья или акт)'),
            ('του εδαφίου (2)', refs.EL, 'sch_1', 'пункт без номера статьи'),
        ]
        for text, language, own, reason in cases:
            with self.subTest(text=text):
                _, found, skipped = self.marked(text, language, own)
                self.assertNotIn('art_5.par_2', found)
                if reason:
                    self.assertEqual(skipped[reason], 1)
        # «пункта (1)» внутри статьи 5, пункт 1 — ссылка на себя
        self.assertEqual(self.marked('του εδαφίου (1)', own='art_5.par_1')[1], [])

    def test_bare_paragraph_without_own_text(self):
        """Изменяющий акт без базового закона в базе: своих фрагментов нет."""
        marker = refs.Marker(refs.Targets([]), refs.EL)
        _, found = marker.mark_text(['του εδαφίου (2)'], 'art_3.pnt_a')
        self.assertEqual(found, [])
        self.assertEqual(marker.skipped['цели нет в законе'], 1)

    def test_russian_article_in_paragraph_word(self):
        """«пункта 9(4)» — статья 9, пункт 4; «пункта 2(α)» — не статья."""
        self.assertEqual(self.marked('пункта 9(4) или (5)', refs.RU)[1],
                         ['art_9.par_4', 'art_9.par_5'])
        self.assertEqual(self.marked('пункта 5(β)', refs.RU)[1], [])

    def amending(self, paragraphs, language=refs.EL, implicit=False):
        marker = refs.Marker(refs.Targets(REF_EIDS), language, LAW, implicit=implicit)
        return marker.mark_text(paragraphs, 'art_1') + (marker.skipped,)

    def test_amending_act_marks_base_law(self):
        """Изменяющий акт: статьи базового закона — межактовые ссылки."""
        cases = [
            ('το άρθρο 5 του βασικού νόμου', refs.EL),
            ('του άρθρου 5 του περί Φόρου Προστιθέμενης Αξίας Νόμου', refs.EL),
            ('статью 5 базового Закона', refs.RU),
            ('статьи 5 Закона о налоге на добавленную стоимость', refs.RU),
        ]
        for text, language in cases:
            with self.subTest(text=text):
                paragraphs, found, _ = self.amending([text], language)
                self.assertEqual(found, [LAW + '#art_5'])
                self.assertIn('to="%s#art_5"' % LAW, paragraphs[0])
                self.assertEqual(refs.strip_own_refs(paragraphs[0]), text)

    def test_amending_act_quotes_and_own_articles(self):
        """В кавычках «…» — текст базового закона, вне кавычек без указания акта — нет."""
        paragraphs = [
            'Το άρθρο 2 του παρόντος Νόμου τροποποιείται ως εξής:',
            '«(1) Σύμφωνα με το άρθρο 8',
            'και το Πέμπτο Παράρτημα.»',
            'Το άρθρο 9 ισχύει.',
        ]
        _, found, skipped = self.amending(paragraphs)
        self.assertEqual(found, [LAW + '#art_8', LAW + '#sch_5'])
        self.assertEqual(skipped['ссылка на сам изменяющий акт'], 1)
        self.assertEqual(skipped['акт не указан'], 1)
        # ΚΔΠ изменяет только базовый закон: и без кавычек — его статьи
        self.assertEqual(self.amending(['Το άρθρο 9 ισχύει.'], implicit=True)[1],
                         [LAW + '#art_9'])
        self.assertEqual(self.amending(['το άρθρο 9 του παρόντος Διατάγματος'],
                                       implicit=True)[1], [])

    def test_amending_act_skips_bare_paragraph(self):
        _, found, skipped = self.amending(['«του εδαφίου (2) πιο πάνω»'])
        self.assertEqual(found, [])
        self.assertEqual(skipped['пункт без номера статьи'], 1)

    def test_amending_act_paragraph_of_amended_article(self):
        """Пункт без статьи — пункт изменяемой статьи: своей или предка."""
        marker = refs.Marker(refs.Targets(REF_EIDS), refs.EL, LAW)
        marker.mark_text(['Το άρθρο 9 του βασικού νόμου τροποποιείται ως ακολούθως:'], 'art_3')
        cases = [
            ('με την προσθήκη στο εδάφιο (4) της φράσης «και»·', [LAW + '#art_9.par_4']),
            ('με την αντικατάσταση του εδαφίου (5) αυτού·', [LAW + '#art_9.par_5']),
            # нового пункта в законе нет — статья
            ('με την προσθήκη αμέσως μετά το εδάφιο (7) του ακόλουθου νέου εδαφίου:',
             [LAW + '#art_9']),
            # статья в кавычках — новый текст, не изменяемая статья
            ('με την προσθήκη στο εδάφιο (4) της φράσης «του άρθρου 5 του βασικού νόμου»',
             [LAW + '#art_9.par_4', LAW + '#art_5']),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(marker.mark_text([text], 'art_3.pnt_a')[1], expected)
        own = marker.mark_text(['Το άρθρο 6 του βασικού νόμου, στο εδάφιο (2)'], 'art_4')
        self.assertEqual(own[1], [LAW + '#art_6', LAW + '#art_6.par_2'])
        # две статьи — какая изменяется, неизвестно
        marker.mark_text(['Τα άρθρα 5 και 6 του βασικού νόμου τροποποιούνται:'], 'art_5')
        self.assertEqual(marker.mark_text(['στο εδάφιο (2)'], 'art_5.pnt_a')[1], [])
        self.assertEqual(marker.skipped['пункт без номера статьи'], 1)

    def test_amending_act_insert_after_article(self):
        """«αμέσως μετά το άρθρο 9» без акта — статья закона; она не изменяемая."""
        marker = refs.Marker(refs.Targets(REF_EIDS), refs.EL, LAW)
        _, found = marker.mark_text([
            'Ο βασικός νόμος τροποποιείται με την προσθήκη, αμέσως μετά το άρθρο 9 '
            'του ακόλουθου νέου άρθρου: «9Α.-(1) Τηρουμένων των διατάξεων του εδαφίου (2)»',
        ], 'art_2')
        self.assertEqual(found, [LAW + '#art_9'])
        self.assertEqual(marker.skipped['пункт без номера статьи'], 1)
        ru = refs.Marker(refs.Targets(REF_EIDS), refs.RU, LAW)
        _, found = ru.mark_text(['добавлением сразу после его статьи 9 новой статьи'], 'art_2')
        self.assertEqual(found, [LAW + '#art_9'])
        ru.mark_text(['Статья 9 базового Закона изменяется:'], 'art_3')
        _, found = ru.mark_text(['заменой в её пункте (4) слов «А»'], 'art_3.pnt_a')
        self.assertEqual(found, [LAW + '#art_9.par_4'])

    def test_translation_ids_follow_greek(self):
        marked = refs.align_ids(
            '<ref id="r1" to="art_9">статьи 9</ref> и <ref id="r2" to="art_8">8</ref>',
            ['art_8', 'art_9'],
        )
        self.assertEqual(
            marked, '<ref id="r2" to="art_9">статьи 9</ref> и <ref id="r1" to="art_8">8</ref>',
        )

    def test_translation_base_law_named_main(self):
        """«статье 5 основного закона» — тот же базовый закон, что «базового Закона»."""
        _, found, _ = self.amending(['В статье 5 основного закона'], refs.RU)
        self.assertEqual(found, [LAW + '#art_5'])

    def test_translation_capital_word_decided_by_greek(self):
        """Заглавное слово после упоминания: ссылка, только если она есть в оригинале."""
        text = ['применения статьи 5 Генеральный директор, статьи 6, или (и) Совет']
        marker = refs.Marker(refs.Targets(REF_EIDS), refs.RU)
        self.assertEqual(marker.mark_text(text, 'art_1')[1], [])
        marker.agreed = {'art_5', 'art_6'}
        marked, found = marker.mark_text(text, 'art_1')
        self.assertEqual(found, ['art_5', 'art_6'])
        self.assertNotIn('(и)</ref>', marked[0])
        marker.agreed = set()
        _, found, _ = self.amending(['положений пункта (2) статьи 5 того же Закона'], refs.RU)
        self.assertEqual(found, [])

    def test_translation_bare_law_decided_by_greek(self):
        """«статьи 5 Закона, как…» — базовый закон, если так размечено в оригинале."""
        text = ['пункта (2) статьи 5 Закона, как он действовал']
        _, found, _ = self.amending(text, refs.RU)
        self.assertEqual(found, [])
        marker = refs.Marker(refs.Targets(REF_EIDS), refs.RU, LAW)
        marker.agreed = {LAW + '#art_5.par_2'}
        self.assertEqual(marker.mark_text(text, 'art_1')[1], [LAW + '#art_5.par_2'])
        _, found, _ = self.amending(['статьи 5 Закона о специальном взносе'], refs.RU)
        self.assertEqual(found, [])

    def test_regulations_enabling_articles_not_base(self):
        """ΚΔΠ: «Κανονισμοί δυνάμει των άρθρων 5, 6…» — статьи закона, не базовых правил."""
        _, found, skipped = self.amending(
            ['Κανονισμοί δυνάμει των άρθρων 5, 6 και 8 Το Υπουργικό Συμβούλιο, το άρθρο 9'],
            implicit=True,
        )
        self.assertEqual(found, [LAW + '#art_9'])
        self.assertEqual(skipped['похоже на другой акт'], 3)

    def test_translation_file_text_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ru.xml'
            source = (
                '<texts>\n  <text fragment="u1" version="base">\n'
                '    <p>согласно статьям 8 и 9 настоящего Закона</p>\n'
                '    <note kind="doubt">статья 5</note>\n  </text>\n</texts>\n'
            )
            path.write_text(source, encoding='utf-8')
            _, differences, total = refs.mark_translation(
                path, refs.Targets(REF_EIDS), {'u1': 'art_1'}, {('u1', 'base'): ['art_8']},
            )
            result = path.read_text(encoding='utf-8')
            self.assertEqual(total, 2)
            self.assertEqual(len(differences), 1)
            self.assertIn('<note kind="doubt">статья 5</note>', result)
            self.assertEqual(refs.strip_own_refs(result), source)
            refs.mark_translation(path, refs.Targets(REF_EIDS), {'u1': 'art_1'}, {})
            self.assertEqual(path.read_text(encoding='utf-8'), result)

    def test_translation_amending_ref_kept(self):
        """164(Ι)/2024 (lmpnxa5twc): пересборка базового закона заново размечает перевод
        изменяющего акта — ссылка «статье N основного/базового закона» остаётся."""
        for word in ('основного', 'базового'):
            with self.subTest(word=word), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'ru.xml'
                source = (
                    '<texts>\n  <text fragment="u1" version="base">\n'
                    '    <p>В <ref id="r1" to="%s#art_5">статье 5</ref> %s закона'
                    ' фраза заменяется</p>\n  </text>\n</texts>\n' % (LAW, word)
                )
                path.write_text(source, encoding='utf-8')
                _, differences, total = refs.mark_translation(
                    path, refs.Targets(REF_EIDS), {'u1': 'art_1'},
                    {('u1', 'base'): [LAW + '#art_5']}, LAW,
                )
                self.assertEqual(path.read_text(encoding='utf-8'), source)
                self.assertEqual((total, differences), (1, []))

    def test_translation_heading_only_record_not_text_start(self):
        """95(Ι)/2000: «<text … heading="…" />» перед записью — тело размечается по своему ключу."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ru.xml'
            path.write_text(
                '<texts>\n  <text fragment="u0" version="base" heading="Стоимость" />\n'
                '  <text fragment="u1" version="base">\n'
                '    <p>согласно статье 8 настоящего Закона</p>\n  </text>\n</texts>\n',
                encoding='utf-8',
            )
            el_marks = {('u0', 'base'): [], ('u1', 'base'): ['art_8']}
            _, differences, total = refs.mark_translation(
                path, refs.Targets(REF_EIDS), {'u0': 'art_1', 'u1': 'art_1'}, el_marks,
            )
            self.assertEqual(total, 1)
            self.assertEqual(differences, [])

class ActMentionsTests(SimpleTestCase):
    """Упоминание другого акта по названию или номеру → ссылка на акт целиком."""

    index = acts.Index(
        uids={
            'cy/law/2000/95_I', 'cy/law/2010/59_I', 'cy/law/2025/245_I',
            'eu/dir/2022/2523', 'eu/dir/2006/112', 'eu/reg/2010/904',
        },
        amending={
            ('Έκτακτης Εισφοράς για την Άμυνα της Δημοκρατίας', '2025', '4'):
                'cy/law/2025/245_I',
        },
    )

    def marked(self, text, lang='el', own='cy/kdp/2012/52', agreed=()):
        language = refs.EL if lang == 'el' else refs.RU
        marker = refs.Marker(
            refs.Targets(REF_EIDS), language, mentions=acts.Mentions(self.index, lang, own),
        )
        marker.agreed = set(agreed)
        paragraphs, found = marker.mark_text([text], 'art_1')
        return paragraphs[0], found

    def test_greek_forms(self):
        cases = {
            'του περί Κοινωνικών Ασφαλίσεων Νόμου': ['cy/law/2010/59_I'],
            'τους περί ΦΠΑ Νόμους του 2000 έως (Αρ. 2) του 2025': ['cy/law/2000/95_I'],
            'ο περί Φόρου Προστιθέμενης Αξίας Νόμος του 1990': [],
            'του περί Εταιρειών Νόμου': [],
            'του περί Έκτακτης Εισφοράς για την Άμυνα της Δημοκρατίας (Τροποποιητικού) '
            '(Αρ. 4) Νόμου του 2025': ['cy/law/2025/245_I'],
            'τον Νόμο 59(Ι)/2010': ['cy/law/2010/59_I'],
            'την Οδηγία (ΕΕ) 2022/2523 του Συμβουλίου': ['eu/dir/2022/2523'],
            'της οδηγίας 2006/112/ΕΚ': ['eu/dir/2006/112'],
            'του κανονισμού (ΕΕ) αριθ. 904/2010': ['eu/reg/2010/904'],
            'του Κανονισμού (ΕΕ) 2022/2472': [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.marked(text)[1], expected)

    def test_markup(self):
        text, _ = self.marked('των άρθρων 10 και 78 του περί Κοινωνικών Ασφαλίσεων Νόμου, αφού')
        self.assertIn(
            '<ref id="r1" to="cy/law/2010/59_I">περί Κοινωνικών Ασφαλίσεων Νόμου</ref>, αφού',
            text,
        )

    def test_own_act_not_marked(self):
        self.assertEqual(self.marked('59(Ι)/2010', own='cy/law/2010/59_I')[1], [])

    def test_russian_law_only_agreed(self):
        text = 'статей 10 и 78 Закона о социальном страховании'
        self.assertNotIn('cy/law/2010/59_I', self.marked(text, 'ru')[1])
        found = self.marked(text, 'ru', agreed=['cy/law/2010/59_I'])[1]
        self.assertIn('cy/law/2010/59_I', found)

    def test_russian_forms(self):
        agreed = ['cy/law/2000/95_I', 'cy/law/2025/245_I']
        cases = {
            'Законов о налоге на добавленную стоимость 2000 года до (№ 2) 2025 года':
                ['cy/law/2000/95_I'],
            'Закона о специальном взносе на оборону Республики (о внесении изменений) (№ 4) '
            '2025 года': ['cy/law/2025/245_I'],
            'Директива Совета (ЕС) 2022/2523': ['eu/dir/2022/2523'],
            'Регламент (ЕС) № 904/2010': ['eu/reg/2010/904'],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.marked(text, 'ru', agreed=agreed)[1], expected)


class PublicAttributeTests(TestCase):
    """Атрибут public у <expression>: читается загрузчиком, сохраняется при пересборке."""

    def test_public_kept_and_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'cy/law/2000/1_I/act.xml'
            path.parent.mkdir(parents=True)
            path.write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<act uid="cy/law/2000/1_I" jurisdiction="cy" kind="law">\n'
                '  <expression lang="el" status="official_text" source="cylaw" file="el.xml"'
                ' public="true" />\n'
                '  <expression lang="ru" status="our_translation" source="nomos" file="ru.xml"'
                ' public="true" />\n'
                '</act>\n',
                encoding='utf-8',
            )
            info = texts_xml.ActInfo(
                uid='cy/law/2000/1_I', jurisdiction='cy', kind='law', source='cylaw',
            )
            texts_xml.write_act(path, info, {}, {})
            public = {
                item.get('lang'): item.get('public')
                for item in texts_xml.read_act(path).iter('expression')
            }
            self.assertEqual(public, {'el': 'true', 'ru': 'true'})
            call_command('load_texts', texts_dir=folder, stdout=StringIO())
        self.assertEqual(
            dict(Expression.objects.values_list('lang', 'public')), {'el': True, 'ru': True},
        )



def write_simple_act(root, uid, kind, text, fragment, source='cy-tax'):
    """Акт из одного фрагмента: act.xml и el.xml."""
    folder = root / uid
    folder.mkdir(parents=True)
    (folder / 'act.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<act uid="%s" jurisdiction="cy" kind="%s" structure-lang="el">\n'
        '  <expression lang="el" status="official_text" source="%s" file="el.xml" />\n'
        '  <amendment key="base" kind="base" published-on="2024-08-06"></amendment>\n'
        '  <fragment uid="%s" kind="sub" eid="sub_1" order-key="0001">\n'
        '    <version key="base" amendment="%s#base" valid-from="2024-08-06"'
        ' known-from="2024-08-06" />\n'
        '  </fragment>\n'
        '</act>\n' % (uid, kind, source, fragment, uid),
        encoding='utf-8',
    )
    (folder / 'el.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<texts act="%s" lang="el">\n'
        '  <text fragment="%s" version="base">\n'
        '    <p>%s</p>\n'
        '  </text>\n'
        '</texts>\n' % (uid, fragment, text),
        encoding='utf-8',
    )


class FormKindTests(TestCase):
    """Бланки налоговой — акты вида form/leaflet; ссылки на них разрешаются."""

    def test_form_reference_resolved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_simple_act(root, 'cy/form/vat/4B', 'form', 'Αίτηση επιστροφής', 'formfrag01')
            write_simple_act(
                root, 'cy/instr/2024/guide', 'instr',
                'Βλέπε <ref id="r1" to="cy/form/vat/4B">έντυπο 4Β</ref> και'
                ' <ref id="r2" to="cy/leaflet/vat/3A">Ενημερωτικό Έντυπο 3Α</ref>.',
                'guidefrag1',
            )
            call_command('load_texts', texts_dir=folder, stdout=StringIO())
        references = {
            reference.anchor: reference
            for reference in Reference.objects.filter(from_text__expression__lang='el')
        }
        form = references['r1']
        self.assertTrue(form.resolved)
        self.assertEqual((form.to_act.uid, form.to_act.kind), ('cy/form/vat/4B', 'form'))
        # информационного бланка 3A в текстах нет: ссылка остаётся неразрешённой
        leaflet = references['r2']
        self.assertFalse(leaflet.resolved)
        self.assertIsNone(leaflet.to_act)

    def test_social_insurance_form_source(self):
        """Бланк Υπηρεσίες Κοινωνικών Ασφαλίσεων: источник cy-sis есть в списке источников."""
        with tempfile.TemporaryDirectory() as folder:
            write_simple_act(
                Path(folder), 'cy/form/yka/1-003', 'form', 'Δήλωση πρόσληψης', 'formfrag02',
                source='cy-sis',
            )
            call_command('load_texts', texts_dir=folder, stdout=StringIO())
        expression = Expression.objects.get(act__uid='cy/form/yka/1-003')
        self.assertEqual(expression.source.code, 'cy-sis')


class LostUidsTests(SimpleTestCase):
    """Пересборка не убирает фрагменты, у которых есть перевод."""

    def test_translated_fragment_lost(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'act.xml').write_text(
                '<act uid="cy/law/2000/1_I">\n'
                '  <fragment uid="aaaaaaaaaa" kind="art" eid="art_1" />\n'
                '  <fragment uid="bbbbbbbbbb" kind="art" eid="art_2" />\n'
                '  <fragment uid="cccccccccc" kind="art" eid="art_3" />\n'
                '</act>\n',
                encoding='utf-8',
            )
            (root / 'ru.xml').write_text(
                '<texts>\n'
                '  <text fragment="bbbbbbbbbb" version="base"><p>Статья 2</p></text>\n'
                '</texts>\n',
                encoding='utf-8',
            )
            # art_2 с переводом и art_3 без него пропали из сборки
            lost = texts_xml.lost_uids(root, {'art_1': 'aaaaaaaaaa'})
        self.assertEqual(lost, [('art_2', 'bbbbbbbbbb')])


class Place:
    """Фрагмент нового разбора для follow_texts: только eid."""

    def __init__(self, eid):
        self.eid = eid


class FollowTextsTests(SimpleTestCase):
    """Пересборка новым разбором: uid и перевод идут за текстом."""

    info = texts_xml.ActInfo(uid='cy/law/2000/1_I', jurisdiction='cy', kind='law')

    def test_point_from_article_line_takes_uid(self):
        """«42.—(1)(α)»: текст εδαφίου ушёл в пункт (α) — пункт получает uid εδαφίου."""
        paragraph = Place('art_42.par_1')
        point = Place('art_42.par_1.pnt_a')
        uids = {paragraph: 'aaaaaaaaaa', point: 'newnewnewn'}
        old = {'aaaaaaaaaa': {'base': '(α) Πρόσωπο:'}}
        new = {paragraph: {}, point: {'base': 'Πρόσωπο:'}}
        nums = {paragraph: '(1)', point: '(α)'}
        eids = {'aaaaaaaaaa': 'art_42.par_1'}
        texts_xml.follow_texts(self.info, uids, new, old, nums, eids)
        self.assertEqual(uids[point], 'aaaaaaaaaa')
        self.assertNotIn(uids[paragraph], ('aaaaaaaaaa', 'newnewnewn'))

    def test_same_text_goes_to_closest_eid(self):
        """Одинаковый текст в двух местах: uid — того, чей прежний eid ближе."""
        first = Place('sch_10.par_12.sub_1.pnt_a.pnt_i')
        second = Place('sch_10.par_13.sub_1.pnt_a.pnt_i')
        uids = {first: 'new1new1ne', second: 'new2new2ne'}
        old = {'aaaaaaaaaa': {'base': 'ή'}, 'bbbbbbbbbb': {'base': 'ή'}}
        new = {first: {'base': 'ή'}, second: {'base': 'ή'}}
        nums = {first: '(i)', second: '(i)'}
        eids = {
            'aaaaaaaaaa': 'sch_10.par_12.sub_1.pnt_i',
            'bbbbbbbbbb': 'sch_10.par_13.sub_1.pnt_i',
        }
        texts_xml.follow_texts(self.info, uids, new, old, nums, eids)
        self.assertEqual(uids[first], 'aaaaaaaaaa')
        self.assertEqual(uids[second], 'bbbbbbbbbb')

    def test_unchanged_text_keeps_uid(self):
        node = Place('art_1')
        uids = {node: 'aaaaaaaaaa'}
        old = {'aaaaaaaaaa': {'base': 'Ένα.'}, 'bbbbbbbbbb': {'base': 'Ένα.'}}
        texts_xml.follow_texts(
            self.info, uids, {node: {'base': 'Ένα.'}}, old, {node: '1'}, {},
        )
        self.assertEqual(uids[node], 'aaaaaaaaaa')

    def test_page_break_merge_joins_translation(self):
        """Абзац, разорванный переносом страницы, склеен: перевод хвоста — в конец абзаца."""
        parent = Place('art_38.par_2')
        uids = {parent: 'aaaaaaaaaa'}
        old = {'aaaaaaaaaa': {'base': 'επιχείρηση που'}, 'bbbbbbbbbb': {'base': 'ασκείται.'}}
        new = {parent: {'base': 'επιχείρηση που ασκείται.'}}
        merged = texts_xml.merged_uids(uids, new, old, ['bbbbbbbbbb'])
        self.assertEqual(merged, [('aaaaaaaaaa', 'bbbbbbbbbb')])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ru.xml').write_text(
                '<texts>\n'
                '  <text fragment="aaaaaaaaaa" version="base" review="draft">\n'
                '    <p>предприятие,</p>\n'
                '  </text>\n'
                '  <text fragment="bbbbbbbbbb" version="base" review="draft">\n'
                '    <p>осуществляемое.</p>\n'
                '    <p>Второй абзац.</p>\n'
                '  </text>\n'
                '</texts>\n',
                encoding='utf-8',
            )
            texts_xml.merge_translations(root, merged)
            result = (root / 'ru.xml').read_text(encoding='utf-8')
        self.assertEqual(
            result,
            '<texts>\n'
            '  <text fragment="aaaaaaaaaa" version="base" review="draft">\n'
            '    <p>предприятие, осуществляемое.</p>\n'
            '    <p>Второй абзац.</p>\n'
            '  </text>\n'
            '</texts>\n',
        )

    def test_not_continuation_not_merged(self):
        parent = Place('art_1')
        uids = {parent: 'aaaaaaaaaa'}
        old = {'aaaaaaaaaa': {'base': 'Ένα.'}, 'bbbbbbbbbb': {'base': 'Δύο.'}}
        new = {parent: {'base': 'Ένα.'}}
        self.assertEqual(texts_xml.merged_uids(uids, new, old, ['bbbbbbbbbb']), [])

    def test_fragment_became_heading(self):
        """Абзац прежнего разбора стал заголовком следующего пункта: перевод — в heading."""
        place = Place('sch_13.prt_I.par_4')
        uids = {place: 'bbbbbbbbbb'}
        headings = {place: {'base': 'Πρόσβαση κ.τ.λ. εκδηλώσεις.'}}
        old = {
            'aaaaaaaaaa': {'base': 'Πρόσβαση κ.τ.λ. εκδηλώσεις.'},
            'bbbbbbbbbb': {'base': 'Υπηρεσίες.'},
        }
        triples = texts_xml.headed_uids(uids, headings, old, ['aaaaaaaaaa'])
        self.assertEqual(triples, [('aaaaaaaaaa', 'bbbbbbbbbb', 'base')])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ru.xml').write_text(
                '<texts>\n'
                '  <text fragment="aaaaaaaaaa" version="base" review="draft">\n'
                '    <p>Доступ на «мероприятия».</p>\n'
                '    <note kind="doubt">заголовок?</note>\n'
                '  </text>\n'
                '  <text fragment="bbbbbbbbbb" version="base" review="draft">\n'
                '    <p>Услуги.</p>\n'
                '  </text>\n'
                '</texts>\n',
                encoding='utf-8',
            )
            texts_xml.move_to_headings(root, triples)
            result = (root / 'ru.xml').read_text(encoding='utf-8')
        self.assertEqual(
            result,
            '<texts>\n'
            '  <text fragment="bbbbbbbbbb" version="base" heading="Доступ на «мероприятия»."'
            ' review="draft">\n'
            '    <note kind="doubt">заголовок?</note>\n'
            '    <p>Услуги.</p>\n'
            '  </text>\n'
            '</texts>\n',
        )


GUIDE = 'cy/instr/2024/vat_return_guide'


@skipUnless((TEXTS_DIR / GUIDE / 'act.xml').exists(), 'нет Nomos-texts с руководством по НДС')
class VatReturnGuideTests(TestCase):
    """Загрузка руководства по заполнению декларации НДС из Nomos-texts (только чтение)."""

    @classmethod
    def setUpTestData(cls):
        call_command('load_texts', texts_dir=str(TEXTS_DIR), stdout=StringIO())

    def test_act_and_relation(self):
        relation = ActRelation.objects.get(from_act__uid=GUIDE)
        self.assertEqual(relation.from_act.kind, 'instr')
        self.assertEqual((relation.kind, relation.to_act.uid), ('explains', LAW))
        sources = dict(
            Expression.objects.filter(act__uid=GUIDE).values_list('lang', 'source__code')
        )
        self.assertEqual(sources.pop('el'), 'cy-tax')
        # русский перевод может быть, а может ещё не быть
        self.assertLessEqual(set(sources), {'ru'})
        if 'ru' in sources:
            self.assertEqual(sources['ru'], 'nomos')

    def test_fields(self):
        fields = Fragment.objects.filter(act__uid=GUIDE, kind='fld')
        self.assertEqual(
            sorted(fields.values_list('eid', flat=True)),
            sorted(
                'fld_%s' % num
                for num in ['1', '2', '3', '4', '5', '6', '7', '8A', '8B', '9', '10', '11A', '11B']
            ),
        )
        self.assertTrue(all(field.parent.eid == 'sct_G' for field in fields))

    def test_greek_refs(self):
        texts = FragmentText.objects.filter(version__fragment__act__uid=GUIDE)
        greek = texts.filter(expression__lang='el')
        self.assertEqual(greek.count(), 145)
        references = Reference.objects.filter(from_text__in=greek)
        self.assertEqual(references.count(), 31)
        # бланки 4B, 3A, 14 (cy/form/…, cy/leaflet/…): их текстов в базе нет
        forms = references.filter(resolved=False)
        self.assertEqual(forms.count(), 3)
        self.assertFalse(forms.exclude(to_act=None).exists())
        self.assertFalse(references.filter(resolved=True).exclude(to_act__uid=LAW).exists())

    def test_translation_if_present(self):
        """Русский — если перевод уже есть: фрагменты те же, ссылки разрешены."""
        texts = FragmentText.objects.filter(version__fragment__act__uid=GUIDE)
        russian = texts.filter(expression__lang='ru')
        if not russian.exists():
            self.skipTest('перевода руководства ещё нет')
        greek_fragments = set(
            texts.filter(expression__lang='el').values_list('version__fragment', flat=True)
        )
        self.assertLessEqual(
            greek_fragments, set(russian.values_list('version__fragment', flat=True)),
        )
        references = Reference.objects.filter(from_text__in=russian)
        self.assertFalse(references.filter(resolved=False).exclude(to_act=None).exists())
        self.assertEqual(references.filter(resolved=False).count(), 3)
        # заголовки разделов (в el — в act.xml) переведены записями без текста
        headed = FragmentVersion.objects.filter(fragment__act__uid=GUIDE).exclude(heading='')
        self.assertEqual(
            set(russian.exclude(heading='').values_list('version', flat=True)),
            set(headed.values_list('pk', flat=True)),
        )


TAX_LAW = 'cy/law/1978/4'


@skipUnless((TEXTS_DIR / TAX_LAW / 'collect.xml').exists(), 'нет Nomos-texts с законом 4/1978')
class TaxCollectionLawTests(TestCase):
    """Сборка и загрузка закона о начислении и взимании налогов 4/1978."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        call_command('collect_cylaw', TAX_LAW, texts_dir=str(cls.copy), stdout=output)
        cls.report = output.getvalue()
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def version(self, eid, day):
        return Fragment.objects.get(act__uid=TAX_LAW, eid=eid).version_on(day)

    def text(self, eid, day):
        version = self.version(eid, day)
        return FragmentText.objects.get(version=version, expression__lang='el').body

    def test_rebuild_keeps_files(self):
        folder = self.copy / TAX_LAW
        changed = [
            path.name
            for path in folder.iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / TAX_LAW / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def test_all_ops_applied(self):
        """В отчёте нет неснятых изменений; заметки (note) — только для сведения."""
        problems = [
            line.strip()
            for line in self.report.splitlines()
            if ('→' in line and not line.endswith(': не применено')) or 'не найдена' in line
        ]
        self.assertEqual(problems, [])

    def test_article_26a_repealed_from_2026(self):
        self.assertEqual(self.version('art_26A', datetime.date(2025, 12, 31)).state, 'active')
        self.assertIsNone(self.version('art_26A', datetime.date(2026, 1, 1)))

    def test_article_48a_from_july_2026(self):
        self.assertIsNone(self.version('art_48A', datetime.date(2026, 6, 30)))
        self.assertEqual(self.version('art_48A', datetime.date(2026, 7, 1)).state, 'active')

    def test_article_50h_by_law_49_2025(self):
        self.assertIsNone(self.version('art_50H', datetime.date(2025, 4, 15)))
        self.assertEqual(self.version('art_50H', datetime.date(2025, 4, 16)).state, 'active')

    def test_fine_amount_changed(self):
        eid = 'art_50ST.par_1'
        self.assertIn('τέσσερις χιλιάδες ευρώ', self.text(eid, datetime.date(2025, 12, 31)))
        self.assertIn('έξι χιλιάδες ευρώ', self.text(eid, datetime.date(2026, 1, 1)))


SOCIAL_LAW = 'cy/law/2010/59_I'


@skipUnless(
    (TEXTS_DIR / SOCIAL_LAW / 'collect.xml').exists(), 'нет Nomos-texts с законом 59(I)/2010',
)
class SocialInsuranceLawTests(TestCase):
    """Сборка и загрузка закона о социальном страховании 59(Ι)/2010."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        call_command('collect_cylaw', SOCIAL_LAW, texts_dir=str(cls.copy), stdout=output)
        cls.report = output.getvalue()
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def version(self, eid, day):
        return Fragment.objects.get(act__uid=SOCIAL_LAW, eid=eid).version_on(day)

    def text(self, eid, day):
        version = self.version(eid, day)
        return FragmentText.objects.get(version=version, expression__lang='el').body

    def test_rebuild_keeps_files(self):
        folder = self.copy / SOCIAL_LAW
        changed = [
            path.name
            for path in folder.iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / SOCIAL_LAW / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def test_all_ops_applied(self):
        """Неизвестен только прежний текст замен целиком; прочие изменения сняты."""
        problems = [
            line.strip()
            for line in self.report.splitlines()
            if ('→' in line and not line.endswith(('не применено', 'прежний текст не приведён')))
            or 'не найдена' in line
        ]
        self.assertEqual(problems, [])

    def test_maternity_weeks_by_law_15_2024(self):
        eid = 'art_29.par_4.pnt_a'
        self.assertIn('δεκαοκτώ (18)', self.text(eid, datetime.date(2024, 2, 29)))
        self.assertIn('είκοσι δύο (22)', self.text(eid, datetime.date(2024, 3, 1)))

    def test_fine_by_law_127_2024(self):
        eid = 'art_85A.par_1'
        self.assertIn('πεντακόσιων ευρώ', self.text(eid, datetime.date(2024, 11, 14)))
        self.assertIn('χιλίων ευρώ', self.text(eid, datetime.date(2024, 11, 15)))

    def test_parental_leave_change_from_march_2025(self):
        eid = 'art_29B.par_2.sub_2'
        self.assertIsNone(self.version(eid, datetime.date(2025, 3, 30)))
        self.assertEqual(self.version(eid, datetime.date(2025, 3, 31)).state, 'active')

    def test_schedules_parsed(self):
        eids = set(Fragment.objects.filter(act__uid=SOCIAL_LAW).values_list('eid', flat=True))
        self.assertLessEqual({'sch_1', 'sch_7', 'sch_3.par_8'}, eids)


    def test_schedule_tables_in_cells(self):
        """Πίνακες 4, 5: столбцы — ячейки `cel` таблицы `tbl`, пункты ячейки — её дети."""
        fragments = Fragment.objects.filter(act__uid=SOCIAL_LAW)
        self.assertEqual(fragments.filter(kind='tbl', eid__startswith='sch_').count(), 9)
        cell = fragments.get(eid='sch_5.prt_II.tbl_1.cel_h1-2_1')
        self.assertEqual(cell.kind, 'cel')
        self.assertEqual(cell.parent.eid, 'sch_5.prt_II.tbl_1')
        self.assertIn(
            'Συμπληρωματικό',
            self.text('sch_4.prt_VI.pnt_a.tbl_1.cel_h1_2', datetime.date(2024, 1, 1)),
        )
        self.assertTrue(fragments.filter(eid='sch_4.prt_IV.tbl_1.cel_1_2.pnt_b.pnt_ii').exists())

CONTRIBUTIONS = 'cy/kdp/2010/289'


@skipUnless(
    (TEXTS_DIR / CONTRIBUTIONS / 'collect.xml').exists(), 'нет Nomos-texts с ΚΔΠ 289/2010',
)
class ContributionRegulationsTests(TestCase):
    """Регламент о взносах ΚΔΠ 289/2010: расшифровка скана, 4Α из ΚΔΠ 52/2012."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        call_command('collect_cylaw', CONTRIBUTIONS, texts_dir=str(cls.copy), stdout=output)
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def test_rebuild_keeps_files(self):
        changed = [
            '%s/%s' % (act, path.name)
            for act in (CONTRIBUTIONS, 'cy/kdp/2012/52')
            for path in (self.copy / act).iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / act / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def test_based_on_law(self):
        kinds = set(ActRelation.objects.filter(
            from_act__uid=CONTRIBUTIONS, to_act__uid=SOCIAL_LAW,
        ).values_list('kind', flat=True))
        self.assertEqual(kinds, {'based_on'})
        self.assertTrue(ActRelation.objects.filter(
            from_act__uid='cy/kdp/2012/52', to_act__uid=CONTRIBUTIONS, kind='amends',
        ).exists())

    def test_structure(self):
        eids = set(Fragment.objects.filter(act__uid=CONTRIBUTIONS).values_list('eid', flat=True))
        expected = {
            'art_2.par_1.def_nomos',
            'art_4A.par_1.pnt_a.pnt_z',
            'art_4A.par_4',
            'art_35',
            'sch_1.par_16',
            'sch_2',
        }
        self.assertLessEqual(expected, eids)



ORDERS = ('cy/kdp/2024/392', 'cy/kdp/2025/378')


@skipUnless(
    all((TEXTS_DIR / order / 'collect.xml').exists() for order in ORDERS),
    'нет Nomos-texts с приказами ΚΔΠ 392/2024 и 378/2025',
)
class BasicEarningsOrderTests(TestCase):
    """Приказы о базовых заработках (ст. 20 закона 59(Ι)/2010): ст. 2 без определений."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        for order in ORDERS:
            call_command('collect_cylaw', order, texts_dir=str(cls.copy), stdout=output)
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def test_rebuild_keeps_files(self):
        changed = [
            '%s/%s' % (order, path.name)
            for order in ORDERS
            for path in (self.copy / order).iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / order / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def test_based_on_law(self):
        for order in ORDERS:
            self.assertTrue(ActRelation.objects.filter(
                from_act__uid=order, to_act__uid=SOCIAL_LAW, kind='based_on',
            ).exists())

    def test_known_from_publication(self):
        """Опубликован после 01.01.2024 — известен с даты публикации."""
        fragment = Fragment.objects.get(act__uid='cy/kdp/2025/378', eid='art_2')
        dates = set(fragment.versions.values_list('known_from', flat=True))
        self.assertEqual(dates, {datetime.date(2025, 12, 12)})
        text = FragmentText.objects.get(
            version__fragment=fragment, expression__lang='el',
        ).body
        self.assertIn('€220,76', text)


HEALTH_LAW = 'cy/law/2001/89_I'


@skipUnless((TEXTS_DIR / HEALTH_LAW / 'collect.xml').exists(), 'нет Nomos-texts с 89(Ι)/2001')
class HealthSystemLawTests(TestCase):
    """Закон о ГеСИ 89(Ι)/2001 с изменениями 29, 53, 118, 139(Ι)/2025, 60 и 61(Ι)/2026."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        call_command('collect_cylaw', HEALTH_LAW, texts_dir=str(cls.copy), stdout=output)
        cls.report = output.getvalue()
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def version(self, eid, day):
        return Fragment.objects.get(act__uid=HEALTH_LAW, eid=eid).version_on(day)

    def text(self, eid, day):
        version = self.version(eid, day)
        return FragmentText.objects.get(version=version, expression__lang='el').body

    def test_rebuild_keeps_files(self):
        folder = self.copy / HEALTH_LAW
        changed = [
            path.name
            for path in folder.iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / HEALTH_LAW / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def test_all_ops_applied(self):
        """Кроме замен целиком без прежнего текста и примечаний, все изменения сняты."""
        problems = [
            line.strip()
            for line in self.report.splitlines()
            if ('→' in line and ':note →' not in line
                and not line.endswith(('не применено', 'прежний текст не приведён')))
            or 'не найдена' in line
            or 'положения нет' in line
        ]
        self.assertEqual(problems, [])

    def test_dental_proviso_from_law_29_2025(self):
        eid = 'art_22.par_5A'
        self.assertIsNone(self.version(eid, datetime.date(2025, 3, 30)))
        self.assertEqual(self.version(eid, datetime.date(2025, 3, 31)).state, 'active')

    def test_competent_authority_by_law_53_2025(self):
        self.assertIsNone(self.version('art_70', datetime.date(2025, 4, 24)))
        self.assertEqual(self.version('art_70', datetime.date(2025, 4, 25)).state, 'active')
        self.assertEqual(self.version('art_45', datetime.date(2025, 4, 24)).state, 'active')
        self.assertIsNone(self.version('art_45', datetime.date(2025, 4, 25)))
        eid = 'art_44.par_1'
        self.assertIn('ο Επίτροπος', self.text(eid, datetime.date(2025, 4, 24)))
        self.assertIn('η αρμόδια αρχή', self.text(eid, datetime.date(2025, 4, 25)))

    def test_counselling_psychologists_by_law_61_2026(self):
        eid = 'art_2.def_alloi_epaggelmaties_ygeias'
        phrase = 'ειδικότητα στη συμβουλευτική ψυχολογία'
        self.assertNotIn(phrase, self.text(eid, datetime.date(2026, 4, 8)))
        self.assertIn(phrase, self.text(eid, datetime.date(2026, 4, 9)))

    def test_subparagraph_by_law_118_2025(self):
        eid = 'art_19.par_4.pnt_b.sub_2'
        self.assertIsNone(self.version(eid, datetime.date(2025, 7, 10)))
        self.assertEqual(self.version(eid, datetime.date(2025, 7, 11)).state, 'active')


class LeginetTests(SimpleTestCase):
    """Сводка Leginet: поле и текст в одной строке (125(Ι)/1999)."""

    BREAK = leginet.PAGE_BREAK

    def test_margin_notes_join_rows(self):
        """Запись поля — до точки или до пустой строки; строки без поля не прерывают."""
        rows = [
            ('Συνοπτικός', '1. Ο παρών Νόμος', 12, self.BREAK),
            ('', 'συνέχεια', 12, None),
            ('τίτλος.', 'συνέχεια', 12, None),
            ('136(Ι) του 2002', '2. Άλλο', 12, ''),
            ('52(Ι) του 2006.', 'συνέχεια', 12, None),
            ('Ερμηνεία', '3. Τρίτο', 12, ''),
            ('Πίνακας.', '(2) Το έμβλημα', 12, ''),
        ]
        self.assertEqual(leginet.margin_notes(rows), [
            (0, 'Συνοπτικός τίτλος.'),
            (3, '136(Ι) του 2002 52(Ι) του 2006.'),
            (5, 'Ερμηνεία'),
            (6, 'Πίνακας.'),
        ])

    def test_law_references_are_not_headings(self):
        for text in ('136(Ι) του 2002 52(Ι) του 2006.', '8(α)(β) του 15(Ι) του 2026.',
                     'Κεφ. 113.', '2 του 15(Ι) του 2026 21(Ι) του 2007.'):
            self.assertIsNotNone(leginet.LAW_REF_RE.match(text), text)
        self.assertIsNone(leginet.LAW_REF_RE.match('Ερμηνεία.'))

    def test_article_headings(self):
        """Заголовок — у абзаца статьи, даже если начат строкой ниже; ссылка «Πίνακας.» у
        εδαφίου — не заголовок; статья без поля — заголовок из оглавления."""
        rows = [
            ('', '1. Ο παρών Νόμος', 12, ''),
            ('Συνοπτικός', 'συνέχεια', 12, None),
            ('τίτλος.', 'συνέχεια', 12, None),
            ('8(α)(β) του 15(Ι) του 2026.', '2. Άλλο', 12, ''),
            ('Πίνακας.', '(2) Το έμβλημα', 12, ''),
            ('', '28. Το Δικαστήριο', 12, ''),
        ]
        headings = leginet.article_headings(rows, {'2': 'Ερμηνεία', '28': 'Δικαιοδοσία.'})
        self.assertEqual(headings, {0: 'Συνοπτικός τίτλος.', 3: 'Ερμηνεία.', 5: 'Δικαιοδοσία.'})

    def test_toc_number_in_margin(self):
        """Оглавление сдвинуто влево: номер статьи — в поле."""
        rows = [('28.', 'Παροχή δικαιοδοσίας', 0, None), ('28.', 'Άλλο', 0, None)]
        self.assertEqual(leginet.toc_headings(rows), {'28': 'Παροχή δικαιοδοσίας'})

    def test_pdf_lines(self):
        """Оглавление отброшено, заголовок — своей строкой, статья изменяющего закона
        («Ο παρών Νόμος [15(Ι)/2026]») пропущена."""
        rows = [
            ('1.', 'Συνοπτικός τίτλος', 0, self.BREAK),
            ('', 'ΕΠΕΙΔΗ είναι σκόπιμο', 12, self.BREAK),
            ('Συνοπτικός τίτλος.', '1. Ο παρών Νόμος', 12, ''),
            ('', '11. Ο παρών Νόμος [15(Ι)/2026] τίθεται σε ισχύ', 12, ''),
            ('', 'την 6η Απριλίου.', 12, None),
            ('', '2. Άλλο', 12, ''),
        ]
        with patch.object(leginet, 'rows', return_value=rows):
            lines = leginet.pdf_lines('x.pdf')
        self.assertEqual(lines, [
            'Προοίμιο', '', self.BREAK, ' ' * 12 + 'ΕΠΕΙΔΗ είναι σκόπιμο',
            '', '', ' ' * 6 + 'Συνοπτικός τίτλος.', '', ' ' * 12 + '1. Ο παρών Νόμος',
            '', ' ' * 12 + '2. Άλλο',
        ])

    def test_schedule_without_number(self):
        """Единственный «ΠΙΝΑΚΑΣ (Αρθρο 3(2))» по центру — sch_1."""
        block = layout.Block(0, 40, 'ΠΙΝΑΚΑΣ (Αρθρο 3(2))')
        self.assertEqual(consolidated.roman_schedule(block), 1)



class CustomsLayoutTests(SimpleTestCase):
    """Сводка Τμήμα Τελωνείων: раскладка как у Leginet, колонки свои (94(Ι)/2004)."""

    BREAK = leginet.PAGE_BREAK

    def test_columns_from_article_numbers(self):
        """Край текста — по номеру статьи с заголовком на поле слева; номер в оглавлении
        (без слова левее) не считается. Просвет — по самому частому шагу строк."""
        pages = [[
            (56, 100, 70, '1.'),
            (80, 100, 150, 'Συνοπτικός'),
            (40, 200, 90, 'Ερμηνεία.'),
            (121, 201, 130, '2.'),
            (40, 300, 90, 'Πεδίο.'),
            (121, 300, 130, '3.'),
            (130, 319, 300, 'κείμενο'),
            (130, 338, 300, 'κείμενο'),
            (130, 357, 300, 'κείμενο'),
        ]]
        found = customs.columns_of(pages)
        self.assertEqual(found.text_left, 120)
        self.assertEqual(found.margin_right, 118)
        self.assertEqual(found.gap, 19 * customs.GAP_RATIO)

    def test_article_shifted_by_space(self):
        """«86.- (1)» с лишним пробелом — отступ текста и начало абзаца."""
        rows = [('', '86.- (1) Ο Διευθυντής', 13, None)]
        self.assertEqual(customs.mark_articles(rows), [('', '86.- (1) Ο Διευθυντής', 12, '')])

    def test_part_after_page_break(self):
        """Μέρος сразу за разрывом страницы не продолжает абзац; название — в той же строке."""
        lines = [
            ' ' * 12 + '9. Ο Διευθυντής δύναται.',
            self.BREAK,
            ' ' * 40 + 'ΜΕΡΟΣ V',
            '',
            ' ' * 14 + 'ΕΞΟΥΣΙΕΣ ΛΕΙΤΟΥΡΓΩΝ',
            '',
            ' ' * 6 + 'Ερμηνεία.',
        ]
        self.assertEqual(customs.close_before_centered(customs.join_parts(lines)), [
            ' ' * 12 + '9. Ο Διευθυντής δύναται.',
            '',
            self.BREAK,
            ' ' * 40 + 'ΜΕΡΟΣ V ΕΞΟΥΣΙΕΣ ΛΕΙΤΟΥΡΓΩΝ',
            '',
            '',
            ' ' * 6 + 'Ερμηνεία.',
        ])

    def test_schedule_heading_left_of_center(self):
        """Первая строка заголовка пункта Παράρτημα левее центра — тоже по центру."""
        lines = [
            '',
            ' ' * 17 + 'Εξουσία για διενέργεια πράξεων',
            '',
            ' ' * 28 + 'που κατασχέθηκε',
            '',
            ' ' * 12 + '13. Σε περίπτωση',
        ]
        self.assertEqual(
            customs.center_headings(lines)[1],
            ' ' * 25 + 'Εξουσία για διενέργεια πράξεων',
        )
        items = ['', ' ' * 15 + '(α) Του προσώπου', '', ' ' * 28 + 'του κατόχου', '', 'x']
        self.assertEqual(customs.center_headings(items), items)

    def test_split_schedule(self):
        """Страница с «ΠΑΡΑΡΤΗΜΑ» по центру делится: выше — статьи, с него — приложение."""
        columns = leginet.Columns(118, 120, 210, customs.FURNITURE_RE, customs.HEADING_SHIFT)
        page = [(121, 100, 300, '127.'), (250, 200, 330, 'ΠΑΡΑΡΤΗΜΑ'), (70, 300, 90, '1.')]
        body, schedule = customs.split_schedule([[(121, 50, 130, '1.')], page], columns)
        self.assertEqual(body, [[(121, 50, 130, '1.')], [(121, 100, 300, '127.')]])
        self.assertEqual(schedule, [[(250, 200, 330, 'ΠΑΡΑΡΤΗΜΑ'), (70, 300, 90, '1.')]])

    def test_lone_schedule(self):
        """Единственное «ΠΑΡΑΡΤΗΜΑ» по центру — sch_1 только у сводок Τμήμα Τελωνείων."""
        block = layout.Block(0, 40, 'ΠΑΡΑΡΤΗΜΑ')
        self.assertTrue(consolidated.Parser('x', lone_schedule=True).is_lone_schedule(block))
        self.assertFalse(consolidated.Parser('x').is_lone_schedule(block))


CUSTOMS_CODE = 'cy/law/2004/94_I'


@skipUnless((TEXTS_DIR / CUSTOMS_CODE / 'collect.xml').exists(), 'нет Nomos-texts с 94(Ι)/2004')
class CustomsCodeTests(SimpleTestCase):
    """Таможенный кодекс 94(Ι)/2004 по сводке Τμήμα Τελωνείων."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = TEXTS_DIR / 'sources' / CUSTOMS_CODE / 'consolidated-customs.pdf'
        cls.root, _, _ = consolidated.parse(path, CUSTOMS_CODE, 'cy-customs')

    def test_structure(self):
        """23 Μέρη, 131 статья по оглавлению, одно приложение из 14 пунктов."""
        parts = [node for node in self.root.children if node.kind == 'prt']
        self.assertEqual(len(parts), 23)
        articles = [node for node in self.root.walk() if node.kind == 'art']
        self.assertEqual(len(articles), 131)
        schedule = self.root.children[-1]
        self.assertEqual(schedule.eid, 'sch_1')
        self.assertEqual(len(schedule.children), 14)
        self.assertEqual(
            schedule.children[12].heading,
            'Εξουσία για διενέργεια ορισμένων πράξεων αναφορικά με το εμπόρευμα που '
            'κατασχέθηκε πριν την κήρυξη του εις δήμευση',
        )

    def test_part_after_page_break(self):
        part = next(node for node in self.root.children if node.eid == 'prt_III')
        self.assertEqual(part.heading, 'ΜΕΡΟΣ ΙΙΙ ΛΙΜΕΝΕΣ ΚΑΙ ΑΕΡΟΛΙΜΕΝΕΣ')
        self.assertEqual(part.children[0].eid, 'art_10')


HRDA_LAW = 'cy/law/1999/125_I'
HRDA_FEE = 'cy/kdp/2026/128'
HRDA_AMENDING = 'cy/law/2026/15_I'


@skipUnless(
    all((TEXTS_DIR / act / 'collect.xml').exists() for act in (HRDA_LAW, HRDA_FEE)),
    'нет Nomos-texts с 125(Ι)/1999 и ΚΔΠ 128/2026',
)
class HumanResourcesLawTests(TestCase):
    """Закон об АРЧР 125(Ι)/1999 по сводке Leginet и регламент о сборе ΚΔΠ 128/2026."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        for act in (HRDA_LAW, HRDA_FEE):
            call_command('collect_cylaw', act, texts_dir=str(cls.copy), stdout=output)
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def test_rebuild_keeps_files(self):
        changed = [
            '%s/%s' % (act, path.name)
            for act in (HRDA_LAW, HRDA_FEE, HRDA_AMENDING)
            for path in (self.copy / act).iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / act / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def heading(self, act, eid, key='base'):
        fragment = Fragment.objects.get(act__uid=act, eid=eid)
        return fragment.versions.get(key=key).heading

    def body(self, eid, key):
        return FragmentText.objects.get(
            version__fragment__act__uid=HRDA_LAW,
            version__fragment__eid=eid,
            version__key=key,
            expression__lang='el',
        ).body

    def test_law_structure(self):
        eids = set(Fragment.objects.filter(act__uid=HRDA_LAW).values_list('eid', flat=True))
        expected = {'prt_VI', 'art_14A', 'art_20.par_1A', 'art_40', 'sch_1'}
        self.assertLessEqual(expected, eids)
        definitions = [eid for eid in eids if eid.startswith('art_2.def_') and eid.count('.') == 1]
        self.assertEqual(len(definitions), 20)

    def test_law_headings(self):
        self.assertEqual(
            self.heading(HRDA_LAW, 'art_20', '2026-04-06'),
            'Υποχρέωση εργοδοτών και αυτοτελώς εργαζομένων για καταβολή τέλους.',
        )
        self.assertEqual(
            self.heading(HRDA_LAW, 'art_20'),
            'Υποχρέωση εργοδοτών για καταβολή τέλους.',
        )
        # нет на поле — из оглавления
        self.assertEqual(
            self.heading(HRDA_LAW, 'art_28'),
            'Παροχή δικαιοδοσίας στο Δικαστήριο Εργατικών Διαφορών.',
        )

    def test_amending_law_2026(self):
        # Ν. 15(Ι)/2026: прежний текст снят с изменений
        phrase = 'για οποιαδήποτε κατηγορία ή κατηγορίες εργοδοτουμένων'
        self.assertIn(phrase, self.body('art_5.pnt_d', 'base'))
        self.assertNotIn(phrase, self.body('art_5.pnt_d', '2026-04-06'))
        self.assertNotIn('αυτοτελώς', self.body('art_31.par_1', 'base'))
        fragment = Fragment.objects.get(act__uid=HRDA_LAW, eid='art_20.par_1A')
        self.assertEqual(
            sorted(fragment.versions.values_list('key', 'valid_from')),
            [('2026-04-06', datetime.date(2026, 4, 6))],
        )

    def test_fee_regulations(self):
        self.assertTrue(ActRelation.objects.filter(
            from_act__uid=HRDA_FEE, to_act__uid=HRDA_LAW, kind='based_on',
        ).exists())
        eids = set(Fragment.objects.filter(act__uid=HRDA_FEE).values_list('eid', flat=True))
        expected = {
            'art_2.par_1.def_periodos_teloys.pnt_g',
            'art_2.par_1.def_telos',
            'art_9.par_1.sub_1',
            'prt_III',
            'art_11',
        }
        self.assertLessEqual(expected, eids)
        fragment = Fragment.objects.get(act__uid=HRDA_FEE, eid='art_5.par_3')
        self.assertEqual(
            set(fragment.versions.values_list('known_from', flat=True)),
            {datetime.date(2026, 3, 11)},
        )


class IncomeTaxParsingTests(SimpleTestCase):
    """Правила разбора, добавленные для 118(Ι)/2002; акты до него собираются по-прежнему."""

    def eids(self, root, prefix):
        return [node.eid for node in root.walk() if node.eid.startswith(prefix)]

    def test_bracketed_and_undotted_article_numbers(self):
        """«(9A). 1.- …» и «9Β(1) …» — начало статьи с εδάφιο (1)."""
        root = parse_body('9. Δαπάνες.', '(9A). 1.- Ένα.', '9Β(1) Ανεξαρτήτως δύο.', '10. Άλλο.')
        texts = {node.eid: node.paragraphs for node in root.walk() if node.eid}
        self.assertEqual(texts['art_9A.par_1'], ['Ένα.'])
        self.assertEqual(texts['art_9B.par_1'], ['Ανεξαρτήτως δύο.'])

    def test_reference_with_subsection_is_not_article(self):
        """«4(1) του …» без буквы в номере — не начало статьи."""
        root = parse_body('3. Κείμενο που συνεχίζεται.', '4(1) του Νόμου ισχύει.')
        self.assertNotIn('art_4', self.eids(root, 'art_4'))

    def test_dashed_article_starts_item(self):
        """«39Α.-(1)» после колонтитула — новый блок; у актов до 118(Ι)/2002 — нет."""
        self.assertTrue(layout.starts_item('39Α.-(1) Κείμενο.'))
        self.assertFalse(layout.starts_item('39Α.-(1) Κείμενο.', dashed=False))

    def test_greek_nu_after_roman_four(self):
        """«(ν)» после «(iv)» — римская «(v)»; у 59(Ι)/2010 — по-прежнему."""
        lines = ('5.-(1) Κείμενο:', '(i) α·', '(ii) β·', '(iii) γ·', '(iv) δ·', '(ν) ε.')
        self.assertIn('art_5.par_1.pnt_v', self.eids(parse_body(*lines), 'art_5'))
        earlier = parse_body(*lines, act_uid='cy/law/2010/59_I')
        self.assertNotIn('art_5.par_1.pnt_v', self.eids(earlier, 'art_5'))

    def test_definition_after_points_of_definition(self):
        """Новый термин после пунктов определения — снова определение статьи 2."""
        root = parse_body(
            '2. Στον παρόντα Νόμο-',
            '«κάτοικος» σημαίνει-',
            '(α) στην περίπτωση ατόμου·',
            '(β) στην περίπτωση εταιρείας·',
            '«κηδεμόνας» σημαίνει τον κηδεμόνα.',
            '3. Άλλο.',
        )
        article = next(node for node in root.walk() if node.eid == 'art_2')
        consolidated.mark_definitions(article)
        definitions = [node.eid for node in article.children if node.kind == 'def']
        self.assertEqual(definitions, ['art_2.def_katoikos', 'art_2.def_khdemonas'])

    def test_uid_collisions_get_longer_suffix(self):
        """Третий фрагмент с тем же uid получает «~~», а не зацикливается на «~»."""
        nodes = [Node(kind='art', num=num) for num in ('1', '2', '3')]

        def make_uid(act, eid):
            return 'u' + '~' * eid.count('~')

        info = type('Info', (), {'uid': 'cy/law/2000/1_I'})()
        with patch.object(numbering, 'make_uid', make_uid):
            uids = texts_xml.assign_uids(info, nodes, Path('/nonexistent/act.xml'))
        self.assertEqual(sorted(uids.values()), ['u', 'u~', 'u~~'])


class IncomeTaxGazetteTests(SimpleTestCase):
    def test_inserted_article_heading_not_in_text(self):
        """243(Ι)/2025: «Συλλογή και    4ΑΑ.-(1) …» — заголовок на поле не входит в текст."""
        lines = [
            ' ' * 20 + '«Συλλογή και                4ΑΑ.-(1) Η συλλογή και επεξεργασία',
            ' ' * 20 + 'επεξεργασία             χαρακτήρα από τον Έφορο κατά την άσκηση',
        ]
        self.assertEqual(
            gazette.inner_margin(lines, 0, 24),
            '«4ΑΑ.-(1) Η συλλογή και επεξεργασία',
        )
        lines[0] = ' ' * 16 + '«Φορολογικές              7. Οσάκις εμπoρική ή βιoμηχαvική'
        self.assertEqual(gazette.inner_margin(lines, 0, 24), '«7. Οσάκις εμπoρική ή βιoμηχαvική')

    def test_law_citations_in_margin_not_in_text(self):
        """243, 164, 165: номера законов на поле — и без «(Ι)», и перед текстом при любом сдвиге."""
        lines = [
            ' ' * 30 + 'έχουν μεταφερθεί χορηγήσεις από πιστωτικό ίδρυμα·',
            ' ' * 14 + '22 του 1985',
            ' ' * 24 + '208(Ι) του 2015.   Νόμου του 2015-',
            ' ' * 24 + '96(Ι) του 2018.     (β)   οι διατάξεις των άρθρων 2, 3 και 4',
            ' ' * 50 + 'Εισοδήματος (Τροποποιητικού) (Αρ. 2) Νόμου του 2018',
        ]
        self.assertEqual(gazette.inner_margin(lines, 1, 24), '')
        self.assertEqual(gazette.inner_margin(lines, 2, 24), 'Νόμου του 2015-')
        self.assertEqual(
            gazette.inner_margin(lines, 3, 24),
            '(β)   οι διατάξεις των άρθρων 2, 3 και 4',
        )

    def test_margin_heading_column_below_text(self):
        """60(Ι)/2026, ст. 33Γ: столбец заголовка на поле ниже конца текста — не в текст."""
        lines = [
            ' ' * 19 + 'Συμβούλιο              εσωτερικούς κανονισμούς.',
            ' ' * 19 + 'Ιατροτεχνολογικών',
            ' ' * 19 + 'Προϊόντων και',
            ' ' * 19 + 'Υγειονομικών',
            ' ' * 19 + 'Ειδών.',
        ]
        for index in range(1, 5):
            with self.subTest(line=lines[index].strip()):
                self.assertEqual(gazette.inner_margin(lines, index, 24), '')

    def test_margin_narrow_gap_keeps_text(self):
        """243(Ι)/2025, ст. 48: «και σφράγιση των   επιχείρηση-» — поле и текст через 3 пробела."""
        lines = [
            ' ' * 16 + 'επιχείρησης        να σφραγίσει τα υποστατικά αυτής εφόσον',
            ' ' * 16 + 'και σφράγιση των   επιχείρηση-',
            ' ' * 16 + 'υποστατικών της.',
        ]
        self.assertEqual(gazette.inner_margin(lines, 1, 24), 'επιχείρηση-')
        self.assertEqual(gazette.inner_margin(lines, 2, 24), '')
        item = [
            ' ' * 59 + 'και',
            ' ' * 47 + '1277',
            ' ' * 50 + '(ii)   στην περίπτωση που η προθεσμία υποβολής της',
        ]
        self.assertIsNone(gazette.inner_margin(item, 2, 24))

    def test_justified_item_line_not_inner_margin(self):
        """243(Ι)/2025: пробелы выключки в строке «(δ) …» — не поле; перенос не теряется."""
        lines = [
            ' ' * 41 + '(δ)   χρηματική   επιβάρυνση     για    εκπρόθεσμη       υποβολή',
            ' ' * 47 + 'φορολογικής δήλωσης·',
            ' ' * 41 + '(ε)   αρχικό ποσό του φόρου και πρόσθετου φόρου που',
        ]
        self.assertIsNone(gazette.inner_margin(lines, 1, 40))

    def test_skipped_article_number(self):
        """244(Ι)/2025: за ст. 13 идёт 16 — статья с заголовком на поле принимается."""
        lines = gazette_lines(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
            ('Τροποποίηση', '2. Το άρθρο 5 τροποποιείται.'),
            ('Τροποποίηση', '4. Το άρθρο 7 τροποποιείται.'),
            ('', '6. Το άρθρο 9 χωρίς τίτλο στο περιθώριο.'),
        )
        root = gazette.GazetteParser('cy/law/2025/1_I').parse(lines)
        articles = [node.num for node in root.children if node.kind == 'art']
        self.assertEqual(articles, ['1', '2', '4'])


INCOME_TAX_LAW = 'cy/law/2002/118_I'


@skipUnless(
    (TEXTS_DIR / INCOME_TAX_LAW / 'collect.xml').exists(), 'нет Nomos-texts с 118(Ι)/2002',
)
class IncomeTaxLawTests(TestCase):
    """Закон о подоходном налоге 118(Ι)/2002 с изменениями 2024–2026 гг."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        call_command('collect_cylaw', INCOME_TAX_LAW, texts_dir=str(cls.copy), stdout=output)
        cls.report = output.getvalue()
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def version(self, eid, day):
        return Fragment.objects.get(act__uid=INCOME_TAX_LAW, eid=eid).version_on(day)

    def text(self, eid, day):
        version = self.version(eid, day)
        return FragmentText.objects.get(version=version, expression__lang='el').body

    def test_rebuild_keeps_files(self):
        folder = self.copy / INCOME_TAX_LAW
        changed = [
            path.name
            for path in folder.iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / INCOME_TAX_LAW / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def test_all_ops_applied(self):
        """Кроме замен целиком без прежнего текста и примечаний, все изменения сняты."""
        problems = [
            line.strip()
            for line in self.report.splitlines()
            if ('→' in line and ':note →' not in line
                and not line.endswith(('не применено', 'прежний текст не приведён')))
            or 'не найдена' in line
            or 'положения нет' in line
        ]
        self.assertEqual(problems, [])

    def test_structure(self):
        eids = set(Fragment.objects.filter(act__uid=INCOME_TAX_LAW).values_list('eid', flat=True))
        expected = {
            'art_9A', 'art_9B', 'art_20D', 'art_21A', 'art_39A', 'art_46A',
            'art_2.def_katoikos_ths_dhmokratias', 'art_8.par_21B', 'sch_2.par_2',
        }
        self.assertLessEqual(expected, eids)

    def test_restructuring_term_by_laws_164_2024_and_219_2025(self):
        eid = 'art_2.def_anadiarthrwsh.sub_1'
        self.assertIn('εννέα (9)', self.text(eid, datetime.date(2024, 12, 30)))
        self.assertIn('δέκα (10)', self.text(eid, datetime.date(2024, 12, 31)))
        self.assertIn('έντεκα (11)', self.text(eid, datetime.date(2025, 12, 12)))

    def test_new_articles_by_law_244_2025(self):
        for eid in ('art_20D', 'art_45A', 'art_8.par_20.sub_3'):
            self.assertIsNone(self.version(eid, datetime.date(2025, 12, 31)), eid)
            self.assertEqual(self.version(eid, datetime.date(2026, 1, 1)).state, 'active', eid)
        self.assertIn('δώδεκα και μισό', self.text('sch_2.par_2', datetime.date(2025, 12, 31)))
        self.assertIn('δεκαπέντε', self.text('sch_2.par_2', datetime.date(2026, 1, 1)))
        self.assertIsNotNone(self.version('art_46', datetime.date(2025, 12, 31)))
        self.assertIsNone(self.version('art_46', datetime.date(2026, 1, 1)))

    def test_retroactive_law_17_2026(self):
        eid = 'art_8.par_21B'
        self.assertIsNone(self.version(eid, datetime.date(2024, 12, 31)))
        self.assertEqual(self.version(eid, datetime.date(2025, 1, 1)).state, 'active')

    def test_correction_5075_of_law_244_2025(self):
        """Διόρθωση Ε.Ε. 5075: с 01.01.2026 «εξυπηρετούμενο δάνειο», известно с 27.02.2026;
        статьи 16–25 закона 244 — 14–23."""
        fragment = Fragment.objects.get(act__uid=INCOME_TAX_LAW, eid='art_14B.par_3.pnt_e')
        day = datetime.date(2026, 3, 1)
        before = fragment.version_on(day, known=datetime.date(2026, 2, 26))
        after = fragment.version_on(day, known=day)
        self.assertEqual(before.key, '2026-01-01')
        self.assertEqual(after.key, '2026-01-01~2')
        body = FragmentText.objects.get(version=after, expression__lang='el').body
        self.assertIn('“εξυπηρετούμενο δάνειο”', body)
        body = FragmentText.objects.get(version=before, expression__lang='el').body
        self.assertIn('“μη εξυπηρετούμενο δάνειο”', body)
        article = Fragment.objects.get(act__uid='cy/law/2025/244_I', eid='art_25')
        self.assertEqual(article.version_on(day, known=day).num, '23')
        self.assertEqual(article.version_on(day, known=datetime.date(2026, 1, 1)).num, '25')

    def test_incentive_proviso_by_law_67_2026(self):
        eid = 'art_20D.par_1.sub_3'
        phrase = 'εντός έξι (6) μηνών από την ημερομηνία έναρξης'
        self.assertIn(phrase, self.text(eid, datetime.date(2026, 4, 13)))
        self.assertNotIn(phrase, self.text(eid, datetime.date(2026, 4, 14)))
        eid = 'art_8.par_11.sub_2'
        self.assertIsNone(self.version(eid, datetime.date(2025, 12, 31)))
        self.assertEqual(self.version(eid, datetime.date(2026, 1, 1)).state, 'active')


DEFENCE_PARSE_ACT = 'cy/law/2002/117_I'


class DefenceContributionParsingTests(SimpleTestCase):
    """Правила разбора, добавленные для 117(Ι)/2002."""

    def test_definition_without_closing_quote(self):
        """«αναδιάρθρωση σημαίνει … — термин без закрывающей кавычки."""
        self.assertEqual(
            consolidated.match_term('«αναδιάρθρωση σημαίνει την διάθεση').group('term'),
            'αναδιάρθρωση',
        )
        self.assertIsNone(consolidated.match_term('«αναδιάρθρωση της οφειλής'))
        root = parse_body(
            '2. Στον παρόντα Νόμο-',
            '«αναδιάρθρωση σημαίνει την διάθεση ακίνητης ιδιοκτησίας·',
            '«έτος» σημαίνει το φορολογικό έτος.',
            '3. Άλλο.',
        )
        article = next(node for node in root.walk() if node.eid == 'art_2')
        consolidated.mark_definitions(article)
        definitions = [node.eid for node in article.children if node.kind == 'def']
        self.assertEqual(definitions, ['art_2.def_anadiarthrwsh', 'art_2.def_etos'])

    @staticmethod
    def parse_indented(*rows, act_uid=DEFENCE_PARSE_ACT):
        """Тело сводного текста: (лишний отступ, строка)."""
        lines = ['Προοίμιο', '', PAD + 'Επειδή.']
        for indent, line in rows:
            lines += ['', PAD + ' ' * indent + line]
        return consolidated.Parser(act_uid).parse(lines)

    def eids(self, root):
        return [node.eid for node in root.walk() if node.eid]

    def test_next_proviso_after_points_of_previous(self):
        """Оговорка N — к пункту оговорки N-1, хоть и глубже пунктов этой оговорки;
        в актах, собранных раньше (4/1978), — по отступу, как прежде."""
        rows = (
            (0, '5.-(1) Πρόσωπο.'),
            (0, 'Νοείται ότι, πρώτη.'),
            (0, 'Νοείται περαιτέρω ότι-'),
            (6, '(α) ένα·'),
            (6, '(β) δύο.'),
            (12, 'Νοείται έτι περαιτέρω ότι, τρίτη.'),
        )
        self.assertIn('art_5.par_1.sub_3', self.eids(self.parse_indented(*rows)))
        earlier = self.parse_indented(*rows, act_uid='cy/law/1978/4')
        self.assertIn('art_5.par_1.sub_2.pnt_b.sub_1', self.eids(earlier))
        self.assertEqual(consolidated.proviso_ordinal('Νοείται ότι, άλλο'), 1)
        self.assertEqual(consolidated.proviso_ordinal('Νοείται περαιτέρω ότι-'), 2)
        self.assertEqual(consolidated.proviso_ordinal('Νοείται έτι έτι περαιτέρω ότι,'), 4)

    def test_proviso_ending_with_comma_opens_list(self):
        """«Νοείται ότι,» + (i), (ii) — пункты самой оговорки."""
        rows = (
            (0, '5.-(1) Πρόσωπο.'),
            (0, 'Νοείται ότι,'),
            (6, '(i) ένα·'),
            (6, '(ii) δύο.'),
        )
        eids = self.eids(self.parse_indented(*rows))
        self.assertIn('art_5.par_1.sub_1.pnt_ii', eids)
        self.assertNotIn('art_5.par_1.pnt_i', eids)

    def test_proviso_after_definition_following_closing_paragraph(self):
        """Абзац после перечня, затем определение: оговорка — к определению."""
        root = self.parse_indented(
            (0, '2. Στον παρόντα Νόμο-'),
            (0, 'Εκτός αν προκύπτει διαφορετικά.'),
            (0, '«έτος» σημαίνει το φορολογικό έτος:'),
            (0, 'Νοείται ότι, άλλο.'),
        )
        article = next(node for node in root.walk() if node.eid == 'art_2')
        consolidated.mark_definitions(article)
        self.assertIn('art_2.def_etos.sub_1', self.eids(root))

    def test_full_corrigendum_in_history(self):
        """«244(I)/2025 ΔΙΟΡΘΩΣΗ Ε.Ε. Παρ. Ι(Ι), Αρ. 5075» на полях — список изменений."""
        self.assertTrue(
            consolidated.is_history('244(I)/2025 ΔΙΟΡΘΩΣΗ Ε.Ε. Παρ. Ι(Ι), Αρ. 5075')
        )

    def test_margin_merged_into_article_line(self):
        """48(Ι)/2025: «Τροποποίηση 2. Το άρθρο 2 … του άρθρου» — поле слилось с текстом."""
        lines = gazette_lines(
            ('Συνοπτικός', '1. Ο παρών Νόμος θα αναφέρεται ως ο περί Δοκιμής Νόμος του 2025.'),
            ('τίτλος.', ''),
        )
        lines += [
            'Τροποποίηση 2. Το άρθρο 2 του βασικού νόμου τροποποιείται με την προσθήκη, '
            'στην κατάλληλη αλφαβητική του άρθρου',
            '2 σειρά, των ακόλουθων νέων όρων: του βασικού νόμου.',
        ]
        lines += gazette_lines(('Τροποποίηση', '3. Το άρθρο 3 τροποποιείται.'))
        root = gazette.GazetteParser('cy/law/2025/1_I').parse(lines)
        articles = [node for node in root.children if node.kind == 'art']
        self.assertEqual([node.num for node in articles], ['1', '2', '3'])
        self.assertEqual(articles[1].heading, 'Τροποποίηση του άρθρου 2 του βασικού νόμου.')
        self.assertEqual(
            articles[1].paragraphs,
            [
                'Το άρθρο 2 του βασικού νόμου τροποποιείται με την προσθήκη, '
                'στην κατάλληλη αλφαβητική σειρά, των ακόλουθων νέων όρων:'
            ],
        )


DEFENCE_LAW = 'cy/law/2002/117_I'


class DefenceContributionLawTests(TestCase):
    """Закон о взносе на оборону 117(Ι)/2002 с изменениями 2024–2025 гг."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.temp.name) / 'texts'
        shutil.copytree(TEXTS_DIR, cls.copy, ignore=shutil.ignore_patterns('.git'))
        output = StringIO()
        call_command('collect_cylaw', DEFENCE_LAW, texts_dir=str(cls.copy), stdout=output)
        cls.report = output.getvalue()
        call_command('load_texts', texts_dir=str(cls.copy), stdout=output)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def version(self, eid, day):
        return Fragment.objects.get(act__uid=DEFENCE_LAW, eid=eid).version_on(day)

    def text(self, eid, day):
        version = self.version(eid, day)
        return FragmentText.objects.get(version=version, expression__lang='el').body

    def test_rebuild_keeps_files(self):
        folder = self.copy / DEFENCE_LAW
        changed = [
            path.name
            for path in folder.iterdir()
            if not filecmp.cmp(path, TEXTS_DIR / DEFENCE_LAW / path.name, shallow=False)
        ]
        self.assertEqual(changed, [])

    def test_all_ops_applied(self):
        """Кроме замен целиком без прежнего текста и примечаний, все изменения сняты."""
        problems = [
            line.strip()
            for line in self.report.splitlines()
            if ('→' in line and ':note →' not in line
                and not line.endswith(('не применено', 'прежний текст не приведён')))
            or 'не найдена' in line
            or 'положения нет' in line
        ]
        self.assertEqual(problems, [])

    def test_restructuring_term_by_laws_165_2024_and_220_2025(self):
        eid = 'art_2.par_1.def_anadiarthrwsh'
        self.assertIn('εννέα (9)', self.text(eid, datetime.date(2024, 12, 30)))
        self.assertIn('δέκα (10)', self.text(eid, datetime.date(2024, 12, 31)))
        self.assertIn('έντεκα (11)', self.text(eid, datetime.date(2025, 12, 12)))

    def test_article_8A_by_laws_165_2024_and_220_2025(self):
        self.assertIsNone(self.version('art_8A', datetime.date(2024, 12, 30)))
        eid = 'art_8A.pnt_a'
        self.assertIn('31η Δεκεμβρίου 2025', self.text(eid, datetime.date(2025, 12, 11)))
        self.assertIn('31η Δεκεμβρίου 2026', self.text(eid, datetime.date(2025, 12, 12)))

    def test_new_articles_by_law_245_2025(self):
        for eid in ('art_3A', 'art_3B', 'art_3G', 'art_3D', 'art_4A', 'art_4B', 'art_4.par_1A'):
            self.assertIsNone(self.version(eid, datetime.date(2025, 12, 31)), eid)
            self.assertEqual(self.version(eid, datetime.date(2026, 1, 1)).state, 'active', eid)

    def test_penalties_by_law_245_2025(self):
        self.assertIn('πεντακόσιες λίρες', self.text('art_6.par_1', datetime.date(2025, 12, 31)))
        self.assertIn('(€5.000)', self.text('art_6.par_1', datetime.date(2026, 1, 1)))
        eid = 'art_4.par_2.sub_1'
        self.assertIn('ή τα ενοίκια', self.text(eid, datetime.date(2025, 12, 31)))
        self.assertNotIn('ή τα ενοίκια', self.text(eid, datetime.date(2026, 1, 1)))
