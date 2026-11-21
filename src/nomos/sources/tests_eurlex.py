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
from .management.commands.collect_eurlex import read_manifest

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
        'pnt_a': 'information under Article 1(2) of this Regulation and Article 143 of Directive'
                 ' 2006/112/EC;',
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

    def test_subparagraphs_only_in_en(self):
        """en: два ALINEA (sub_1, sub_2), el: один ALINEA с двумя P (ст. 61(1) 282/2011);
        ст. 61a(2): в el нет второго подабзаца, пункты — у первого."""
        def document(lang, first, second):
            return (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<CONS.ACT><INFO.CONSLEG START.DATE="20240101" END.DATE="99999999"/>'
                '<CONS.DOC><TITLE><TI><P>T</P></TI></TITLE><ENACTING.TERMS>'
                '<ARTICLE IDENTIFIER="061"><TI.ART>%s 61</TI.ART>'
                '<PARAG IDENTIFIER="061.001"><NO.PARAG>1.</NO.PARAG>%s</PARAG>'
                '<PARAG IDENTIFIER="061.002"><NO.PARAG>2.</NO.PARAG>%s</PARAG>'
                '</ARTICLE></ENACTING.TERMS></CONS.DOC></CONS.ACT>'
            ) % ('Article' if lang == 'en' else 'Άρθρο', first, second)

        def points(a, b):
            return (
                '<P><LIST TYPE="alpha">'
                '<ITEM><NP><NO.P>%s</NO.P><TXT>x</TXT></NP></ITEM>'
                '<ITEM><NP><NO.P>%s</NO.P><TXT>y</TXT></NP></ITEM>'
                '</LIST></P>'
            ) % (a, b)

        en = document(
            'en',
            '<ALINEA>One.</ALINEA><ALINEA>Two.</ALINEA>',
            '<ALINEA><P>Lead.</P>%s<P>After.</P></ALINEA>' % points('(a)', '(b)'),
        )
        el = document(
            'el',
            '<ALINEA><P>Ένα.</P><P>Δύο.</P></ALINEA>',
            '<ALINEA><P>Αρχή.</P>%s</ALINEA>' % points('α)', 'β)'),
        )
        edition = editions.Edition(
            start='20240101',
            end='99999999',
            source_end='',
            en=parse_text(en, 'en'),
            el=parse_text(el, 'el'),
        )
        report = []
        history = editions.History(ACT, [edition], {}, report)
        texts = {
            fragment.eid: fragment.versions[0].texts
            for fragment in history.build()
        }
        self.assertEqual(texts['art_61.par_1.sub_1']['el'][0], ['Ένα.'])
        self.assertEqual(texts['art_61.par_1.sub_2']['el'][0], ['Δύο.'])
        self.assertEqual(texts['art_61.par_1']['el'][0], [])
        self.assertEqual(texts['art_61.par_2.sub_1']['el'][0], ['Αρχή.'])
        self.assertEqual(texts['art_61.par_2.sub_1.pnt_b']['el'][0], ['y'])
        self.assertNotIn('el', texts['art_61.par_2.sub_2'])
        self.assertEqual(report, ['20240101: нет el для art_61.par_2.sub_2'])

    def test_loc_to_eid(self):
        self.assertEqual(editions.loc_to_eid('AR:24b;PA:2;PT:a'), 'art_24b.par_2.pnt_a')
        self.assertEqual(editions.loc_to_eid('AX:0;PT:9'), '')


