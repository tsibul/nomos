import datetime
import os
from io import StringIO
from pathlib import Path
from unittest import skipUnless

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from nomos.acts.models import Fragment

from .cylaw import history, numbering
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
        output = StringIO()
        call_command('collect_cylaw', LAW, texts_dir=str(TEXTS_DIR), stdout=output)
        call_command('load_texts', texts_dir=str(TEXTS_DIR), stdout=output)

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
