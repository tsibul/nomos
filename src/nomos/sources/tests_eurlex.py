"""Сборка актов ЕС из Formex: разбор, редакции, ссылки, сборка и загрузка в тестовую БД.

Файлы Formex — маленькие образцы в коде теста: raw/ в git не входит.
"""

import os
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from unittest import skipUnless

from django.test import SimpleTestCase, TestCase

from nomos.acts.models import (
    Act,
    ActRelation,
    Amendment,
    Fragment,
    FragmentText,
    FragmentVersion,
    Reference,
)

from .eurlex import editions, formex, refs

ACT = 'eu/reg/2010/904'
AMENDING_CELEX = '32099R0001'

MARKER = (
    '<?CLG.MDFO ID="O002001M001000" IDREF="C002001M001000" ACTION="REPLACED"'
    ' LEVEL="STRUCTURE" COMMAND="EXPLICIT" ACTIVE.DOC="%s" ACTIVE.LOC="AR:1;PT:2"'
    ' MOD.LEVEL="1"?>' % AMENDING_CELEX
)
MARKER_CLOSE = '<?CLG.MDFC IDREF="C002001M001000"?>'

WORDS = {
    'en': {
        'title': 'Council Regulation (EU) No 904/2010',
        'chapter': 'CHAPTER I',
        'chapter_heading': 'General provisions',
        'article': 'Article',
        'par_1': 'This Regulation lays down rules.',
        'par_2': 'The authorities referred to in paragraph 1 shall cooperate.',
        'intro': 'For the purposes of this Regulation:',
        'pnt_a': 'information under Article 1(2) and Article 143 of Directive 2006/112/EC;',
        'pnt_b_old': 'old text;',
        'pnt_b_new': 'new text under Articles 33 and 34 of Directive 2006/112/EC;',
        'points': ('(a)', '(b)'),
    },
    'el': {
        'title': 'Κανονισμός (ΕΕ) αριθ. 904/2010 του Συμβουλίου',
        'chapter': 'ΚΕΦΑΛΑΙΟ I',
        'chapter_heading': 'Γενικές διατάξεις',
        'article': 'Άρθρο',
        'par_1': 'Ο παρών κανονισμός θεσπίζει κανόνες.',
        'par_2': 'Οι αρχές που αναφέρονται στην παράγραφο 1 συνεργάζονται.',
        'intro': 'Για τους σκοπούς του παρόντος κανονισμού:',
        'pnt_a': (
            'πληροφορίες βάσει του άρθρου 1 παράγραφος 2 και του άρθρου 143'
            ' της οδηγίας 2006/112/ΕΚ·'
        ),
        'pnt_b_old': 'παλαιό κείμενο·',
        'pnt_b_new': 'νέο κείμενο βάσει των άρθρων 33 και 34 της οδηγίας 2006/112/ΕΚ·',
        'points': ('α)', 'β)'),
    },
}


