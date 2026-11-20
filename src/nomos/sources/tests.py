import datetime
import filecmp
import os
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import skipUnless

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from nomos.acts.models import Amendment, Expression, Fragment, FragmentText, Reference
from nomos.translate.models import Note

from .cylaw import history, numbering, refs, texts_xml
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
        eid = found.get(
            from_text__expression__lang='el',
            from_text__version__fragment__eid='art_11.par_5',
            from_text__version__key=history.BASE_KEY,
        ).to_fragment.eid
        self.assertEqual(eid, 'sch_7')

    def test_expressions_not_public_by_default(self):
        self.assertFalse(Expression.objects.filter(public=True).exists())

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

    def test_translation_ids_follow_greek(self):
        marked = refs.align_ids(
            '<ref id="r1" to="art_9">статьи 9</ref> и <ref id="r2" to="art_8">8</ref>',
            ['art_8', 'art_9'],
        )
        self.assertEqual(
            marked, '<ref id="r2" to="art_9">статьи 9</ref> и <ref id="r1" to="art_8">8</ref>',
        )

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