class ManifestTests(SimpleTestCase):
    def test_root_note_is_base_note(self):
        text = (
            '<collect uid="eu/dir/2022/2523">'
            '<note> Исправления  не внесены. </note>'
            '<edition start="20221222" en="a" el="b" />'
            '<amending uid="eu/dir/2025/1" celex="x" en="c" el="d"><note>Своя.</note></amending>'
            '</collect>'
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'collect.xml'
            path.write_text(text, encoding='utf-8')
            manifest = read_manifest(path)
        self.assertEqual(manifest['note'], 'Исправления не внесены.')
        self.assertEqual(manifest['amending'][0]['note'], 'Своя.')


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

    def test_act_named_after_qualifiers_or_list(self):
        """«of Directive …» после уточнений или в конце перечня относится ко всем статьям."""
        marked = self.mark(
            'en',
            'in Article 54(1), second subparagraph, Article 56(2), first subparagraph,'
            ' and Articles 58 and 59 of Directive 2006/112/EC',
        )
        self.assertIn('to="eu/dir/2006/112#art_54.par_1">Article 54(1)</ref>', marked)
        self.assertIn('to="eu/dir/2006/112#art_56.par_2">Article 56(2)</ref>', marked)
        marked = self.mark('en', 'by Article 53 and Article 54(1) of Directive 2006/112/EC')
        self.assertIn('to="eu/dir/2006/112#art_53">Article 53</ref>', marked)
        marked = self.mark(
            'en',
            'Article 56 and point (g) of the first paragraph of Article 59'
            ' of Directive 2006/112/EC',
        )
        self.assertIn('to="eu/dir/2006/112#art_56">Article 56</ref>', marked)
        marked = self.mark(
            'el',
            'στο άρθρο 54 παράγραφος 1 δεύτερο εδάφιο, στο άρθρο 56 παράγραφος 2'
            ' πρώτο εδάφιο και στα άρθρα 58 και 59 της οδηγίας 2006/112/ΕΚ',
        )
        self.assertIn('to="eu/dir/2006/112#art_54.par_1"', marked)
        self.assertIn('to="eu/dir/2006/112#art_56.par_2"', marked)
        marked = self.mark('el', 'του άρθρου 17, παράγραφος 1 του παρόντος κανονισμού')
        self.assertIn('to="art_17.par_1">άρθρου 17, παράγραφος 1', marked)

    def test_own_articles_before_foreign_list_stay_own(self):
        marked = self.mark('en', 'Article 17 of this Regulation and Article 1')
        self.assertIn('to="art_17"', marked)
        self.assertIn('to="art_1">Article 1</ref>', marked)
        marked = self.mark(
            'en',
            'under Article 17, the rules of Article 59 of Directive 2006/112/EC',
        )
        self.assertIn('to="art_17">Article 17</ref>', marked)

    def test_second_number_is_paragraph_of_same_article(self):
        """«άρθρο 17 παράγραφοι 1 και 2», «Article 17(1) and (2)» — пункт 2 статьи 17."""
        marked = self.mark('el', 'στο άρθρο 1 παράγραφοι 1 και 2 δεν')
        self.assertIn('to="art_1.par_2">2</ref>', marked)
        marked = self.mark('en', 'in Article 1(1) and (2) shall')
        self.assertIn('to="art_1.par_2">(2)</ref>', marked)
        marked = self.mark('en', 'Article 17(1)(a) and (g)')
        self.assertIn('to="art_17.par_1.pnt_g">(g)</ref>', marked)
        # единственное число — перечень статей
        marked = self.mark('el', 'άρθρο 17 παράγραφος 1 και 1')
        self.assertIn('to="art_1">1</ref>', marked)
        self.assertNotIn('(b)</ref>', self.mark('en', 'Article 17 and (b)'))

    def test_article_of_unknown_act_after_list(self):
        """Статья перед «of Directive 92/12/EEC» не ссылается на свой акт."""
        marked = self.mark('en', 'Article 17(1) and Article 1 of Directive 92/12/EEC')
        self.assertNotIn('<ref', marked)
        marked = self.mark('el', 'το άρθρο 17 παράγραφος 1 και το άρθρο 1 της οδηγίας 92/12/ΕΟΚ')
        self.assertNotIn('<ref', marked)


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
IMPLEMENTING = 'eu/reg/2011/282'


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
        self.assertTrue(
            {'en', 'el'} <= set(act.expressions.values_list('lang', flat=True)),
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

    def test_all_references_between_eu_acts_resolved(self):
        # все языки, включая ru: цель — существующий фрагмент акта ЕС
        unresolved = Reference.objects.filter(
            from_text__version__fragment__act__uid__startswith='eu/',
            to_act__uid__startswith='eu/',
            resolved=False,
        )
        self.assertEqual(list(unresolved.values_list('from_text__expression__lang', 'anchor')), [])

    def test_implementing_regulation(self):
        act = Act.objects.get(uid=IMPLEMENTING)
        self.assertEqual(act.kind, Act.Kind.IMPLEMENTING_REGULATION)
        self.assertGreater(act.fragments.count(), 500)
        keys = set(
            FragmentVersion.objects.filter(fragment__act__uid=IMPLEMENTING)
            .values_list('key', flat=True)
        )
        self.assertEqual(keys, {'base', '2025-04-14'})
        targets = set(
            ActRelation.objects.filter(from_act__uid='eu/reg/2025/518')
            .values_list('to_act__uid', flat=True)
        )
        self.assertEqual(targets, {IMPLEMENTING})

    def test_section_out_of_place_in_greek(self):
        # «ΤΜΗΜΑ 3» главы XI в el вынесен на верх акта; el взят по виду и номеру
        version = FragmentVersion.objects.get(
            fragment__act__uid=IMPLEMENTING,
            fragment__eid='cpt_XI.sct_3',
            key='base',
        )
        self.assertTrue(version.texts.filter(expression__lang='el').exists())