def consolidated(lang, start, changed):
    words = WORDS[lang]
    point_b = words['pnt_b_new'] if changed else words['pnt_b_old']
    if changed:
        point_b = MARKER + point_b + MARKER_CLOSE
    source_end = '' if changed else ' SOURCE.END="32020L0285"'
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<CONS.ACT>'
        '<INFO.CONSLEG START.DATE="%(start)s" END.DATE="99999999"%(source_end)s/>'
        '<CONS.DOC>'
        '<TITLE><TI><P>%(title)s</P></TI></TITLE>'
        '<ENACTING.TERMS>'
        '<DIVISION>'
        '<TITLE><TI><P>%(chapter)s</P></TI><STI><P>%(chapter_heading)s</P></STI></TITLE>'
        '<ARTICLE IDENTIFIER="001">'
        '<TI.ART>%(article)s 1</TI.ART>'
        '<PARAG IDENTIFIER="001.001"><NO.PARAG>1.</NO.PARAG>'
        '<ALINEA>%(par_1)s</ALINEA></PARAG>'
        '<PARAG IDENTIFIER="001.002"><NO.PARAG>2.</NO.PARAG>'
        '<ALINEA>%(par_2)s</ALINEA></PARAG>'
        '</ARTICLE>'
        '<ARTICLE IDENTIFIER="002">'
        '<TI.ART>%(article)s 2</TI.ART>'
        '<ALINEA>%(intro)s'
        '<LIST TYPE="alpha">'
        '<ITEM><NP><NO.P>%(point_a)s</NO.P><TXT>%(pnt_a)s</TXT></NP></ITEM>'
        '<ITEM><NP><NO.P>%(point_b)s</NO.P><TXT>%(pnt_b)s</TXT></NP></ITEM>'
        '</LIST>'
        '</ALINEA>'
        '</ARTICLE>'
        '</DIVISION>'
        '</ENACTING.TERMS>'
        '</CONS.DOC>'
        '</CONS.ACT>'
    ) % dict(
        words,
        start=start,
        source_end=source_end,
        point_a=words['points'][0],
        point_b=words['points'][1],
        pnt_b=point_b,
    )


AMENDING = {
    'en': (
        'Council Regulation (EU) 2099/1',
        'Regulation (EU) No 904/2010 is amended as follows: in Article 2,'
        ' point (b) is replaced.',
        'This Regulation shall enter into force on the twentieth day.',
    ),
    'el': (
        'Κανονισμός (ΕΕ) 2099/1 του Συμβουλίου',
        'Ο κανονισμός (ΕΕ) αριθ. 904/2010 τροποποιείται ως εξής: στο άρθρο 2,'
        ' το στοιχείο β) αντικαθίσταται.',
        'Ο παρών κανονισμός αρχίζει να ισχύει την εικοστή ημέρα.',
    ),
}


def amending(lang):
    title, first, second = AMENDING[lang]
    article = WORDS[lang]['article']
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<ACT>'
        '<TITLE><TI><P>%s</P></TI></TITLE>'
        '<ENACTING.TERMS>'
        '<ARTICLE IDENTIFIER="001"><TI.ART>%s 1</TI.ART><ALINEA>%s</ALINEA></ARTICLE>'
        '<ARTICLE IDENTIFIER="002"><TI.ART>%s 2</TI.ART><ALINEA>%s</ALINEA></ARTICLE>'
        '</ENACTING.TERMS>'
        '</ACT>'
    ) % (title, article, first, article, second)


MANIFEST = '''<?xml version="1.0" encoding="utf-8"?>
<collect uid="eu/reg/2010/904" kind="reg" number="904/2010" year="2010"
  celex="32010R0904" eli="http://data.europa.eu/eli/reg/2010/904/oj"
  date-adopted="2010-10-07" published-on="2010-10-12" in-force-from="2010-11-01">
  <edition start="20240101" en="a/en.xml" el="a/el.xml" />
  <edition start="20250101" en="b/en.xml" el="b/el.xml" />
  <amending uid="eu/reg/2099/1" kind="reg" number="2099/1" year="2099"
    celex="32099R0001" date-adopted="2099-01-10" published-on="2099-01-20"
    in-force-from="2099-02-09" en="m/en.xml" el="m/el.xml">
    <note>Есть несводные изменения.</note>
  </amending>
</collect>
'''


def write_raw(folder):
    files = {
        'a/en.xml': consolidated('en', '20240101', False),
        'a/el.xml': consolidated('el', '20240101', False),
        'b/en.xml': consolidated('en', '20250101', True),
        'b/el.xml': consolidated('el', '20250101', True),
        'm/en.xml': amending('en'),
        'm/el.xml': amending('el'),
    }
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')


def parse_text(content, lang):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'act.xml'
        path.write_text(content, encoding='utf-8')
        return formex.parse(path, lang)


