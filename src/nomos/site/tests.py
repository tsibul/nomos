import datetime
import html
import re

from django.test import RequestFactory, TestCase, override_settings
from django.utils import translation

from nomos.acts.models import (
    Act,
    ActRelation,
    Amendment,
    Expression,
    Fragment,
    FragmentText,
    FragmentVersion,
    Reference,
)
from nomos.site import notices, reading, views
from nomos.sources.management.commands import load_texts
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
        base, groups = response.context['base'], response.context['groups']
        self.assertEqual([entry['act'] for entry in base], [self.law])
        self.assertEqual(groups[0]['title'], 'Изменяющие законы')
        self.assertEqual([entry['act'] for entry in groups[0]['entries']], [self.amending])
        self.assertContains(response, 'class="acts__group"')
        self.assertContains(response, 'Что меняет: ст. 1')

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
        self.assertContains(response, 'Πηγή κειμένου')
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
        response = self.client.get(ACT_URL + '@2024-06-01/art_1.par_1/')
        self.assertRedirects(
            response,
            ACT_URL + '@2024-06-01/art_1/#art_1.par_1',
            fetch_redirect_response=False,
        )

    def add_ru(self):
        return Expression.objects.create(
            act=self.law,
            lang='ru',
            status='our_translation',
            source=Source.objects.get(code='test'),
        )

    def test_act_without_translation_offers_no_russian(self):
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertEqual([lang['code'] for lang in response.context['langs']], ['el'])
        self.assertEqual(self.client.get(ACT_URL + 'art_1/ru/').status_code, 404)

    def test_history_keeps_language(self):
        self.add_ru()
        response = self.client.get(ACT_URL + 'art_1.par_1/history/el/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['route'].lang, 'el')
        switch = {lang['code']: lang['url'] for lang in response.context['langs']}
        self.assertEqual(switch['ru'], ACT_URL + 'art_1.par_1/history/')
        response = self.client.get(ACT_URL + 'art_1.par_1/history/ru/')
        self.assertRedirects(
            response,
            ACT_URL + 'art_1.par_1/history/',
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_history_leads_back_to_current_text(self):
        self.add_ru()
        response = self.client.get(ACT_URL + '@2024-06-01/art_1.par_1/history/el/')
        self.assertContains(response, f'href="{ACT_URL}art_1/el/#art_1.par_1"')
        self.assertContains(response, f'href="{ACT_URL}@2024-06-01/art_1/el/#art_1.par_1"')
        urls = [entry['url'] for entry in response.context['versions']]
        self.assertTrue(all(url.endswith('/el/#art_1.par_1') for url in urls))
        self.assertIn(f'{ACT_URL}art_1/el/#art_1.par_1', urls)

    def test_dated_page_shows_way_back(self):
        response = self.client.get(ACT_URL + '@2024-06-01/art_1/')
        self.assertContains(response, 'Βλέπετε την έκδοση της 01.06.2024')
        self.assertContains(response, f'href="{ACT_URL}art_1/"')
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertNotContains(response, 'Βλέπετε την έκδοση')
        self.assertContains(response, f'href="{ACT_URL}art_1.par_1/history/"')

    def test_page_does_not_link_to_itself(self):
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertNotContains(response, f'href="{ACT_URL}art_1/"')
        self.assertNotContains(response, 'href="#art_1"')
        self.assertContains(response, 'href="#art_1.par_1"')
        response = self.client.get(ACT_URL)
        self.assertNotContains(response, f'href="{ACT_URL}"')

    def test_language_kept_outside_act(self):
        self.add_ru()
        response = self.client.get(ACT_URL + 'art_1/el/')
        self.assertContains(response, 'href="/cy/?lang=el"')
        self.assertContains(response, 'href="/about/?lang=el"')
        response = self.client.get('/cy/', {'lang': 'el'})
        self.assertContains(response, f'href="{ACT_URL}el/"')
        response = self.client.get('/cy/')
        self.assertContains(response, f'href="{ACT_URL}"')

    def test_interface_follows_page_language(self):
        self.add_ru()
        response = self.client.get(ACT_URL + 'art_1/el/')
        self.assertContains(response, 'lang="el"')
        self.assertContains(response, 'Κύπρος')
        self.assertContains(response, 'Περιεχόμενα')
        self.assertContains(response, 'Έκδοση κατά ημερομηνία')
        self.assertNotContains(response, 'Оглавление')
        self.assertNotContains(response, 'Редакция на дату')
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'Оглавление')
        self.assertContains(response, 'Кипр')

    def test_interface_language_on_list(self):
        response = self.client.get('/cy/', {'lang': 'el'})
        self.assertContains(response, 'Κύπρος: πράξεις')
        response = self.client.get('/cy/')
        self.assertContains(response, 'Кипр: акты')

    def test_reference_leads_there_and_back(self):
        text = FragmentText.objects.get(version=self.new)
        text.body = '<p>Βλ. <ref id="r1">άρθρο 2</ref>.</p>'
        text.save()
        Reference.objects.create(
            from_text=text,
            anchor='r1',
            to_act=self.amending,
            to_fragment=self.amending.fragments.get(eid='art_2'),
            resolved=True,
        )
        page = ACT_URL + '@2025-06-01/art_1/'
        response = self.client.get(page)
        self.assertContains(response, 'id="ref-art_1.par_1-r1"')
        found = re.search(r'<a class="ref" id="[^"]*" href="([^"]*)"', response.content.decode())
        href = html.unescape(found.group(1))
        self.assertTrue(href.startswith('/cy/law/2024/2_I/@2025-06-01/art_2/?'))
        # на странице цели — путь назад к месту ссылки, той же дате и языку
        response = self.client.get(href)
        self.assertContains(response, '← Πίσω στο άρθρο 1')
        self.assertContains(response, f'href="{page}#ref-art_1.par_1-r1"')
        # чужой адрес возврата не принимается
        response = self.client.get('/cy/law/2024/2_I/art_2/', {'from': '//example.com/'})
        self.assertNotContains(response, 'back-ref')

    def test_reference_to_paragraph_keeps_way_back(self):
        origin = '/cy/law/2024/2_I/art_2/'
        query = {'from': origin, 'at': 'ref-art_2-r1'}
        response = self.client.get(ACT_URL + '@2025-06-01/art_1.par_1/', query)
        self.assertRedirects(
            response,
            ACT_URL + '@2025-06-01/art_1/?from=%2Fcy%2Flaw%2F2024%2F2_I%2Fart_2%2F'
            '&at=ref-art_2-r1#art_1.par_1',
            fetch_redirect_response=False,
        )
        response = self.client.get(ACT_URL + '@2025-06-01/art_1/', query)
        self.assertContains(response, '← Πίσω στο άρθρο 2')
        self.assertContains(response, f'href="{origin}#ref-art_2-r1"')

    def test_back_link_follows_switched_language(self):
        self.add_ru()
        page = ACT_URL + '@2025-06-01/art_1/'
        request = RequestFactory().get('/', {'from': page, 'at': 'ref-art_1.par_1-r1'})
        # с переводом язык акта по умолчанию — русский; язык не меняли — к самой ссылке
        back = views.back_link(request, 'ru')
        self.assertEqual(back['url'], page + '#ref-art_1.par_1-r1')
        # перешли на греческий — назад на греческую страницу, к пункту ссылки
        back = views.back_link(request, 'el')
        self.assertEqual(back['url'], page + 'el/#art_1.par_1')

    def test_language_switch_keeps_original_mode(self):
        self.add_ru()
        response = self.client.get(ACT_URL + '@2024-06-01/art_1/', {'orig': '1'})
        switch = {lang['code']: lang['url'] for lang in response.context['langs']}
        self.assertEqual(switch['el'], ACT_URL + '@2024-06-01/art_1/el/?orig=1')
        self.assertContains(response, 'data-keep-hash')

    def test_missing_translation_falls_back_to_greek(self):
        self.add_ru()
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'Νέο κείμενο')
        self.assertContains(response, 'Перевод готовится')

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
        Note.objects.create(
            target=text,
            kind='source_comment',
            origin='llm',
            body='Сноска источника',
        )
        Note.objects.create(
            target=text,
            kind='editor_comment',
            origin='llm',
            body='Заметка редактора',
        )
        response = self.client.get(ACT_URL + 'art_1/', {'orig': '1'})
        self.assertContains(response, 'Новый текст')
        self.assertContains(response, 'Νέο κείμενο')
        self.assertContains(response, 'Сомнительный термин')
        self.assertContains(response, 'unit--with-original')
        self.assertContains(response, 'Перевод выполнен ИИ, человеком не выверен')
        self.assertNotContains(response, 'Сноска источника')
        self.assertNotContains(response, 'Заметка редактора')
        self.assertContains(response, 'class="fragment__original"\n            lang="el"')

    def test_translated_heading(self):
        ru = Expression.objects.create(
            act=self.law,
            lang='ru',
            status='our_translation',
            source=Source.objects.create(code='nomos', name='Неофициальный перевод'),
        )
        article = self.law.fragments.get(eid='art_1')
        FragmentText.objects.create(
            version=article.versions.get(),
            expression=ru,
            body='',
            heading='Толкование',
            text_hash='ru-heading',
        )
        FragmentText.objects.create(
            version=self.new,
            expression=ru,
            body='<p>Новый текст</p>',
            text_hash='ru-body',
        )
        response = self.client.get(ACT_URL)
        self.assertRegex(response.content.decode(), r'lang="ru"\s*>\s*Статья 1\. Толкование')
        self.assertNotContains(response, 'перевод готовится')
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'Статья 1. Толкование — Закон 1(I)/2000 — Nomos')
        self.assertContains(response, 'Неофициальный перевод. Перевод выполнен ИИ')
        self.assertNotContains(response, 'Текст неизвестен</p>')
        self.assertNotContains(response, 'Перевод готовится')
        self.assertContains(response, 'class="crumbs__item"\n        lang="el"')

    def test_untranslated_article_is_marked(self):
        Expression.objects.create(
            act=self.law,
            lang='ru',
            status='our_translation',
            source=Source.objects.get(code='test'),
        )
        response = self.client.get(ACT_URL)
        self.assertContains(response, 'перевод готовится')
        self.assertContains(response, 'toc__link--pending')
        self.assertRegex(response.content.decode(), r'lang="el"\s*>\s*Άρθρο 1\. Ερμηνεία')
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'Перевод готовится — показан официальный греческий текст.')

    def test_side_column(self):
        response = self.client.get('/cy/')
        self.assertNotContains(response, 'sidenav')
        response = self.client.get(ACT_URL)
        self.assertContains(response, 'sidenav--wide-only')
        self.assertContains(response, 'toc--main')
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'pager__link--toc')
        self.assertContains(response, 'Περιεχόμενα της πράξης')
        self.assertContains(response, 'href="/cy/?lang=el"')
        self.assertContains(response, 'toc__link toc__link--current')
        response = self.client.get(ACT_URL + 'art_1.par_1/history/')
        self.assertContains(response, 'toc__link--current')

    def test_empty_section_has_same_layout(self):
        response = self.client.get('/eu/')
        self.assertContains(response, 'В разделе пока нет актов')
        self.assertNotContains(response, 'Изменяющие акты')

    def test_legal_minimum(self):
        response = self.client.get('/cy/')
        self.assertContains(response, 'name="robots"')
        self.assertContains(response, 'Сайт не даёт юридических и налоговых консультаций')
        self.assertNotContains(response, 'МСФО')
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'lang="el"\n  >\n    Το Nomos είναι')
        self.assertContains(response, 'Η έκδοση ανασυντέθηκε')
        response = self.client.get('/robots.txt')
        self.assertContains(response, 'Disallow: /')
        self.assertEqual(self.client.get('/admin/').status_code, 404)
        response = self.client.get('/about/')
        self.assertContains(response, 'cookies')
        self.assertContains(response, 'CC BY 4.0 — только наши переводы и разметка')
        self.assertContains(response, 'Ilia Tsigelnitskii')

    def test_short_label_of_kdp_is_number_and_date(self):
        kdp = Act.objects.create(
            uid='cy/kdp/2024/95',
            jurisdiction='cy',
            kind='kdp',
            number='95',
            year='2024',
            published_on=datetime.date(2024, 3, 15),
        )
        with translation.override('ru'):
            label = reading.act_short_label(kdp, 'ru')
        self.assertEqual(str(label), 'ΚΔΠ 95/2024 от 15.03.2024')
        with translation.override('el'):
            label = reading.act_short_label(kdp, 'el')
        self.assertEqual(str(label), 'ΚΔΠ 95/2024 της 15.03.2024')

    def test_interface_language_by_django_catalogs_without_cookies(self):
        response = self.client.get('/cy/?lang=el')
        self.assertContains(response, 'Τροποποιητικοί νόμοι')
        self.assertContains(response, 'lang="el"\n  >\n    Το Nomos είναι')
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertNotIn('django_language', response.cookies)
        response = self.client.get('/cy/')
        self.assertContains(response, 'Изменяющие законы')
        with translation.override('en'):
            self.assertTrue(str(notices.DISCLAIMER).startswith('Nomos is a private'))

    def test_short_label_of_amending_law(self):
        Expression.objects.filter(act=self.law).update(short_title='Ο περί Δοκιμής Νόμος')
        self.assertEqual(
            str(reading.act_short_label(self.amending, 'el')),
            'Δοκιμής (Τροποποιητικός) 2(I)/2024',
        )
        Expression.objects.create(
            act=self.law,
            lang='ru',
            status='our_translation',
            source=Source.objects.get(code='test'),
            short_title='Закон об испытании',
        )
        self.assertEqual(
            str(reading.act_short_label(self.amending, 'ru')),
            'Закон об испытании (изм.) 2(I)/2024',
        )

    def test_acts_sorted_by_year_and_number(self):
        acts = [
            Act(number='363', year='2026'),
            Act(number='82', year='2026'),
            Act(number='102', year='2025'),
        ]
        acts.sort(key=reading.act_sort_key)
        self.assertEqual([act.number for act in acts], ['102', '82', '363'])

    def test_home_has_first_screen(self):
        response = self.client.get('/')
        self.assertContains(response, 'class="hero"')
        self.assertContains(response, f'href="{ACT_URL}"')
        self.assertContains(response, 'href="/about/"')
        self.assertNotContains(self.client.get('/cy/'), 'class="hero"')

    def test_russian_is_default_where_translated(self):
        # без русского текста адрес без языка — греческий, /ru/ — явный выбор
        self.assertContains(self.client.get(ACT_URL), 'lang="el"')
        self.assertEqual(self.client.get(ACT_URL + 'el/').status_code, 301)
        Expression.objects.create(
            act=self.law,
            lang='ru',
            status='our_translation',
            source=Source.objects.get(code='test'),
        )
        response = self.client.get(ACT_URL + 'art_1/ru/', {'orig': '1'})
        self.assertRedirects(
            response,
            ACT_URL + 'art_1/?orig=1',
            status_code=301,
            fetch_redirect_response=False,
        )
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertEqual(response.context['route'].lang, 'ru')
        response = self.client.get(ACT_URL + 'el/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['route'].lang, 'el')
        self.assertContains(response, f'href="{ACT_URL}art_1/el/"')

    def test_history_has_human_label(self):
        response = self.client.get(ACT_URL + 'art_1.par_1/history/')
        self.assertRegex(response.content.decode(), r'Εκδόσεις: Άρθρο 1\. Ερμηνεία, \(?1')
        self.assertNotContains(response, 'Εκδόσεις art_1')

    def test_unknown_text_is_marked(self):
        response = self.client.get(ACT_URL + 'art_1/')
        self.assertContains(response, 'Άγνωστο κείμενο')
        # рабочая заметка редактора на сайте не выводится
        self.assertNotContains(response, 'Текст этой редакции неизвестен.')

    def test_history_lists_versions(self):
        response = self.client.get(ACT_URL + 'art_1.par_1/history/')
        self.assertEqual(len(response.context['versions']), 3)
        self.assertContains(response, 'έως 01.01.2024')
        self.assertContains(response, 'από 01.01.2025')

    def test_cylaw_credit_only_on_cylaw_texts(self):
        pages = [ACT_URL, ACT_URL + 'art_1/', ACT_URL + 'art_1.par_1/history/']
        # источник «test» — как акт из Официальной газеты: подписи CyLaw нет
        for page in pages:
            self.assertNotContains(self.client.get(page), 'www.cylaw.org')
        self.el.source = Source.objects.create(code='cylaw', name='CyLaw')
        self.el.save()
        for page in pages:
            response = self.client.get(page)
            self.assertContains(response, 'class="source-credit"')
            self.assertContains(response, 'Πηγή κειμένου: ενοποιημένο κείμενο του CyLaw')

    def test_source_labels_follow_interface_language(self):
        fields = load_texts.SOURCES['cylaw']
        self.el.source = Source.objects.create(code='cylaw', **fields)
        self.el.save()
        response = self.client.get(ACT_URL)
        self.assertContains(response, 'CyLaw — ανεπίσημο ενοποιημένο κείμενο')
        self.assertContains(response, 'Ενοποιημένο κείμενο του CyLaw (www.cylaw.org, ΚΙΝΟΠ)')
        self.assertNotContains(response, fields['name'])
        self.assertNotContains(response, fields['attribution'])

    def test_preamble_label_follows_interface_language(self):
        preamble = Fragment.objects.create(
            uid='lw00000099',
            act=self.law,
            kind='cit',
            eid='cit_1',
            order_key='0000',
        )
        base = self.old.amendment
        self.add_version(preamble, 'base', base, '', self.el, 'Η Βουλή των Αντιπροσώπων')
        self.assertContains(self.client.get(ACT_URL), 'Προοίμιο')
        self.assertNotContains(self.client.get(ACT_URL), 'Преамбула')

    def link_texts_both_ways(self):
        """Ст. 1 закона ссылается на ст. 2 изменяющего, та — обратно: цепочка любой длины."""
        text = FragmentText.objects.get(version=self.new)
        text.body = '<p>Βλ. <ref id="r1">άρθρο 2</ref>.</p>'
        text.save()
        provision = self.amending.fragments.get(eid='art_2')
        Reference.objects.create(
            from_text=text,
            anchor='r1',
            to_act=self.amending,
            to_fragment=provision,
            resolved=True,
        )
        back = FragmentText.objects.get(version__fragment=provision)
        back.body = '<p>Βλ. <ref id="r1">άρθρο 1</ref>.</p>'
        back.save()
        Reference.objects.create(
            from_text=back,
            anchor='r1',
            to_act=self.law,
            to_fragment=self.law.fragments.get(eid='art_1'),
            resolved=True,
        )

    def follow_ref(self, response):
        found = re.search(r'<a class="ref" id="[^"]*" href="([^"]*)"', response.content.decode())
        return html.unescape(found.group(1))

    def test_chain_start_stays_on_screen(self):
        self.link_texts_both_ways()
        start = ACT_URL + '@2025-06-01/art_1/'
        start_link = f'href="{start}#ref-art_1.par_1-r1"'
        response = self.client.get(start)
        self.assertNotContains(response, 'chain-start')
        # три перехода по ссылкам: A → B → C → D; «К началу» на каждом шаге ведёт в A
        steps = []
        for _step in range(3):
            href = self.follow_ref(response)
            response = self.client.get(href)
            self.assertContains(response, 'class="chain-start"')
            self.assertContains(response, 'Στην αρχή: άρθρο 1')
            self.assertContains(response, start_link)
            steps.append((href, response))
        # «← Назад» — на один шаг: из C в B, с началом цепочки
        back = steps[1][1].context['back']['url']
        self.assertTrue(back.startswith(steps[0][0].split('?')[0] + '?start='))
        self.assertContains(self.client.get(back), start_link)
        # из D — в C; C здесь и есть A, начало цепочки не нужно
        self.assertEqual(response.context['back']['url'], start + '#ref-art_1.par_1-r1')
        # из A по «К началу» цепочка кончается
        self.assertNotContains(self.client.get(start), 'chain-start')

    def test_chain_start_survives_language_history_and_dates(self):
        self.add_ru()
        self.link_texts_both_ways()
        start = ACT_URL + '@2025-06-01/art_1/el/'
        chain = {'start': start, 'start_at': 'ref-art_1.par_1-r1'}
        page = '/cy/law/2024/2_I/@2025-06-01/art_2/'
        response = self.client.get(page, {'from': start, 'at': 'ref-art_1.par_1-r1'})
        # смена языка: на русском — к пункту ссылки русской страницы A
        response = self.client.get(ACT_URL + '@2025-06-01/art_1/', chain)
        self.assertEqual(
            response.context['chain_start']['url'],
            ACT_URL + '@2025-06-01/art_1/#art_1.par_1',
        )
        switch = {lang['code']: lang['url'] for lang in response.context['langs']}
        self.assertIn('start=', switch['el'])
        # история и редакции из неё несут начало цепочки
        history = response.context['root'].children[0].history_url
        self.assertIn('start=', history)
        response = self.client.get(history)
        self.assertContains(response, 'class="chain-start"')
        self.assertTrue(all('start=' in entry['url'] for entry in response.context['versions']))
        # форма даты переносит начало цепочки на новую страницу
        response = self.client.get(ACT_URL + 'art_1/', {'on': '2024-06-01', **chain})
        self.assertIn('start=', response['Location'])
