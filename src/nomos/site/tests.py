import datetime

from django.test import TestCase, override_settings

from nomos.acts.models import (
    Act,
    ActRelation,
    Amendment,
    Expression,
    Fragment,
    FragmentText,
    FragmentVersion,
)
from nomos.sources.models import Source
from nomos.translate.models import Note

ACT_URL = '/cy/law/2000/1_I/'


@override_settings(DJANGO_VITE={'default': {'dev_mode': True}})
class ViewerTests(TestCase):
    """Маленький закон: ст. 1 с п. 1, заменённым с 01.01.2025, и будущей редакцией."""

    @classmethod
    def setUpTestData(cls):
        today = datetime.date.today()
        cls.future_day = today + datetime.timedelta(days=100)
        source = Source.objects.create(code='test', name='Тест')
        cls.law = Act.objects.create(
            uid='cy/law/2000/1_I',
            jurisdiction='cy',
            kind='law',
            number='1(I)',
            year='2000',
            structure_lang='el',
        )
        cls.amending = Act.objects.create(
            uid='cy/law/2024/2_I',
            jurisdiction='cy',
            kind='law',
            number='2(I)',
            year='2024',
        )
        ActRelation.objects.create(from_act=cls.amending, to_act=cls.law, kind='amends')
        cls.el = Expression.objects.create(
            act=cls.law,
            lang='el',
            status='official_text',
            source=source,
            title='Ο περί Δοκιμής Νόμος',
        )
        amending_el = Expression.objects.create(
            act=cls.amending,
            lang='el',
            status='official_text',
            source=source,
            title='Τροποποιητικός',
        )
        base = Amendment.objects.create(
            amending_act=cls.law,
            kind='base',
            key='base',
            marker='base',
        )
        amending_base = Amendment.objects.create(
            amending_act=cls.amending,
            kind='base',
            key='base',
            marker='base',
        )
        provision = Fragment.objects.create(
            uid='am00000001',
            act=cls.amending,
            kind='art',
            eid='art_2',
        )
        cls.add_version(provision, 'base', amending_base, '(2)', amending_el, 'Αντικαθίσταται')
        replace = Amendment.objects.create(
            amending_act=cls.amending,
            amending_fragment=provision,
            kind='replace',
            key='art_2:replace',
            marker='art_2:replace',
            published_on=datetime.date(2024, 12, 1),
            in_force_from=datetime.date(2025, 1, 1),
        )
        future = Amendment.objects.create(
            amending_act=cls.amending,
            amending_fragment=provision,
            kind='replace',
            key='art_2:replace@future',
            marker='art_2:replace@future',
            published_on=datetime.date(2024, 12, 1),
            in_force_from=cls.future_day,
        )
        part = Fragment.objects.create(uid='lw00000001', act=cls.law, kind='prt', eid='prt_I')
        cls.add_version(part, 'base', base, 'I', None, '', heading='ΜΕΡΟΣ I')
        article = Fragment.objects.create(
            uid='lw00000002',
            act=cls.law,
            kind='art',
            eid='art_1',
            parent=part,
            order_key='0001',
        )
        cls.add_version(article, 'base', base, '1', cls.el, '', heading='Ερμηνεία')
        cls.paragraph = Fragment.objects.create(
            uid='lw00000003',
            act=cls.law,
            kind='par',
            eid='art_1.par_1',
            parent=article,
            order_key='0001',
        )
        cls.old = cls.add_version(cls.paragraph, 'base', base, '(1)', cls.el, 'Παλαιό κείμενο')
        cls.new = cls.add_version(
            cls.paragraph,
            '2025-01-01',
            replace,
            '(1)',
            cls.el,
            'Νέο κείμενο',
            valid_from=datetime.date(2025, 1, 1),
            known_from=datetime.date(2024, 12, 1),
        )
        cls.add_version(
            cls.paragraph,
            cls.future_day.isoformat(),
            future,
            '(1)',
            cls.el,
            'Μελλοντικό κείμενο',
            valid_from=cls.future_day,
            known_from=datetime.date(2024, 12, 1),
        )
        unknown = Fragment.objects.create(
            uid='lw00000004',
            act=cls.law,
            kind='par',
            eid='art_1.par_2',
            parent=article,
            order_key='0002',
        )
        unknown_version = cls.add_version(unknown, 'base', base, '(2)', None, '')
        unknown_text = FragmentText.objects.create(
            version=unknown_version,
            expression=cls.el,
            body='',
            text_hash='0',
        )
        Note.objects.create(
            target=unknown_text,
            kind='editor_comment',
            origin='human',
            body='Текст этой редакции неизвестен.',
        )

    @classmethod
    def add_version(cls, fragment, key, amendment, num, expression, body, **fields):
        fields.setdefault('known_from', datetime.date(2024, 1, 1))
        version = FragmentVersion.objects.create(
            fragment=fragment,
            key=key,
            amendment=amendment,
            num=num,
            heading=fields.pop('heading', ''),
            **fields,
        )
        if expression is not None and body:
            FragmentText.objects.create(
                version=version,
                expression=expression,
                body=f'<p>{body}</p>',
                text_hash=key,
            )
        return version

    def test_act_list_separates_amending_acts(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        base, amending = response.context['base'], response.context['amending']
        self.assertEqual([entry['act'] for entry in base], [self.law])
        self.assertEqual([entry['act'] for entry in amending], [self.amending])

    def test_menu_sections_follow_data(self):
        response = self.client.get('/')
        self.assertContains(response, 'href="/cy/"')
        self.assertNotContains(response, 'href="/eu/"')
        self.assertContains(response, 'скоро')
        self.assertContains(response, 'href="/about/"')

    def test_section_lists_its_acts(self):
        response = self.client.get('/cy/')
        self.assertEqual(len(response.context['base']), 1)
        self.assertEqual(self.client.get('/xx/').status_code, 404)

    def test_about_lists_sources(self):
        response = self.client.get('/about/')
        self.assertContains(response, 'Тест')
        self.assertContains(response, 'CC BY 4.0')

    def test_act_page_has_toc(self):
        response = self.client.get(ACT_URL)
        self.assertContains(response, 'ΜΕΡΟΣ I')
        self.assertContains(response, f'href="{ACT_URL}art_1/"')
        self.assertContains(response, 'translate="no"')
        self.assertContains(response, 'Источник текста')
        self.assertContains(response, 'Тест')

    def test_version_by_date(self):
        response = self.client.get(ACT_URL + '@2024-06-01/art_1/')
        self.assertContains(response, 'Παλαιό κείμενο')
        self.assertNotContains(response, 'Νέο κείμενο')
        response = self.client.get(ACT_URL + '@2025-02-01/art_1/')
        self.assertContains(response, 'Νέο κείμενο')
        self.assertNotContains(response, 'Παλαιό κείμενο')

    def test_amended_version_links_to_provision(self):
        response = self.client.get(ACT_URL + '@2025-02-01/art_1/')
        self.assertContains(response, 'href="/cy/law/2024/2_I/art_2/"')

    def test_today_shows_future_version(self):
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'Νέο κείμενο')
        future_url = f'{ACT_URL}@{self.future_day.isoformat()}/art_1.par_1/'
        self.assertContains(response, f'href="{future_url}"')
        response = self.client.get(future_url, follow=True)
        self.assertContains(response, 'Μελλοντικό κείμενο')

    def test_date_before_2024_is_rejected(self):
        response = self.client.get(ACT_URL + '@2023-12-31/art_1/')
        self.assertEqual(response.status_code, 404)

    def test_date_form_redirects_to_address(self):
        response = self.client.get(ACT_URL + 'art_1/', {'on': '2024-06-01'})
        self.assertRedirects(response, ACT_URL + '@2024-06-01/art_1/')
        response = self.client.get(ACT_URL + 'art_1/', {'on': '2020-01-01'})
        self.assertRedirects(response, ACT_URL + '@2024-01-01/art_1/')

    def test_fragment_anchor(self):
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'id="art_1.par_1"')
        self.assertContains(response, 'href="#art_1.par_1"')
        response = self.client.get(ACT_URL + '@2024-06-01/art_1.par_1/ru/')
        self.assertRedirects(
            response,
            ACT_URL + '@2024-06-01/art_1/ru/#art_1.par_1',
            fetch_redirect_response=False,
        )

    def test_missing_translation_falls_back_to_greek(self):
        response = self.client.get(ACT_URL + 'art_1/ru/')
        self.assertContains(response, 'Νέο κείμενο')
        self.assertContains(response, 'Перевода нет')

    def test_translation_with_original_and_note(self):
        ru = Expression.objects.create(
            act=self.law,
            lang='ru',
            status='our_translation',
            source=Source.objects.get(code='test'),
        )
        text = FragmentText.objects.create(
            version=self.new,
            expression=ru,
            body='<p>Новый текст</p>',
            text_hash='ru',
        )
        Note.objects.create(target=text, kind='doubt', origin='llm', body='Сомнительный термин')
        response = self.client.get(ACT_URL + 'art_1/ru/', {'orig': '1'})
        self.assertContains(response, 'Новый текст')
        self.assertContains(response, 'Νέο κείμενο')
        self.assertContains(response, 'Сомнительный термин')
        self.assertContains(response, 'unit--with-original')
        self.assertContains(response, 'Рабочий перевод, не выверен')
        self.assertContains(response, 'class="fragment__original"\n            lang="el"')

    def test_unknown_text_is_marked(self):
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'Текст неизвестен')
        self.assertContains(response, 'Текст этой редакции неизвестен.')

    def test_history_lists_versions(self):
        response = self.client.get(ACT_URL + 'art_1.par_1/history/')
        self.assertEqual(len(response.context['versions']), 3)
        self.assertContains(response, 'до 01.01.2024')
        self.assertContains(response, 'с 01.01.2025')