class FormexTests(SimpleTestCase):
    def test_tree(self):
        for lang in ('en', 'el'):
            document = parse_text(consolidated(lang, '20240101', False), lang)
            eids = [node.eid for node in document.nodes()]
            self.assertEqual(eids[:4], ['cpt_I', 'art_1', 'art_1.par_1', 'art_1.par_2'])
            self.assertIn('art_2.pnt_%s' % ('b' if lang == 'en' else 'β'), eids)
            self.assertEqual(document.report, [])

    def test_title_and_heading(self):
        document = parse_text(consolidated('en', '20240101', False), 'en')
        self.assertEqual(document.info['title'], 'Council Regulation (EU) No 904/2010')
        self.assertEqual(document.by_eid()['cpt_I'].heading, 'CHAPTER I General provisions')

    def test_marker(self):
        document = parse_text(consolidated('en', '20250101', True), 'en')
        point = document.by_eid()['art_2.pnt_b']
        self.assertEqual([marker.doc for marker in point.markers], [AMENDING_CELEX])
        self.assertEqual(editions.marker_label(point.markers[0].id), 'M2')


class EditionsTests(SimpleTestCase):
    def history(self):
        found = []
        for start, changed in (('20240101', False), ('20250101', True)):
            found.append(editions.Edition(
                start=start,
                end='99999999',
                source_end='',
                en=parse_text(consolidated('en', start, changed), 'en'),
                el=parse_text(consolidated('el', start, changed), 'el'),
            ))
        amending = {
            AMENDING_CELEX: {
                'uid': 'eu/reg/2099/1',
                'in_force_from': '2099-02-09',
                'published_on': '2099-01-20',
            },
        }
        return editions.History(ACT, found, amending, [])

    def test_versions(self):
        history = self.history()
        fragments = {fragment.eid: fragment for fragment in history.build()}
        point = fragments['art_2.pnt_b']
        self.assertEqual([version.key for version in point.versions], ['base', '2025-01-01'])
        version = point.versions[1]
        self.assertEqual(version.amendment, 'eu/reg/2099/1#art_1.pnt_2:replace')
        self.assertEqual(version.applies_from, '2025-01-01')
        self.assertEqual(version.valid_from, '2099-02-09')
        self.assertIn('νέο κείμενο', version.texts['el'][0][0])
        self.assertEqual(len(fragments['art_2.pnt_a'].versions), 1)
        self.assertEqual(
            history.used[AMENDING_CELEX],
            {('art_1.pnt_2', 'replace'): ('M2', '2025-01-01')},
        )

    def test_loc_to_eid(self):
        self.assertEqual(editions.loc_to_eid('AR:24b;PA:2;PT:a'), 'art_24b.par_2.pnt_a')
        self.assertEqual(editions.loc_to_eid('AX:0;PT:9'), '')


class GreekNumberTests(SimpleTestCase):
    def test_tokens(self):
        self.assertEqual(refs.el_token('369ιζ'), '369q')
        self.assertEqual(refs.el_token('47ιγ'), '47m')
        self.assertEqual(refs.el_token('στ)'), 'f')
        self.assertEqual(refs.el_token('24α'), '24a')
        self.assertEqual(refs.el_token('12'), '12')


class LinkerTests(SimpleTestCase):
    KNOWN = {ACT: {'art_1', 'art_1.par_1', 'art_1.par_2', 'art_17', 'art_17.par_1',
                   'art_17.par_1.pnt_g', 'anx_I'}}

    def mark(self, lang, text, own='art_5', default=None):
        linker = refs.Linker(lang, ACT, self.KNOWN)
        return linker.mark([text], own, default)[0]

    def test_point_of_article(self):
        self.assertEqual(
            self.mark('en', 'see point (g) of Article 17(1).'),
            'see <ref id="r1" to="art_17.par_1.pnt_g">point (g) of Article 17(1)</ref>.',
        )
        self.assertEqual(
            self.mark('el', 'στο στοιχείο ζ) του άρθρου 17 παράγραφος 1.'),
            'στο <ref id="r1" to="art_17.par_1.pnt_g">στοιχείο ζ) του άρθρου 17'
            ' παράγραφος 1</ref>.',
        )

    def test_list_and_directive(self):
        marked = self.mark('en', 'Articles 33 and 34 of Directive 2006/112/EC')
        self.assertIn('<ref id="r1" to="eu/dir/2006/112#art_33">Articles 33</ref>', marked)
        self.assertIn(
            '<ref id="r2" to="eu/dir/2006/112#art_34">34 of Directive 2006/112/EC</ref>',
            marked,
        )
        marked = self.mark('el', 'των άρθρων 369, 369ια και 369κδ της οδηγίας 2006/112/ΕΚ')
        self.assertIn('to="eu/dir/2006/112#art_369k"', marked)
        self.assertIn('to="eu/dir/2006/112#art_369x"', marked)

    def test_missing_point_goes_to_ancestor(self):
        self.assertIn('to="art_1.par_2"', self.mark('en', 'Article 1(2)(c)'))
        self.assertIn('to="art_17"', self.mark('el', 'άρθρο 17 παράγραφος 5'))

    def test_unknown_article_and_other_acts(self):
        self.assertNotIn('<ref', self.mark('en', 'Article 99'))
        self.assertNotIn('<ref', self.mark('en', 'Article 24 of Regulation (EU) 2017/1939'))
        self.assertNotIn('<ref', self.mark('el', 'άρθρο 5 της οδηγίας 2008/9/ΕΚ'))

    def test_paragraph_and_annex(self):
        self.assertIn(
            '<ref id="r1" to="art_1.par_1">paragraph 1</ref>',
            self.mark('en', 'referred to in paragraph 1', own='art_1.par_2'),
        )
        self.assertIn('to="anx_I"', self.mark('el', 'στο παράρτημα I'))

    def test_self_reference(self):
        self.assertNotIn('<ref', self.mark('en', 'this Article 1', own='art_1.par_2'))

    def test_amending_act(self):
        linker = refs.Linker('en', 'eu/reg/2099/1', self.KNOWN)
        marked = linker.mark(['in Article 17(1), paragraph 2 is replaced'], 'art_1', ACT)[0]
        self.assertIn('to="eu/reg/2010/904#art_17.par_1"', marked)
        self.assertNotIn('paragraph 2</ref>', marked)

    def test_footnote_untouched(self):
        marked = self.mark('en', 'text<fn id="fn1">OJ, see Article 1</fn> and Article 17')
        self.assertIn('<fn id="fn1">OJ, see Article 1</fn>', marked)
        self.assertIn('to="art_17"', marked)


class CollectTests(TestCase):
    """collect_eurlex на образцах и load_texts в тестовую БД."""

    @classmethod
    def setUpTestData(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        raw = root / 'raw'
        texts = root / 'texts'
        write_raw(raw)
        (texts / ACT).mkdir(parents=True)
        (texts / ACT / 'collect.xml').write_text(MANIFEST, encoding='utf-8')
        cls.output = StringIO()
        call_command(
            'collect_eurlex', ACT, texts_dir=str(texts), raw_dir=str(raw), stdout=cls.output,
        )
        cls.texts = texts
        call_command('load_texts', texts_dir=str(texts), stdout=StringIO())

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def test_files(self):
        for uid in (ACT, 'eu/reg/2099/1'):
            for name in ('act.xml', 'en.xml', 'el.xml'):
                self.assertTrue((self.texts / uid / name).exists(), '%s/%s' % (uid, name))

    def test_act(self):
        act = Act.objects.get(uid=ACT)
        self.assertEqual(act.celex, '32010R0904')
        self.assertEqual(str(act.date_adopted), '2010-10-07')
        self.assertEqual(
            sorted(act.expressions.values_list('lang', 'status')),
            [('el', 'official_text'), ('en', 'official_text')],
        )
        self.assertEqual(Fragment.objects.filter(act=act).count(), 7)

    def test_versions_and_amendment(self):
        point = Fragment.objects.get(act__uid=ACT, eid='art_2.pnt_b')
        versions = list(point.versions.order_by('key'))
        self.assertEqual([version.key for version in versions], ['2025-01-01', 'base'])
        amendment = versions[0].amendment
        self.assertEqual(amendment.key, 'art_1.pnt_2:replace')
        self.assertEqual(amendment.marker, 'M2')
        self.assertEqual(amendment.amending_act.uid, 'eu/reg/2099/1')
        base = Amendment.objects.get(amending_act__uid='eu/reg/2099/1', key='base')
        self.assertEqual(base.note, 'Есть несводные изменения.')

    def test_texts(self):
        texts = FragmentText.objects.filter(version__fragment__act__uid=ACT)
        self.assertEqual(
            texts.filter(expression__lang='en').count(),
            texts.filter(expression__lang='el').count(),
        )
        self.assertLessEqual(
            texts.count(),
            2 * FragmentVersion.objects.filter(fragment__act__uid=ACT).count(),
        )
        chapter = FragmentText.objects.get(
            version__fragment__act__uid=ACT,
            version__fragment__eid='cpt_I',
            expression__lang='el',
        )
        self.assertEqual(chapter.heading, 'ΚΕΦΑΛΑΙΟ I Γενικές διατάξεις')

    def test_references(self):
        own = Reference.objects.filter(from_text__version__fragment__act__uid=ACT)
        self.assertTrue(own.filter(to_fragment__eid='art_1.par_2', resolved=True).exists())
        self.assertTrue(own.filter(to_fragment__eid='art_1.par_1', resolved=True).exists())
        directive = own.filter(to_act__isnull=True)
        self.assertTrue(directive.exists())
        self.assertFalse(directive.filter(resolved=True).exists())
        amending = Reference.objects.filter(
            from_text__version__fragment__act__uid='eu/reg/2099/1',
            to_fragment__eid='art_2',
            to_fragment__act__uid=ACT,
        )
        self.assertEqual(amending.count(), 2)


TEXTS_DIR = Path(os.environ.get('NOMOS_TEXTS_DIR', '/nonexistent'))
DIRECTIVE = 'eu/dir/2006/112'


@skipUnless((TEXTS_DIR / DIRECTIVE / 'act.xml').exists(), 'нет Nomos-texts с директивой НДС')
class EuVatTextsTests(TestCase):
    """load_texts на собранных актах ЕС из Nomos-texts (NOMOS_TEXTS_DIR)."""

    @classmethod
    def setUpTestData(cls):
        call_command('load_texts', texts_dir=str(TEXTS_DIR), stdout=StringIO())

    def test_directive(self):
        act = Act.objects.get(uid=DIRECTIVE)
        self.assertEqual(act.kind, Act.Kind.DIRECTIVE)
        self.assertGreater(act.fragments.count(), 2000)
        self.assertEqual(
            set(act.expressions.values_list('lang', flat=True)),
            {'en', 'el'},
        )

    def test_act_amending_two_acts(self):
        targets = set(
            ActRelation.objects.filter(from_act__uid='eu/dir/2020/285')
            .values_list('to_act__uid', flat=True)
        )
        self.assertEqual(targets, {DIRECTIVE, ACT})

    def test_editions_of_directive(self):
        keys = set(
            FragmentVersion.objects.filter(fragment__act__uid=DIRECTIVE)
            .values_list('key', flat=True)
        )
        self.assertTrue({'base', '2025-01-01', '2025-03-20', '2025-04-14'} <= keys)

    def test_references_between_eu_acts(self):
        references = Reference.objects.filter(
            from_text__version__fragment__act__uid='eu/dir/2020/285',
            to_act__uid=DIRECTIVE,
        )
        self.assertGreater(references.count(), 0)
        self.assertFalse(references.filter(resolved=False).exists())
