import datetime
import html
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import override_script_prefix
from django.utils import translation

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
from nomos.site import access, notices, reading, views
from nomos.sources.management.commands import load_texts
from nomos.sources.models import Source
from nomos.translate.models import Note

ACT_URL = "/cy/law/2000/1_I/"


def make_nav():
    """Каркас меню как в nav.xml: Кипр → Налоги, ЕС → НДС, МСФО и Аналитика без
    пунктов.

    Пункты — основные акты тестов: какие из них есть в базе, те и связаны.
    """
    order = iter(range(1, 100))

    def node(parent, key="", label="", uid=""):
        return NavNode.objects.create(
            parent=parent,
            key=key,
            label=label,
            order=next(order),
            act_uid=uid,
            act=Act.objects.filter(uid=uid).first() if uid else None,
        )

    cy = node(None, "cy", "Кипр")
    taxes = node(cy, "taxes", "Налоги")
    node(taxes, label="НДС", uid="cy/law/2000/1_I")
    eu = node(None, "eu", "ЕС")
    vat = node(eu, "vat", "НДС")
    node(vat, uid="eu/dir/2006/112")
    node(None, "ifrs", "МСФО")
    node(None, "analytics", "Аналитика")


@override_settings(DJANGO_VITE={"default": {"dev_mode": True}})
class ViewerTests(TestCase):
    """Маленький закон: ст. 1 с п. 1, заменённым с 01.01.2025, и будущей
    редакцией."""

    @classmethod
    def setUpTestData(cls):
        today = datetime.date.today()
        cls.future_day = today + datetime.timedelta(days=100)
        cls.reader = User.objects.create_user("reader")
        source = Source.objects.create(code="test", name="Тест")
        cls.law = Act.objects.create(
            uid="cy/law/2000/1_I",
            jurisdiction="cy",
            kind="law",
            number="1(I)",
            year="2000",
            structure_lang="el",
        )
        cls.amending = Act.objects.create(
            uid="cy/law/2024/2_I",
            jurisdiction="cy",
            kind="law",
            number="2(I)",
            year="2024",
        )
        ActRelation.objects.create(
            from_act=cls.amending, to_act=cls.law, kind="amends"
        )
        cls.el = Expression.objects.create(
            act=cls.law,
            lang="el",
            status="official_text",
            source=source,
            title="Ο περί Δοκιμής Νόμος",
        )
        amending_el = Expression.objects.create(
            act=cls.amending,
            lang="el",
            status="official_text",
            source=source,
            title="Τροποποιητικός",
        )
        base = Amendment.objects.create(
            amending_act=cls.law,
            kind="base",
            key="base",
            marker="base",
        )
        amending_base = Amendment.objects.create(
            amending_act=cls.amending,
            kind="base",
            key="base",
            marker="base",
        )
        provision = Fragment.objects.create(
            uid="am00000001",
            act=cls.amending,
            kind="art",
            eid="art_2",
        )
        cls.add_version(
            provision,
            "base",
            amending_base,
            "(2)",
            amending_el,
            "Αντικαθίσταται",
        )
        replace = Amendment.objects.create(
            amending_act=cls.amending,
            amending_fragment=provision,
            kind="replace",
            key="art_2:replace",
            marker="art_2:replace",
            published_on=datetime.date(2024, 12, 1),
            in_force_from=datetime.date(2025, 1, 1),
        )
        future = Amendment.objects.create(
            amending_act=cls.amending,
            amending_fragment=provision,
            kind="replace",
            key="art_2:replace@future",
            marker="art_2:replace@future",
            published_on=datetime.date(2024, 12, 1),
            in_force_from=cls.future_day,
        )
        part = Fragment.objects.create(
            uid="lw00000001", act=cls.law, kind="prt", eid="prt_I"
        )
        cls.add_version(part, "base", base, "I", None, "", heading="ΜΕΡΟΣ I")
        article = Fragment.objects.create(
            uid="lw00000002",
            act=cls.law,
            kind="art",
            eid="art_1",
            parent=part,
            order_key="0001",
        )
        cls.add_version(
            article, "base", base, "1", cls.el, "", heading="Ερμηνεία"
        )
        cls.paragraph = Fragment.objects.create(
            uid="lw00000003",
            act=cls.law,
            kind="par",
            eid="art_1.par_1",
            parent=article,
            order_key="0001",
        )
        cls.old = cls.add_version(
            cls.paragraph, "base", base, "(1)", cls.el, "Παλαιό κείμενο"
        )
        cls.new = cls.add_version(
            cls.paragraph,
            "2025-01-01",
            replace,
            "(1)",
            cls.el,
            "Νέο κείμενο",
            valid_from=datetime.date(2025, 1, 1),
            known_from=datetime.date(2024, 12, 1),
        )
        cls.add_version(
            cls.paragraph,
            cls.future_day.isoformat(),
            future,
            "(1)",
            cls.el,
            "Μελλοντικό κείμενο",
            valid_from=cls.future_day,
            known_from=datetime.date(2024, 12, 1),
        )
        unknown = Fragment.objects.create(
            uid="lw00000004",
            act=cls.law,
            kind="par",
            eid="art_1.par_2",
            parent=article,
            order_key="0002",
        )
        unknown_version = cls.add_version(
            unknown, "base", base, "(2)", None, ""
        )
        unknown_text = FragmentText.objects.create(
            version=unknown_version,
            expression=cls.el,
            body="",
            text_hash="0",
        )
        Note.objects.create(
            target=unknown_text,
            kind="editor_comment",
            origin="human",
            body="Текст этой редакции неизвестен.",
        )
        make_nav()

    @classmethod
    def add_version(
        cls, fragment, key, amendment, num, expression, body, **fields
    ):
        fields.setdefault("known_from", datetime.date(2024, 1, 1))
        version = FragmentVersion.objects.create(
            fragment=fragment,
            key=key,
            amendment=amendment,
            num=num,
            heading=fields.pop("heading", ""),
            **fields,
        )
        if expression is not None and body:
            FragmentText.objects.create(
                version=version,
                expression=expression,
                body=f"<p>{body}</p>",
                text_hash=key,
            )
        return version

    def test_act_list_separates_amending_acts(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        items = response.context["groups"][0]["items"]
        self.assertEqual([item["entry"]["act"] for item in items], [self.law])
        blocks = items[0]["blocks"]
        self.assertEqual(blocks[0]["title"], "Изменяющие законы")
        self.assertEqual(
            [entry["act"] for entry in blocks[0]["entries"]], [self.amending]
        )
        self.assertContains(response, 'class="acts__group"')
        self.assertContains(response, "Что меняет: ст. 1")

    def test_menu_sections_follow_data(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="/cy/"')
        self.assertNotContains(response, 'href="/eu/"')
        self.assertContains(response, "скоро")
        self.assertContains(response, 'href="/about/"')

    def test_section_lists_its_acts(self):
        response = self.client.get("/cy/")
        self.assertEqual(len(response.context["groups"][0]["items"]), 1)
        self.assertEqual(self.client.get("/xx/").status_code, 404)
        self.assertEqual(self.client.get("/cy/xx/").status_code, 404)

    def test_subsection_page_shows_missing_item(self):
        taxes = NavNode.objects.get(key="taxes")
        NavNode.objects.create(
            parent=taxes,
            label="Подоходный налог",
            order=50,
            act_uid="cy/law/2002/118_I",
        )
        response = self.client.get("/cy/taxes/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "acts__card--missing")
        self.assertContains(response, "Подоходный налог")
        self.assertContains(response, "не загружен")

    def test_act_page_blocks_by_relations(self):
        kdp = Act.objects.create(
            uid="cy/kdp/2010/290", jurisdiction="cy", kind="kdp"
        )
        directive = Act.objects.create(
            uid="eu/dir/2006/112", jurisdiction="eu", kind="dir"
        )
        ActRelation.objects.create(
            from_act=kdp, to_act=self.law, kind="based_on"
        )
        ActRelation.objects.create(
            from_act=self.law, to_act=directive, kind="related"
        )
        response = self.client.get(ACT_URL)
        self.assertEqual(len(response.context["blocks"]), 3)
        titles = (
            "Τροποποιητικοί νόμοι",
            "Κανονισμοί και διατάγματα",
            "Σχετικές πράξεις της ΕΕ",
        )
        for title in titles:
            self.assertContains(response, title)
        # раздел в боковом меню — родитель пункта акта
        self.assertContains(response, 'href="/cy/taxes/?')

    def test_about_lists_sources(self):
        response = self.client.get("/about/")
        self.assertContains(response, "Тест")
        self.assertContains(response, "CC BY 4.0")

    def test_act_page_has_toc(self):
        response = self.client.get(ACT_URL)
        self.assertContains(response, "ΜΕΡΟΣ I")
        self.assertContains(response, f'href="{ACT_URL}art_1/"')
        self.assertContains(response, 'translate="no"')
        self.assertContains(response, "Πηγή κειμένου")
        self.assertContains(response, "Тест")

    def test_version_by_date(self):
        response = self.client.get(ACT_URL + "@2024-06-01/art_1/")
        self.assertContains(response, "Παλαιό κείμενο")
        self.assertNotContains(response, "Νέο κείμενο")
        response = self.client.get(ACT_URL + "@2025-02-01/art_1/")
        self.assertContains(response, "Νέο κείμενο")
        self.assertNotContains(response, "Παλαιό κείμενο")

    def add_repealed_article(self):
        article = Fragment.objects.get(eid="art_1")
        repealed = Fragment.objects.create(
            uid="lw00000005",
            act=self.law,
            kind="art",
            eid="art_2",
            parent=article.parent,
            order_key="0002",
        )
        base = Amendment.objects.get(amending_act=self.law, kind="base")
        self.add_version(
            repealed,
            "base",
            base,
            "2",
            self.el,
            "Καταργούμενο",
            heading="Τέλη",
        )
        delete = Amendment.objects.create(
            amending_act=self.amending,
            amending_fragment=Fragment.objects.get(uid="am00000001"),
            kind="delete",
            key="art_2:delete",
            marker="art_2:delete",
            published_on=datetime.date(2025, 2, 1),
            in_force_from=datetime.date(2025, 3, 1),
        )
        self.add_version(
            repealed,
            "2025-03-01",
            delete,
            "2",
            None,
            "",
            state="repealed",
            valid_from=datetime.date(2025, 3, 1),
            known_from=datetime.date(2025, 2, 1),
        )
        later = Fragment.objects.create(
            uid="lw00000006",
            act=self.law,
            kind="art",
            eid="art_3",
            parent=article.parent,
            order_key="0003",
        )
        self.add_version(
            later,
            "2025-03-01",
            delete,
            "3",
            self.el,
            "Νέο άρθρο",
            valid_from=datetime.date(2025, 3, 1),
            known_from=datetime.date(2025, 2, 1),
        )

    def test_repealed_article_page(self):
        self.add_repealed_article()
        response = self.client.get(ACT_URL + "@2025-06-01/art_2/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "site/repealed.html")
        self.assertContains(response, "Άρθρο 2. Τέλη")
        self.assertContains(
            response, "Η διάταξη έπαψε να ισχύει από 01.03.2025."
        )
        self.assertContains(response, 'href="/cy/law/2024/2_I/art_2/"')
        self.assertContains(response, f'href="{ACT_URL}@2025-02-28/art_2/"')
        self.assertContains(response, "(στις 28.02.2025)")
        self.assertContains(response, f'href="{ACT_URL}art_2/history/"')
        self.assertNotContains(response, "Καταργούμενο")

    def test_repealed_article_page_in_english(self):
        self.add_repealed_article()
        english = Expression.objects.create(
            act=self.law,
            lang="en",
            status="our_translation",
            source=self.el.source,
            title="Test Law",
        )
        base_version = Fragment.objects.get(uid="lw00000005").versions.get(
            key="base"
        )
        FragmentText.objects.create(
            version=base_version,
            expression=english,
            body="<p>Repealed text</p>",
            text_hash="en",
        )
        response = self.client.get(ACT_URL + "@2025-06-01/art_2/en/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "This provision ceased to have effect on 01.03.2025."
        )
        self.assertContains(
            response, "Last version in force (as at 28.02.2025)"
        )
        self.assertContains(response, f'href="{ACT_URL}@2025-02-28/art_2/en/"')
        response = self.client.get(
            ACT_URL + "@2025-06-01/art_2/", {"lang": "en"}, follow=True
        )
        self.assertContains(
            response, "This provision ceased to have effect on 01.03.2025."
        )

    def test_repealed_article_before_repeal_shows_text(self):
        self.add_repealed_article()
        response = self.client.get(ACT_URL + "@2025-02-28/art_2/")
        self.assertTemplateNotUsed(response, "site/repealed.html")
        self.assertContains(response, "Καταργούμενο")

    def test_not_yet_existing_article_is_404(self):
        self.add_repealed_article()
        response = self.client.get(ACT_URL + "@2025-02-28/art_3/")
        self.assertEqual(response.status_code, 404)
        response = self.client.get(ACT_URL + "@2025-06-01/art_3/")
        self.assertContains(response, "Νέο άρθρο")

    def test_amended_version_links_to_provision(self):
        response = self.client.get(ACT_URL + "@2025-02-01/art_1/")
        self.assertContains(response, 'href="/cy/law/2024/2_I/art_2/"')

    def test_today_shows_future_version(self):
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "Νέο κείμενο")
        future_url = f"{ACT_URL}@{self.future_day.isoformat()}/art_1.par_1/"
        self.assertContains(response, f'href="{future_url}"')
        response = self.client.get(future_url, follow=True)
        self.assertContains(response, "Μελλοντικό κείμενο")

    def test_date_before_2024_is_rejected(self):
        response = self.client.get(ACT_URL + "@2023-12-31/art_1/")
        self.assertEqual(response.status_code, 404)

    def test_date_form_redirects_to_address(self):
        response = self.client.get(ACT_URL + "art_1/", {"on": "2024-06-01"})
        self.assertRedirects(response, ACT_URL + "@2024-06-01/art_1/")
        response = self.client.get(ACT_URL + "art_1/", {"on": "2020-01-01"})
        self.assertRedirects(response, ACT_URL + "@2024-01-01/art_1/")

    def test_fragment_anchor(self):
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, 'id="art_1.par_1"')
        self.assertContains(response, 'href="#art_1.par_1"')
        response = self.client.get(ACT_URL + "@2024-06-01/art_1.par_1/")
        self.assertRedirects(
            response,
            ACT_URL + "@2024-06-01/art_1/#art_1.par_1",
            fetch_redirect_response=False,
        )

    def add_ru(self):
        return Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
        )

    def test_act_without_translation_offers_no_russian(self):
        response = self.client.get(ACT_URL + "art_1/")
        self.assertEqual(
            [lang["code"] for lang in response.context["langs"]], ["el"]
        )
        self.assertEqual(
            self.client.get(ACT_URL + "art_1/ru/").status_code, 404
        )

    def test_history_keeps_language(self):
        self.add_ru()
        response = self.client.get(ACT_URL + "art_1.par_1/history/el/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["route"].lang, "el")
        switch = {
            lang["code"]: lang["url"] for lang in response.context["langs"]
        }
        self.assertEqual(switch["ru"], ACT_URL + "art_1.par_1/history/")
        response = self.client.get(ACT_URL + "art_1.par_1/history/ru/")
        self.assertRedirects(
            response,
            ACT_URL + "art_1.par_1/history/",
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_history_leads_back_to_current_text(self):
        self.add_ru()
        response = self.client.get(
            ACT_URL + "@2024-06-01/art_1.par_1/history/el/"
        )
        self.assertContains(response, f'href="{ACT_URL}art_1/el/#art_1.par_1"')
        self.assertContains(
            response, f'href="{ACT_URL}@2024-06-01/art_1/el/#art_1.par_1"'
        )
        urls = [entry["url"] for entry in response.context["versions"]]
        self.assertTrue(all(url.endswith("/el/#art_1.par_1") for url in urls))
        self.assertIn(f"{ACT_URL}art_1/el/#art_1.par_1", urls)

    def test_history_shows_changes_of_nested_fragments(self):
        response = self.client.get(ACT_URL + "art_1/history/")
        self.assertNotContains(response, "Καμία τροποποίηση")
        self.assertContains(response, "Αλλαγές σε επιμέρους διατάξεις")
        self.assertContains(response, "ιστορικό της διάταξης")
        nested = response.context["nested"]
        self.assertEqual(nested[0]["start"], datetime.date(2025, 1, 1))
        self.assertEqual(nested[0]["place"], "(1)")
        self.assertEqual(nested[0]["version"], self.new)
        self.assertTrue(nested[0]["introduced"]["label"].endswith(", art_2"))
        self.assertEqual(nested[0]["url"], f"{ACT_URL}art_1/#art_1.par_1")
        self.assertContains(response, f'href="{ACT_URL}art_1.par_1/history/"')
        self.assertTrue(
            all(entry["version"].amendment.kind != "base" for entry in nested)
        )
        response = self.client.get(ACT_URL + "prt_I/history/")
        self.assertEqual(response.context["nested"][0]["place"], "1(1)")

    def test_section_address_leads_to_its_first_page(self):
        """У раздела своей страницы нет: адрес раздела ведёт на первую страницу
        в нём (не 500)."""
        response = self.client.get(ACT_URL + "prt_I/")
        self.assertRedirects(
            response, ACT_URL + "art_1/", fetch_redirect_response=False
        )
        response = self.client.get(
            ACT_URL + "@2024-06-01/prt_I/", {"orig": "1"}
        )
        self.assertRedirects(
            response,
            ACT_URL + "@2024-06-01/art_1/?orig=1",
            fetch_redirect_response=False,
        )

    def test_empty_section_address_is_not_found(self):
        Fragment.objects.create(
            uid="lw00000099", act=self.law, kind="prt", eid="prt_II"
        )
        self.assertEqual(self.client.get(ACT_URL + "prt_II/").status_code, 404)

    def test_history_without_any_changes_says_so(self):
        response = self.client.get(ACT_URL + "art_1.par_2/history/")
        self.assertContains(response, "Καμία τροποποίηση")
        self.assertEqual(response.context["nested"], [])

    def test_dated_page_shows_way_back(self):
        response = self.client.get(ACT_URL + "@2024-06-01/art_1/")
        self.assertContains(response, "Βλέπετε την έκδοση της 01.06.2024")
        self.assertContains(response, f'href="{ACT_URL}art_1/"')
        response = self.client.get(ACT_URL + "art_1/")
        self.assertNotContains(response, "Βλέπετε την έκδοση")
        self.assertContains(response, f'href="{ACT_URL}art_1.par_1/history/"')

    def test_page_does_not_link_to_itself(self):
        response = self.client.get(ACT_URL + "art_1/")
        self.assertNotContains(response, f'href="{ACT_URL}art_1/"')
        self.assertNotContains(response, 'href="#art_1"')
        self.assertContains(response, 'href="#art_1.par_1"')
        response = self.client.get(ACT_URL)
        self.assertNotContains(response, f'href="{ACT_URL}"')

    def test_language_kept_outside_act(self):
        self.add_ru()
        response = self.client.get(ACT_URL + "art_1/el/")
        self.assertContains(response, 'href="/cy/?lang=el"')
        self.assertContains(response, 'href="/about/?lang=el"')
        response = self.client.get("/cy/", {"lang": "el"})
        self.assertContains(response, f'href="{ACT_URL}el/"')
        response = self.client.get("/cy/")
        self.assertContains(response, f'href="{ACT_URL}"')

    def test_interface_follows_page_language(self):
        self.add_ru()
        response = self.client.get(ACT_URL + "art_1/el/")
        self.assertContains(response, 'lang="el"')
        self.assertContains(response, "Κύπρος")
        self.assertContains(response, "Περιεχόμενα")
        self.assertContains(response, "Έκδοση κατά ημερομηνία")
        self.assertNotContains(response, "Оглавление")
        self.assertNotContains(response, "Редакция на дату")
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "Оглавление")
        self.assertContains(response, "Кипр")

    def test_interface_language_on_list(self):
        response = self.client.get("/cy/", {"lang": "el"})
        self.assertContains(response, "Κύπρος: πράξεις")
        response = self.client.get("/cy/")
        self.assertContains(response, "Кипр: акты")

    def test_reference_leads_there_and_back(self):
        text = FragmentText.objects.get(version=self.new)
        text.body = '<p>Βλ. <ref id="r1">άρθρο 2</ref>.</p>'
        text.save()
        Reference.objects.create(
            from_text=text,
            anchor="r1",
            to_act=self.amending,
            to_fragment=self.amending.fragments.get(eid="art_2"),
            resolved=True,
        )
        page = ACT_URL + "@2025-06-01/art_1/"
        response = self.client.get(page)
        self.assertContains(response, 'id="ref-art_1.par_1-r1"')
        found = re.search(
            r'<a class="ref" id="[^"]*" href="([^"]*)"',
            response.content.decode(),
        )
        href = html.unescape(found.group(1))
        self.assertTrue(
            href.startswith("/cy/law/2024/2_I/@2025-06-01/art_2/?")
        )
        # на странице цели — путь назад к месту ссылки, той же дате и языку
        response = self.client.get(href)
        self.assertContains(response, "← Πίσω στο άρθρο 1")
        self.assertContains(response, f'href="{page}#ref-art_1.par_1-r1"')
        # чужой адрес возврата не принимается
        response = self.client.get(
            "/cy/law/2024/2_I/art_2/", {"from": "//example.com/"}
        )
        self.assertNotContains(response, "back-ref")

    def test_reference_to_paragraph_keeps_way_back(self):
        origin = "/cy/law/2024/2_I/art_2/"
        query = {"from": origin, "at": "ref-art_2-r1"}
        response = self.client.get(ACT_URL + "@2025-06-01/art_1.par_1/", query)
        self.assertRedirects(
            response,
            ACT_URL
            + "@2025-06-01/art_1/?from=%2Fcy%2Flaw%2F2024%2F2_I%2Fart_2%2F"
            "&at=ref-art_2-r1#art_1.par_1",
            fetch_redirect_response=False,
        )
        response = self.client.get(ACT_URL + "@2025-06-01/art_1/", query)
        self.assertContains(response, "← Πίσω στο άρθρο 2")
        self.assertContains(response, f'href="{origin}#ref-art_2-r1"')

    def test_back_link_follows_switched_language(self):
        self.add_ru()
        page = ACT_URL + "@2025-06-01/art_1/"
        request = RequestFactory().get(
            "/", {"from": page, "at": "ref-art_1.par_1-r1"}
        )
        # с переводом язык акта по умолчанию — русский; язык не меняли — к
        # самой ссылке
        back = views.back_link(request, "ru")
        self.assertEqual(back["url"], page + "#ref-art_1.par_1-r1")
        # перешли на греческий — назад на греческую страницу, к пункту ссылки
        back = views.back_link(request, "el")
        self.assertEqual(back["url"], page + "el/#art_1.par_1")

    def test_language_switch_keeps_original_mode(self):
        self.add_ru()
        response = self.client.get(
            ACT_URL + "@2024-06-01/art_1/", {"orig": "1"}
        )
        switch = {
            lang["code"]: lang["url"] for lang in response.context["langs"]
        }
        self.assertEqual(
            switch["el"], ACT_URL + "@2024-06-01/art_1/el/?orig=1"
        )
        self.assertContains(response, "data-keep-hash")

    def test_missing_translation_falls_back_to_greek(self):
        self.add_ru()
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "Νέο κείμενο")
        self.assertContains(response, "Перевод готовится")

    def test_translation_with_original_and_note(self):
        ru = Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
        )
        text = FragmentText.objects.create(
            version=self.new,
            expression=ru,
            body="<p>Новый текст</p>",
            text_hash="ru",
        )
        Note.objects.create(
            target=text, kind="doubt", origin="llm", body="Сомнительный термин"
        )
        Note.objects.create(
            target=text,
            kind="source_comment",
            origin="llm",
            body="Сноска источника",
        )
        Note.objects.create(
            target=text,
            kind="editor_comment",
            origin="llm",
            body="Заметка редактора",
        )
        response = self.client.get(ACT_URL + "art_1/", {"orig": "1"})
        self.assertContains(response, "Новый текст")
        self.assertContains(response, "Νέο κείμενο")
        self.assertContains(response, "Сомнительный термин")
        self.assertContains(response, "unit--with-original")
        self.assertContains(
            response, "Перевод выполнен ИИ, человеком не выверен"
        )
        self.assertNotContains(response, "Сноска источника")
        self.assertNotContains(response, "Заметка редактора")
        self.assertContains(
            response, 'class="fragment__original"\n            lang="el"'
        )

    def test_translated_heading(self):
        ru = Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.create(
                code="nomos", name="Неофициальный перевод"
            ),
        )
        article = self.law.fragments.get(eid="art_1")
        FragmentText.objects.create(
            version=article.versions.get(),
            expression=ru,
            body="",
            heading="Толкование",
            text_hash="ru-heading",
        )
        FragmentText.objects.create(
            version=self.new,
            expression=ru,
            body="<p>Новый текст</p>",
            text_hash="ru-body",
        )
        response = self.client.get(ACT_URL)
        self.assertRegex(
            response.content.decode(), r'lang="ru"\s*>\s*Статья 1\. Толкование'
        )
        self.assertNotContains(response, "перевод готовится")
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(
            response, "Статья 1. Толкование — Закон 1(I)/2000 — Nomos"
        )
        self.assertContains(
            response, "Неофициальный перевод. Перевод выполнен ИИ"
        )
        self.assertNotContains(response, "Текст неизвестен</p>")
        self.assertNotContains(response, "Перевод готовится")
        self.assertContains(response, 'class="crumbs__item"\n      lang="el"')

    def test_untranslated_article_is_marked(self):
        Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
        )
        response = self.client.get(ACT_URL)
        self.assertContains(response, "перевод готовится")
        self.assertContains(response, "toc__link--pending")
        self.assertRegex(
            response.content.decode(), r'lang="el"\s*>\s*Άρθρο 1\. Ερμηνεία'
        )
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(
            response,
            "Перевод готовится — показан греческий текст.",
        )

    def test_side_column(self):
        response = self.client.get("/cy/")
        self.assertNotContains(response, "sidenav")
        response = self.client.get(ACT_URL)
        self.assertContains(response, "sidenav--wide-only")
        self.assertContains(response, "toc--main")
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "pager__link--toc")
        self.assertContains(response, "Περιεχόμενα της πράξης")
        self.assertContains(response, 'href="/cy/?lang=el"')
        self.assertContains(response, "toc__link toc__link--current")
        response = self.client.get(ACT_URL + "art_1.par_1/history/")
        self.assertContains(response, "toc__link--current")

    def test_empty_section_has_same_layout(self):
        self.client.force_login(
            User.objects.create_superuser("empty_section_admin")
        )
        response = self.client.get("/ifrs/")
        self.assertContains(response, "В разделе пока нет актов")
        self.assertNotContains(response, "Изменяющие акты")

    def test_legal_minimum(self):
        response = self.client.get("/cy/")
        self.assertContains(response, 'name="robots"')
        self.assertContains(
            response, "Сайт не даёт юридических и налоговых консультаций"
        )
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, 'lang="el"\n  >\n    Το Nomos είναι')
        self.assertContains(response, "Η έκδοση ανασυντέθηκε")
        response = self.client.get("/robots.txt")
        self.assertContains(response, "Disallow: /")
        self.assertEqual(self.client.get("/admin/").status_code, 404)
        response = self.client.get("/about/")
        self.assertContains(response, "Cookie используется только для входа")
        self.assertContains(
            response, "CC BY 4.0 — только наши переводы и разметка"
        )
        self.assertContains(response, "Ilia Tsigelnitskii")

    def test_short_label_of_kdp_is_number_and_date(self):
        kdp = Act.objects.create(
            uid="cy/kdp/2024/95",
            jurisdiction="cy",
            kind="kdp",
            number="95",
            year="2024",
            published_on=datetime.date(2024, 3, 15),
        )
        with translation.override("ru"):
            label = reading.act_short_label(kdp, "ru")
        self.assertEqual(str(label), "ΚΔΠ 95/2024 от 15.03.2024")
        with translation.override("el"):
            label = reading.act_short_label(kdp, "el")
        self.assertEqual(str(label), "ΚΔΠ 95/2024 της 15.03.2024")

    def test_interface_language_by_django_catalogs_without_cookies(self):
        response = self.client.get("/cy/?lang=el")
        self.assertContains(response, "Τροποποιητικοί νόμοι")
        self.assertContains(response, 'lang="el"\n  >\n    Το Nomos είναι')
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertNotIn("django_language", response.cookies)
        response = self.client.get("/cy/")
        self.assertContains(response, "Изменяющие законы")
        with translation.override("en"):
            self.assertTrue(
                str(notices.DISCLAIMER).startswith("Nomos is a private")
            )

    def test_short_label_of_amending_law(self):
        Expression.objects.filter(act=self.law).update(
            short_title="Ο περί Δοκιμής Νόμος"
        )
        self.assertEqual(
            str(reading.act_short_label(self.amending, "el")),
            "Δοκιμής (Τροποποιητικός) 2(I)/2024",
        )
        Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
            short_title="Закон об испытании",
        )
        self.assertEqual(
            str(reading.act_short_label(self.amending, "ru")),
            "Закон об испытании (изм.) 2(I)/2024",
        )

    def test_acts_sorted_by_year_and_number(self):
        acts = [
            Act(number="363", year="2026"),
            Act(number="82", year="2026"),
            Act(number="102", year="2025"),
        ]
        acts.sort(key=reading.act_sort_key)
        self.assertEqual([act.number for act in acts], ["102", "82", "363"])

    def test_home_has_first_screen(self):
        response = self.client.get("/")
        self.assertContains(response, 'class="hero"')
        self.assertContains(response, f'href="{ACT_URL}"')
        self.assertContains(response, 'href="/about/"')
        self.assertNotContains(self.client.get("/cy/"), 'class="hero"')

    def test_home_says_who_and_what(self):
        response = self.client.get("/")
        self.assertContains(response, "прежде всего Кипра")
        self.assertContains(response, "ссылка на официальный текст")
        self.assertContains(response, "Перевод сделан ИИ и не выверен")
        self.assertContains(response, "Источники: CyLaw, EUR-Lex, OJ")
        self.assertRegex(
            response.content.decode(),
            r'class="hero__sources"\s+href="/about/"',
        )
        self.assertContains(response, 'class="acts__section"')

    def test_about_counts_only_sources_with_texts(self):
        Source.objects.create(code="empty", name="Пустой источник")
        response = self.client.get("/about/")
        self.assertNotContains(response, "Пустой источник")
        self.assertNotContains(response, "(текстов: 0)")
        count = Source.objects.get(code="test").expressions.count()
        self.assertContains(response, f"(текстов: {count})")
        self.assertContains(response, "Для кого этот сайт")
        self.assertContains(response, "Сообщество")
        self.assertContains(response, "законодательство Кипра и акты ЕС")

    def test_russian_is_default_where_translated(self):
        # без русского текста адрес без языка — греческий, /ru/ — явный выбор
        self.assertContains(self.client.get(ACT_URL), 'lang="el"')
        self.assertEqual(self.client.get(ACT_URL + "el/").status_code, 301)
        Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
        )
        response = self.client.get(ACT_URL + "art_1/ru/", {"orig": "1"})
        self.assertRedirects(
            response,
            ACT_URL + "art_1/?orig=1",
            status_code=301,
            fetch_redirect_response=False,
        )
        response = self.client.get(ACT_URL + "art_1/")
        self.assertEqual(response.context["route"].lang, "ru")
        response = self.client.get(ACT_URL + "el/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["route"].lang, "el")
        self.assertContains(response, f'href="{ACT_URL}art_1/el/"')

    def test_history_has_human_label(self):
        response = self.client.get(ACT_URL + "art_1.par_1/history/")
        self.assertRegex(
            response.content.decode(), r"Εκδόσεις: Άρθρο 1\. Ερμηνεία, \(?1"
        )
        self.assertNotContains(response, "Εκδόσεις art_1")

    def test_unknown_text_is_marked(self):
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "Άγνωστο κείμενο")
        # рабочая заметка редактора на сайте не выводится
        self.assertNotContains(response, "Текст этой редакции неизвестен.")

    def test_history_lists_versions(self):
        response = self.client.get(ACT_URL + "art_1.par_1/history/")
        self.assertEqual(len(response.context["versions"]), 3)
        self.assertContains(response, "έως 01.01.2024")
        self.assertContains(response, "από 01.01.2025")

    def test_cylaw_credit_only_on_cylaw_texts(self):
        pages = [ACT_URL, ACT_URL + "art_1/", ACT_URL + "art_1.par_1/history/"]
        # источник «test» — как акт из Официальной газеты: подписи CyLaw нет
        for page in pages:
            self.assertNotContains(self.client.get(page), "www.cylaw.org")
        self.el.source = Source.objects.create(code="cylaw", name="CyLaw")
        self.el.save()
        for page in pages:
            response = self.client.get(page)
            self.assertContains(response, 'class="source-credit"')
            self.assertContains(
                response, "Πηγή κειμένου: ενοποιημένο κείμενο του CyLaw"
            )

    def test_cylaw_source_line_on_act_page(self):
        """«Источник: CyLaw (cylaw.org)» со ссылкой — только у актов CyLaw,
        подпись на языке страницы."""

        def link(caption, lang):
            """Адрес ссылки в строке «<caption> CyLaw (cylaw.org)»; нет —
            None."""
            # акт заново из базы: языки акта запоминаются на объекте
            act = Act.objects.get(pk=self.law.pk)
            response = self.client.get(reading.build_url(act, lang=lang))
            found = re.search(
                r'class="original-source">\s*%s\s*<a\s+'
                r'class="original-source__link"\s+href="([^"]*)"\s+'
                r'rel="external"\s*>\s*CyLaw \(cylaw\.org\)\s*</a>'
                % re.escape(caption),
                response.content.decode(),
            )
            return found.group(1) if found else None

        self.assertIsNone(link("Πηγή:", "el"))
        fields = load_texts.SOURCES["cylaw"]
        self.el.source = Source.objects.create(code="cylaw", **fields)
        self.el.save()
        for lang in ("en", "ru"):
            Expression.objects.create(
                act=self.law,
                lang=lang,
                status="our_translation",
                source=Source.objects.get(code="test"),
                title="Law",
            )
        captions = {"el": "Πηγή:", "en": "Source:", "ru": "Источник:"}
        for lang, caption in captions.items():
            with self.subTest(lang=lang):
                self.assertEqual(link(caption, lang), "https://www.cylaw.org")
        self.el.url = "https://www.cylaw.org/nomoi/enop/non-ind/2000_1_1/"
        self.el.save()
        self.assertEqual(link("Источник:", "ru"), self.el.url)

    def test_source_labels_follow_interface_language(self):
        fields = load_texts.SOURCES["cylaw"]
        self.el.source = Source.objects.create(code="cylaw", **fields)
        self.el.save()
        response = self.client.get(ACT_URL)
        self.assertContains(response, "CyLaw — ανεπίσημο ενοποιημένο κείμενο")
        self.assertContains(
            response, "Ενοποιημένο κείμενο του CyLaw (www.cylaw.org, ΚΙΝΟΠ)"
        )
        self.assertNotContains(response, fields["name"])
        self.assertNotContains(response, fields["attribution"])

    def test_publication_lines_on_act_page(self):
        """Где опубликован акт и каждый изменяющий: «Επίσημη Εφημερίδα,
        Παράρτημα …, Αρ. N, дата» — в сведениях и в списках актов."""
        Act.objects.filter(pk=self.law.pk).update(
            published_in="Ε.Ε. Παρ. Ι(Ι) Αρ. 4154",
            published_on=datetime.date(2007, 12, 31),
        )
        Act.objects.filter(pk=self.amending.pk).update(
            published_in="Ε.Ε. Παρ. Ι(Ι) Αρ. 5070",
            published_on=datetime.date(2024, 12, 1),
        )
        law_line = "Επίσημη Εφημερίδα, Παράρτημα Ι(Ι), Αρ. 4154, 31.12.2007"
        amending_line = (
            "Επίσημη Εφημερίδα, Παράρτημα Ι(Ι), Αρ. 5070, 01.12.2024"
        )
        # закон: своя публикация и публикация изменяющего в списке изменений
        response = self.client.get(ACT_URL)
        self.assertContains(response, "Δημοσίευση:")
        self.assertContains(response, law_line)
        self.assertContains(response, 'class="acts__publication"')
        self.assertContains(response, amending_line)
        # изменяющий: своя публикация и публикация изменяемого в «Изменяет:»
        response = self.client.get("/cy/law/2024/2_I/")
        self.assertContains(response, amending_line)
        self.assertContains(response, law_line)

    def test_publication_formats(self):
        act = Act(published_in="", published_on=None)
        self.assertIsNone(views.publication(act))
        act.published_in = "OJ L 347, 11.12.2006, p. 1"
        self.assertEqual(
            views.publication(act),
            {"text": "OJ L 347, 11.12.2006, p. 1", "lang": "en"},
        )
        act.published_in = "Ε.Ε. Παρ. ΙΙΙ(Ι) Αρ. 3846Α"
        self.assertEqual(
            views.publication(act)["text"],
            "Επίσημη Εφημερίδα, Παράρτημα ΙΙΙ(Ι), Αρ. 3846Α",
        )
        act.published_in = "Τμήμα Φορολογίας"
        self.assertEqual(
            views.publication(act),
            {"text": "Τμήμα Φορολογίας", "lang": "el"},
        )

    def test_nomos_credit_only_on_gazette_texts(self):
        """Оговорка Nomos — у текстов из газеты; у CyLaw — прежняя подпись."""
        credit = (
            "Сводная редакция и русский перевод — Nomos, неофициальные "
            "(CC BY 4.0); юридическую силу имеет только текст в Ε.Ε."
        )
        ru = Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
            title="Закон об испытании",
        )
        act = Act.objects.get(pk=self.law.pk)
        ru_url = reading.build_url(act, lang="ru")
        self.assertNotContains(self.client.get(ru_url), credit)
        self.el.source = Source.objects.create(
            code="cy-gazette", **load_texts.SOURCES["cy-gazette"]
        )
        self.el.save()
        pages = [
            ru_url,
            reading.build_url(act, eid="art_1", lang="ru"),
            reading.build_url(act, eid="art_1.par_1", lang="ru", history=True),
        ]
        for page in pages:
            with self.subTest(page=page):
                self.assertContains(self.client.get(page), credit)
        self.assertContains(
            self.client.get(reading.build_url(act, lang="el")),
            "νομική ισχύ έχει μόνο το κείμενο της Ε.Ε.",
        )
        self.assertNotContains(self.client.get(ru_url), "www.cylaw.org")
        # CyLaw: подпись CyLaw, оговорки Nomos нет
        ru.source = Source.objects.create(
            code="cylaw", **load_texts.SOURCES["cylaw"]
        )
        ru.save()
        response = self.client.get(ru_url)
        self.assertContains(response, "www.cylaw.org")
        self.assertNotContains(response, credit)

    def test_translation_notice_on_act_page(self):
        """Плашка «перевод ИИ» на странице акта — только на языке перевода."""
        Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
            title="Закон об испытании",
        )
        act = Act.objects.get(pk=self.law.pk)
        response = self.client.get(reading.build_url(act, lang="ru"))
        self.assertContains(response, 'class="draft-warning"')
        self.assertContains(
            response, "Перевод выполнен ИИ, человеком не выверен."
        )
        response = self.client.get(reading.build_url(act, lang="el"))
        self.assertNotContains(response, 'class="draft-warning"')

    def test_no_official_text_label(self):
        """Метки «Официальный текст» у сводных текстов нет: «Текст по Ε.Ε.»."""
        self.assertEqual(
            str(views.STATUS_NAMES[Expression.Status.OFFICIAL_TEXT]),
            "Текст по Ε.Ε.",
        )
        with translation.override("el"):
            self.assertEqual(
                str(views.STATUS_NAMES[Expression.Status.OFFICIAL_TEXT]),
                "Κείμενο κατά την Ε.Ε.",
            )
        Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
            title="Закон об испытании",
        )
        act = Act.objects.get(pk=self.law.pk)
        pages = [
            reading.build_url(act, lang=lang) for lang in ("el", "ru")
        ] + [
            reading.build_url(act, eid="art_1", lang="ru"),
            reading.build_url(act, eid="art_1", lang="el"),
            ACT_URL + "art_1.par_1/history/",
        ]
        for page in pages:
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertNotContains(response, "Официальный текст")
                self.assertNotContains(response, "Επίσημο κείμενο")
                self.assertNotContains(response, "επίσημο ελληνικό")

    def test_preamble_label_follows_interface_language(self):
        preamble = Fragment.objects.create(
            uid="lw00000099",
            act=self.law,
            kind="cit",
            eid="cit_1",
            order_key="0000",
        )
        base = self.old.amendment
        self.add_version(
            preamble, "base", base, "", self.el, "Η Βουλή των Αντιπροσώπων"
        )
        self.assertContains(self.client.get(ACT_URL), "Προοίμιο")
        self.assertNotContains(self.client.get(ACT_URL), "Преамбула")

    def link_texts_both_ways(self):
        """Ст. 1 закона ссылается на ст. 2 изменяющего, та — обратно: цепочка
        любой длины."""
        text = FragmentText.objects.get(version=self.new)
        text.body = '<p>Βλ. <ref id="r1">άρθρο 2</ref>.</p>'
        text.save()
        provision = self.amending.fragments.get(eid="art_2")
        Reference.objects.create(
            from_text=text,
            anchor="r1",
            to_act=self.amending,
            to_fragment=provision,
            resolved=True,
        )
        back = FragmentText.objects.get(version__fragment=provision)
        back.body = '<p>Βλ. <ref id="r1">άρθρο 1</ref>.</p>'
        back.save()
        Reference.objects.create(
            from_text=back,
            anchor="r1",
            to_act=self.law,
            to_fragment=self.law.fragments.get(eid="art_1"),
            resolved=True,
        )

    def follow_ref(self, response):
        found = re.search(
            r'<a class="ref" id="[^"]*" href="([^"]*)"',
            response.content.decode(),
        )
        return html.unescape(found.group(1))

    def test_chain_start_stays_on_screen(self):
        self.link_texts_both_ways()
        start = ACT_URL + "@2025-06-01/art_1/"
        start_link = f'href="{start}#ref-art_1.par_1-r1"'
        response = self.client.get(start)
        self.assertNotContains(response, "chain-start")
        # три перехода по ссылкам: A → B → C → D; «К началу» на каждом шаге
        # ведёт в A
        steps = []
        for _step in range(3):
            href = self.follow_ref(response)
            response = self.client.get(href)
            self.assertContains(response, 'class="chain-start"')
            self.assertContains(response, "Στην αρχή: άρθρο 1")
            self.assertContains(response, start_link)
            steps.append((href, response))
        # «← Назад» — на один шаг: из C в B, с началом цепочки
        back = steps[1][1].context["back"]["url"]
        self.assertTrue(back.startswith(steps[0][0].split("?")[0] + "?start="))
        self.assertContains(self.client.get(back), start_link)
        # из D — в C; C здесь и есть A, начало цепочки не нужно
        self.assertEqual(
            response.context["back"]["url"], start + "#ref-art_1.par_1-r1"
        )
        # из A по «К началу» цепочка кончается
        self.assertNotContains(self.client.get(start), "chain-start")

    def test_chain_start_survives_language_history_and_dates(self):
        self.add_ru()
        self.link_texts_both_ways()
        start = ACT_URL + "@2025-06-01/art_1/el/"
        chain = {"start": start, "start_at": "ref-art_1.par_1-r1"}
        page = "/cy/law/2024/2_I/@2025-06-01/art_2/"
        response = self.client.get(
            page, {"from": start, "at": "ref-art_1.par_1-r1"}
        )
        # смена языка: в русском тексте этой ссылки нет — к её пункту русской
        # страницы A
        response = self.client.get(ACT_URL + "@2025-06-01/art_1/", chain)
        self.assertEqual(
            response.context["chain_start"]["url"],
            ACT_URL + "@2025-06-01/art_1/#art_1.par_1",
        )
        switch = {
            lang["code"]: lang["url"] for lang in response.context["langs"]
        }
        self.assertIn("start=", switch["el"])
        # история и редакции из неё несут начало цепочки
        history = response.context["root"].children[0].history_url
        self.assertIn("start=", history)
        response = self.client.get(history)
        self.assertContains(response, 'class="chain-start"')
        self.assertTrue(
            all(
                "start=" in entry["url"]
                for entry in response.context["versions"]
            )
        )
        # форма даты переносит начало цепочки на новую страницу
        response = self.client.get(
            ACT_URL + "art_1/", {"on": "2024-06-01", **chain}
        )
        self.assertIn("start=", response["Location"])

    def test_chain_start_leads_to_reference_after_language_switch(self):
        """id ссылок в el и ru совпадают — «К началу» ведёт к месту ссылки, не
        к пункту."""
        ru = self.add_ru()
        self.link_texts_both_ways()
        text = FragmentText.objects.create(
            version=self.new,
            expression=ru,
            body='<p>См. <ref id="r1">статью 2</ref>.</p>',
            text_hash="ru",
        )
        Reference.objects.create(
            from_text=text,
            anchor="r1",
            to_act=self.amending,
            to_fragment=self.amending.fragments.get(eid="art_2"),
            resolved=True,
        )
        chain = {
            "start": ACT_URL + "@2025-06-01/art_1/el/",
            "start_at": "ref-art_1.par_1-r1",
        }
        # на русской странице цепочки: «К началу» — к той же ссылке русского
        # текста
        response = self.client.get(ACT_URL + "@2025-06-01/art_1/", chain)
        self.assertEqual(
            response.context["chain_start"]["url"],
            ACT_URL + "@2025-06-01/art_1/#ref-art_1.par_1-r1",
        )
        self.assertContains(response, 'id="ref-art_1.par_1-r1"')

    def test_chain_keeps_full_anchor_through_history_and_language(self):
        self.add_ru()
        self.link_texts_both_ways()
        start = ACT_URL + "@2025-06-01/art_1/el/"
        anchor = "ref-art_1.par_1-r1"
        chain = {"start": start, "start_at": anchor}
        # история на другом языке передаёт якорь целиком
        response = self.client.get(ACT_URL + "art_1/history/", chain)
        self.assertEqual(response.context["route"].chain, chain)
        switch = {
            lang["code"]: lang["url"] for lang in response.context["langs"]
        }
        self.assertIn("start_at=" + anchor, switch["el"])
        self.assertNotContains(response, "start_at=art_1.par_1")
        # после смены языка обратно «К началу» ведёт к самой ссылке
        response = self.client.get(switch["el"])
        self.assertEqual(
            response.context["chain_start"]["url"], start + "#" + anchor
        )

    def test_about_in_greek(self):
        Source.objects.filter(code="test").update(
            name="Официальная газета Республики Кипр"
        )
        response = self.client.get("/about/", {"lang": "el"})
        self.assertContains(response, "Σχετικά με το έργο")
        self.assertContains(
            response, "Δημιουργός του έργου: Ilia Tsigelnitskii."
        )
        self.assertContains(
            response, "Επίσημη Εφημερίδα της Κυπριακής Δημοκρατίας"
        )
        self.assertContains(
            response, '(<a href="https://www.cylaw.org" rel="external">'
        )
        article = re.search(
            r"<article.*</article>", response.content.decode(), re.S
        ).group()
        self.assertIsNone(re.search("[а-яё]", article, re.I))
        # по-русски страница прежняя
        self.assertContains(
            self.client.get("/about/"), "Автор проекта — Ilia Tsigelnitskii."
        )

    def test_pages_open_without_login_by_default(self):
        """NOMOS_PUBLIC_ONLY выключена: без входа видно всё; вход — отдельная
        страница."""
        pages = [
            "/",
            "/cy/",
            ACT_URL,
            ACT_URL + "art_1/",
            ACT_URL + "art_1.par_1/history/",
            "/about/",
            "/about/?lang=el",
            "/login/",
            "/robots.txt",
        ]
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 200, page)
        self.assertNotContains(self.client.get("/"), 'action="/logout/"')

    def test_login_returns_only_to_own_address(self):
        User.objects.create_user("editor", password="test-password-1")
        self.client.logout()
        credentials = {"username": "editor", "password": "test-password-1"}
        response = self.client.post(
            "/login/", {**credentials, "next": ACT_URL + "art_1/"}
        )
        self.assertRedirects(
            response, ACT_URL + "art_1/", fetch_redirect_response=False
        )
        self.client.logout()
        response = self.client.post(
            "/login/", {**credentials, "next": "https://example.com/"}
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_logout_from_menu(self):
        self.client.force_login(self.reader)
        response = self.client.get("/")
        self.assertContains(response, 'action="/logout/"')
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertNotContains(self.client.get("/"), 'action="/logout/"')

    def test_login_page_in_greek(self):
        self.client.logout()
        response = self.client.get("/login/", {"lang": "el"})
        self.assertContains(response, "Σύνδεση")


class SignedIn:
    """Пользователь запроса, вошедший на сайт (без записи в базе)."""

    is_authenticated = True
    is_superuser = False


# тексты непубличного греческого выражения, которых не должно быть в HTML
# анонима
GREEK_TEXTS = (
    "Νέο κείμενο",
    "Παλαιό κείμενο",
    "Ο περί Δοκιμής Νόμος",
    "Ερμηνεία",
    "ΜΕΡΟΣ I",
    "Τροποποιητικός",
    "Αντικαθίσταται",
)


@override_settings(
    DJANGO_VITE={"default": {"dev_mode": True}}, NOMOS_PUBLIC_ONLY=True
)
class PublicOnlyTests(TestCase):
    """NOMOS_PUBLIC_ONLY: аноним видит только публичные выражения, вошедший —
    всё."""

    setUpTestData = ViewerTests.__dict__["setUpTestData"]
    add_version = ViewerTests.__dict__["add_version"]

    def test_about_hides_sources_without_public_texts(self):
        response = self.client.get("/about/")
        self.assertNotContains(response, "(текстов: 0)")
        self.assertContains(response, "Источников в базе нет.")
        self.add_public_ru()
        self.assertContains(self.client.get("/about/"), "(текстов: 1)")

    def add_public_ru(self, with_text=True):
        ru = Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=Source.objects.get(code="test"),
            title="Закон об испытании",
            public=True,
        )
        if with_text:
            text = FragmentText.objects.create(
                version=self.new,
                expression=ru,
                body='<p>Новый текст, см. <ref id="r1">статью 2</ref>.</p>',
                text_hash="ru",
            )
            Reference.objects.create(
                from_text=text,
                anchor="r1",
                to_act=self.amending,
                to_fragment=self.amending.fragments.get(eid="art_2"),
                resolved=True,
            )
            Note.objects.create(
                target=text,
                kind="doubt",
                origin="llm",
                body="Сомнительный термин",
            )
        return ru

    def test_missing_item_hidden(self):
        NavNode.objects.create(
            parent=NavNode.objects.get(key="taxes"),
            label="Подоходный налог",
            order=50,
            act_uid="cy/law/2002/118_I",
        )
        self.assertNotContains(self.client.get("/cy/taxes/"), "не загружен")

    def assertNoGreek(self, response):
        for greek in GREEK_TEXTS:
            self.assertNotContains(response, greek)

    def test_setting_off_shows_everything(self):
        with self.settings(NOMOS_PUBLIC_ONLY=False):
            self.assertContains(self.client.get("/"), "Ο περί Δοκιμής Νόμος")
            response = self.client.get(ACT_URL + "art_1/")
            self.assertContains(response, "Νέο κείμενο")
            self.assertContains(
                self.client.get("/cy/law/2024/2_I/"), "Τροποποιητικός"
            )

    def test_act_without_public_expression_is_hidden(self):
        self.assertNotContains(self.client.get("/"), ACT_URL)
        self.assertNoGreek(self.client.get("/"))
        self.assertEqual(self.client.get(ACT_URL).status_code, 404)
        self.assertEqual(self.client.get(ACT_URL + "art_1/").status_code, 404)
        self.assertEqual(
            self.client.get(ACT_URL + "art_1.par_1/history/").status_code, 404
        )

    def test_public_translation_without_original(self):
        self.add_public_ru()
        response = self.client.get("/")
        self.assertContains(response, "Закон об испытании")
        self.assertNoGreek(response)
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "Новый текст")
        self.assertContains(response, "Сомнительный термин")
        self.assertContains(response, "Неофициальный перевод")
        self.assertContains(response, "Оригинал:")
        self.assertNoGreek(response)
        # греческого нет в переключателе, страница на греческом — 404
        el_url = reading.build_url(self.law, eid="art_1", lang="el")
        self.assertNotContains(response, f'href="{el_url}')
        self.assertEqual(self.client.get(el_url).status_code, 404)
        # ссылка на скрытый акт — текстом; изменяющий акт — номером без ссылки
        self.assertContains(
            response, '<span class="ref ref--unresolved">статью 2</span>'
        )
        self.assertNotContains(response, 'href="/cy/law/2024/2_I/')
        self.assertContains(response, "2(I)/2024")

    def test_original_mode_is_off(self):
        self.add_public_ru()
        response = self.client.get(ACT_URL + "art_1/", {"orig": "1"})
        self.assertNotContains(response, "unit--with-original")
        self.assertNotContains(response, "fragment__original")
        self.assertNotContains(response, "orig=1")
        self.assertNoGreek(response)

    def test_untranslated_page_shows_no_greek(self):
        self.add_public_ru(with_text=False)
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "Перевод готовится.")
        self.assertContains(response, "Оригинал:")
        self.assertNoGreek(response)

    def test_original_link_prefers_act_address(self):
        self.add_public_ru()
        Source.objects.filter(code="test").update(url="https://example.org/")
        greek = self.law.expressions.get(lang="el")
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, 'href="https://example.org/"')
        greek.url = "https://example.org/acts/2000_1_1.pdf"
        greek.save()
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(
            response, 'href="https://example.org/acts/2000_1_1.pdf"'
        )
        self.assertNotContains(response, 'href="https://example.org/"')

    def test_act_page_and_history_show_no_greek(self):
        self.add_public_ru()
        response = self.client.get(ACT_URL)
        self.assertContains(response, "Закон об испытании")
        self.assertContains(response, "Оригинал:")
        self.assertNoGreek(response)
        response = self.client.get(ACT_URL + "art_1.par_1/history/")
        self.assertEqual(response.status_code, 200)
        self.assertNoGreek(response)
        self.assertNoGreek(self.client.get("/about/"))

    def test_signed_in_sees_everything(self):
        self.add_public_ru()
        request = RequestFactory().get(ACT_URL + "art_1/", {"orig": "1"})
        request.user = SignedIn()
        middleware = access.PublicOnlyMiddleware(
            lambda request: views.read(request, "cy/law/2000/1_I/art_1/"),
        )
        response = middleware(request)
        self.assertContains(response, "Νέο κείμενο")
        self.assertContains(response, "unit--with-original")
        self.assertFalse(access.is_restricted())

    def test_login_opens_everything_and_logout_hides_again(self):
        """Настоящий вход через форму: со входом — всё, после выхода — только
        публичное."""
        User.objects.create_user("editor", password="test-password-1")
        self.assertEqual(self.client.get(ACT_URL + "art_1/").status_code, 404)
        self.assertNotContains(self.client.get("/"), 'action="/logout/"')
        response = self.client.post(
            "/login/",
            {
                "username": "editor",
                "password": "test-password-1",
                "next": ACT_URL + "art_1/",
            },
        )
        self.assertRedirects(
            response, ACT_URL + "art_1/", fetch_redirect_response=False
        )
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, "Νέο κείμενο")
        self.assertContains(self.client.get("/"), 'action="/logout/"')
        self.client.post("/logout/")
        self.assertEqual(self.client.get(ACT_URL + "art_1/").status_code, 404)

    def test_login_link_and_home_text_follow_sign_in(self):
        """Без входа: пункт «Войти»; со входом — «Выйти». Текст главной
        одинаков для всех."""
        home_text = "Право Кипра и ЕС для русскоязычных жителей"
        response = self.client.get("/")
        self.assertContains(response, 'href="/login/"')
        self.assertContains(response, home_text)
        response = self.client.get("/login/")
        self.assertContains(
            response, 'aria-current="page"\n          >\n            Войти'
        )
        response = self.client.get("/", {"lang": "el"})
        self.assertContains(response, 'href="/login/?lang=el"')
        self.assertContains(response, "Σύνδεση")
        self.assertContains(response, "Κυπριακό δίκαιο και δίκαιο της ΕΕ")
        self.client.force_login(self.reader)
        response = self.client.get("/")
        self.assertNotContains(response, 'href="/login/"')
        self.assertContains(response, 'action="/logout/"')
        self.assertContains(response, home_text)

    def test_no_login_link_when_everything_is_open(self):
        with self.settings(NOMOS_PUBLIC_ONLY=False):
            response = self.client.get("/")
        self.assertNotContains(response, 'href="/login/"')
        self.assertContains(response, "Право Кипра и ЕС")

    def test_anonymous_html_has_no_greek_letters(self):
        """Без входа на ru в HTML нет греческих букв: ни текстов, ни названия
        газеты.

        Подпись «Άρθρο 1» тоже: без греческого оригинала приставка — «Статья».
        """
        self.add_public_ru()
        greek_letters = re.compile("[\u0370-\u03ff\u1f00-\u1fff]+")
        for url in ["/", "/cy/", ACT_URL, ACT_URL + "art_1/", "/login/"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    greek_letters.findall(response.content.decode()), []
                )
        self.assertContains(self.client.get(ACT_URL + "art_1/"), "Статья 1")

    def test_gazette_name_in_greek_after_login(self):
        """Вошедший видит отказ с греческим названием газеты, как без
        NOMOS_PUBLIC_ONLY."""
        self.assertNotContains(
            self.client.get("/"), "Επίσημη Εφημερίδα της Δημοκρατίας"
        )
        self.client.force_login(User.objects.create_user("editor"))
        self.assertContains(
            self.client.get("/"), "Επίσημη Εφημερίδα της Δημοκρατίας"
        )


class ActNumberTests(TestCase):
    """Номер в подписи: год не дублируется, если он уже в номере (акты ЕС)."""

    def test_number_with_year(self):
        cases = [
            (Act(kind="reg", number="904/2010", year="2010"), "904/2010"),
            (Act(kind="reg", number="2025/517", year="2025"), "2025/517"),
            (Act(kind="reg", number="2026/1743", year="2026"), "2026/1743"),
            (Act(kind="dir", number="2020/285", year="2020"), "2020/285"),
            (Act(kind="law", number="95(I)", year="2000"), "95(I)/2000"),
            (Act(kind="kdp", number="159", year="2025"), "159/2025"),
        ]
        for act, number in cases:
            with self.subTest(number=act.number):
                self.assertEqual(reading.act_number(act), number)
                label = reading.act_label(act)
                self.assertTrue(label.endswith(" " + number), label)


class DeployCheckTests(TestCase):
    """manage.py check --deploy при боевых значениях .env: только W021 (HSTS
    preload)."""

    def test_check_deploy(self):
        env = dict(
            os.environ,
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY="x" * 30 + secrets.token_urlsafe(40),
            DJANGO_ALLOWED_HOSTS="example.org",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://example.org",
            NOMOS_PUBLIC_ONLY="1",
        )
        env.pop("DJANGO_SETTINGS_MODULE", None)
        env.pop("DJANGO_HTTPS", None)
        result = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "check",
                "--deploy",
                "--fail-level",
                "ERROR",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # без сборки (npm run build) добавляется django_vite.W001 — к выкладке
        # не относится
        warnings = set(re.findall(r"\(([a-z_]+\.W\d+)\)", result.stderr))
        self.assertEqual(
            warnings - {"django_vite.W001"}, {"security.W021"}, result.stderr
        )


class FragmentLabelLangTests(TestCase):
    """Статья без заголовка: приставка на языке просмотра, если перевод акта
    есть."""

    @classmethod
    def setUpTestData(cls):
        source = Source.objects.create(code="test", name="Тест")
        cls.act = Act.objects.create(
            uid="eu/reg/2010/904",
            jurisdiction="eu",
            kind="reg",
            number="904/2010",
            year="2010",
            structure_lang="en",
        )
        for lang in ("en", "ru"):
            Expression.objects.create(
                act=cls.act,
                lang=lang,
                status="official_text",
                source=source,
                title="Regulation",
            )
        cls.fragment = Fragment.objects.create(
            uid="eu00000001",
            act=cls.act,
            kind="art",
            eid="art_1",
        )
        amendment = Amendment.objects.create(
            amending_act=cls.act,
            kind="base",
            key="base",
            marker="base",
        )
        cls.version = FragmentVersion.objects.create(
            fragment=cls.fragment,
            key="base",
            amendment=amendment,
            num="1",
            heading="",
            known_from=datetime.date(2024, 1, 1),
        )

    def label(self, lang):
        return reading.fragment_label(self.fragment, self.version, {}, lang)

    def test_prefix_in_reading_lang(self):
        label = self.label("ru")
        self.assertEqual((label.text, label.lang), ("Статья 1", "ru"))

    def test_original_and_missing_translation_keep_original_prefix(self):
        self.assertEqual(self.label("en").text, "Article 1")
        self.assertEqual(self.label("el").text, "Article 1")
        self.assertEqual(
            reading.fragment_label(self.fragment, self.version).text,
            "Article 1",
        )

    def test_greek_heading_of_eu_act(self):
        """У акта ЕС оригинал en: греческий — перевод, его заголовки берутся
        (раздел, глава)."""
        el = Expression.objects.create(
            act=self.act,
            lang="el",
            status="official_translation",
            source=Source.objects.get(code="test"),
            title="Κανονισμός",
        )
        FragmentText.objects.create(
            version=self.version,
            expression=el,
            heading="Αντικείμενο",
            body="<p>Κείμενο</p>",
            text_hash="el",
        )
        headings = reading.translated_headings([self.version], "el", "en")
        self.assertEqual(headings, {self.version.id: "Αντικείμενο"})
        self.assertEqual(
            reading.translated_headings([self.version], "en", "en"), {}
        )
        label = reading.fragment_label(
            self.fragment, self.version, headings, "el"
        )
        self.assertEqual(
            (label.text, label.lang), ("Άρθρο 1. Αντικείμενο", "el")
        )
        response = self.client.get(reading.build_url(self.act, lang="el"))
        self.assertContains(response, "Άρθρο 1. Αντικείμενο")
        self.assertNotContains(response, "Article 1")


class EuOriginalLangTests(TestCase):
    """У акта ЕС оригинал — английский: он рядом с переводом, на en-странице
    оригинала нет."""

    @classmethod
    def setUpTestData(cls):
        source = Source.objects.create(code="test", name="Тест")
        cls.act = Act.objects.create(
            uid="eu/dir/2006/112",
            jurisdiction="eu",
            kind="dir",
            number="2006/112",
            year="2006",
            structure_lang="en",
        )
        fragment = Fragment.objects.create(
            uid="eu00000002",
            act=cls.act,
            kind="art",
            eid="art_1",
        )
        amendment = Amendment.objects.create(
            amending_act=cls.act,
            kind="base",
            key="base",
            marker="base",
        )
        version = FragmentVersion.objects.create(
            fragment=fragment,
            key="base",
            amendment=amendment,
            num="1",
            heading="",
            known_from=datetime.date(2024, 1, 1),
        )
        bodies = {
            "en": ("official_text", "English text"),
            "el": ("official_text", "Ελληνικό κείμενο"),
            "ru": ("our_translation", "Русский текст"),
        }
        for lang, (status, body) in bodies.items():
            expression = Expression.objects.create(
                act=cls.act,
                lang=lang,
                status=status,
                source=source,
                title="Directive",
            )
            FragmentText.objects.create(
                version=version,
                expression=expression,
                body=f"<p>{body}</p>",
                text_hash=lang,
            )
        make_nav()

    def page(self, lang, **query):
        return self.client.get(
            reading.build_url(self.act, eid="art_1", lang=lang), query
        )

    def test_en_page_has_no_original(self):
        response = self.page("en", orig="1")
        self.assertContains(response, "English text")
        self.assertNotContains(response, "fragment__original")
        self.assertNotContains(response, "Ελληνικό κείμενο")
        self.assertNotContains(response, "data-original-toggle")
        self.assertNotContains(response, "draft-warning")

    def test_ru_page_shows_english_original(self):
        response = self.page("ru", orig="1")
        self.assertContains(response, "Русский текст")
        self.assertContains(response, "fragment__original")
        self.assertContains(response, "English text")
        self.assertNotContains(response, "Ελληνικό κείμενο")
        self.assertContains(response, "Скрыть английский текст")
        self.assertContains(
            response, 'data-show-text="Показать английский текст рядом"'
        )
        self.assertContains(response, str(notices.TRANSLATION_NOTICE_EU))
        self.assertNotContains(response, "греческий")

    def test_el_page_is_official_with_english_alongside(self):
        response = self.page("el", orig="1")
        self.assertContains(response, "Ελληνικό κείμενο")
        self.assertContains(response, "English text")
        self.assertContains(response, "fragment__original")
        self.assertContains(response, "data-original-toggle")
        self.assertNotContains(response, "draft-warning")

    def test_untranslated_lang_reports_english(self):
        """Без перевода на языке страницы показан английский оригинал, а не
        греческий."""
        FragmentText.objects.filter(expression__lang="ru").delete()
        response = self.page("ru")
        self.assertContains(response, "показан официальный английский текст")
        self.assertContains(response, "English text")


class EuDisclaimerTests(EuOriginalLangTests):
    """Подвал акта ЕС: Официальный журнал ЕС на языке страницы; у кипрских —
    прежний."""

    def footer(self, response):
        content = response.content.decode()
        return content[content.index("footer__disclaimer") :]

    def test_eu_act_pages(self):
        expected = {
            "ru": "Официальном журнале Европейского союза (EUR-Lex)",
            "en": "Official Journal of the European Union (EUR-Lex)",
            "el": "Επίσημη Εφημερίδα της Ευρωπαϊκής Ένωσης (EUR-Lex)",
        }
        for lang, text in expected.items():
            with self.subTest(lang=lang):
                footer = self.footer(self.page(lang))
                self.assertIn(text, footer)
                self.assertNotIn("Εφημερίδα της Δημοκρατίας", footer)
                self.assertNotIn("Республики Кипр", footer)

    def test_eu_section_and_cyprus(self):
        self.assertIn(
            "Официальном журнале", self.footer(self.client.get("/eu/"))
        )
        self.assertIn("Республики Кипр", self.footer(self.client.get("/")))
        self.assertEqual(notices.disclaimer("cy"), notices.DISCLAIMER)


PREFIX = "/resources/nomos"

# Сайт в подпути: так settings.py выводит значения из
# NOMOS_URL_PREFIX=/resources/nomos.
# Прокси передаёт запрос без подпути — тестовый клиент тоже запрашивает пути от
# корня.
prefix_settings = override_settings(
    NOMOS_URL_PREFIX=PREFIX,
    FORCE_SCRIPT_NAME=PREFIX,
    STATIC_URL=PREFIX + "/static/",
    SESSION_COOKIE_PATH=PREFIX + "/",
    CSRF_COOKIE_PATH=PREFIX + "/",
)


def under_prefix(cls):
    """Настройки подпути и префикс reverse(): тестовый клиент, в отличие от
    WSGI-сервера,
    префикс адресов из FORCE_SCRIPT_NAME сам не ставит."""
    return prefix_settings(override_script_prefix(PREFIX + "/")(cls))


ADDRESS_RE = re.compile(r'(?:href|action|src)="([^"]*)"')


HOME_URL = "https://vatelse.eu/"

HOME_LINK_RE = re.compile(
    r'href="([^"]*)"\s+data-home-link\s*>\s*([^<]*?)\s*</a>'
)


class UrlPrefixMixin:
    """Ссылки, перенаправления и cookie сайта: в корне домена и в подпути."""

    prefix = ""

    setUpTestData = classmethod(ViewerTests.setUpTestData.__func__)
    add_version = classmethod(ViewerTests.add_version.__func__)

    def site(self, path):
        return self.prefix + path

    def assert_addresses_in_site(self, page):
        """Все адреса сайта на странице начинаются с подпути; чужих путей от
        «/» нет."""
        response = self.client.get(page)
        self.assertEqual(response.status_code, 200, page)
        for address in ADDRESS_RE.findall(response.content.decode()):
            address = html.unescape(address)
            if address == "" or address.startswith(
                ("?", "#", "http://", "https://", "mailto:")
            ):
                continue
            self.assertTrue(
                address.startswith(self.site("/")), f"{page}: {address}"
            )
            self.assertFalse(address.startswith("//"), f"{page}: {address}")

    def test_page_addresses(self):
        self.assert_addresses_in_site("/login/")
        self.client.force_login(self.reader)
        pages = [
            "/",
            "/cy/",
            "/about/",
            ACT_URL,
            ACT_URL + "art_1/",
            ACT_URL + "@2025-06-01/art_1/?orig=1",
            ACT_URL + "art_1.par_1/history/",
        ]
        for page in pages:
            with self.subTest(page=page):
                self.assert_addresses_in_site(page)

    def test_menu_and_language_links(self):
        response = self.client.get(ACT_URL + "art_1/")
        self.assertContains(response, f'href="{self.site("/cy/")}')
        self.assertContains(response, f'href="{self.site("/about/")}')
        self.assertContains(
            response, f'href="{self.site(ACT_URL + "art_1.par_1/history/")}'
        )
        self.assertContains(response, "menu__link--current")
        # статика и сборка Vite (в разработке — адрес dev-сервера с тем же
        # путём)
        self.assertContains(response, f"{self.site('/static/')}js/main")

    def test_reference_leads_there_and_back(self):
        text = FragmentText.objects.get(version=self.new)
        text.body = '<p>Βλ. <ref id="r1">άρθρο 2</ref>.</p>'
        text.save()
        Reference.objects.create(
            from_text=text,
            anchor="r1",
            to_act=self.amending,
            to_fragment=self.amending.fragments.get(eid="art_2"),
            resolved=True,
        )
        page = ACT_URL + "@2025-06-01/art_1/"
        response = self.client.get(page)
        found = re.search(
            r'<a class="ref" id="[^"]*" href="([^"]*)"',
            response.content.decode(),
        )
        href = html.unescape(found.group(1))
        self.assertTrue(
            href.startswith(self.site("/cy/law/2024/2_I/@2025-06-01/art_2/?"))
        )
        # прокси снимает подпуть; страница цели ведёт назад, к месту ссылки
        response = self.client.get(href.removeprefix(self.prefix))
        self.assertContains(
            response, f'href="{self.site(page)}#ref-art_1.par_1-r1"'
        )

    def test_old_date_redirects_inside_site(self):
        response = self.client.get(ACT_URL + "art_1/", {"on": "2024-06-01"})
        self.assertTrue(
            response["Location"].startswith(
                self.site(ACT_URL + "@2024-06-01/")
            )
        )

    def test_login_logout_and_cookies(self):
        User.objects.create_user("editor", password="test-password-1")
        response = self.client.get("/login/")
        self.assertEqual(response.cookies["csrftoken"]["path"], self.site("/"))
        credentials = {"username": "editor", "password": "test-password-1"}
        target = self.site(ACT_URL + "art_1/")
        response = self.client.post("/login/", {**credentials, "next": target})
        self.assertRedirects(response, target, fetch_redirect_response=False)
        self.assertEqual(response.cookies["sessionid"]["path"], self.site("/"))
        self.assertContains(
            self.client.get("/"),
            f'action="{self.site("/logout/")}"',
        )
        response = self.client.post("/logout/")
        self.assertRedirects(
            response, self.site("/"), fetch_redirect_response=False
        )
        self.client.logout()
        response = self.client.post("/login/", credentials)
        self.assertRedirects(
            response, self.site("/"), fetch_redirect_response=False
        )

    @override_settings(NOMOS_HOME_URL=HOME_URL, NOMOS_HOME_LABEL="VATelse")
    def test_home_link(self):
        """Ссылка на внешний сайт: адрес абсолютный, подпуть к нему не
        добавляется."""
        for page in ["/", "/about/", ACT_URL]:
            with self.subTest(page=page):
                response = self.client.get(page)
                links = HOME_LINK_RE.findall(response.content.decode())
                self.assertEqual(links, [(HOME_URL, "VATelse")])

    def test_no_home_link(self):
        """Адрес не задан — ссылки нет."""
        for page in ["/", "/about/", ACT_URL]:
            with self.subTest(page=page):
                self.assertNotContains(self.client.get(page), "data-home-link")

    def test_robots(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(
            response.content.decode(),
            f"User-agent: *\nDisallow: {self.site('/')}\n",
        )


class RootUrlTests(UrlPrefixMixin, TestCase):
    """Сайт в корне домена (NOMOS_URL_PREFIX пуст)."""


@under_prefix
class PrefixUrlTests(UrlPrefixMixin, TestCase):
    """Сайт в подпути /resources/nomos."""

    prefix = PREFIX

    def test_return_address_outside_site_is_ignored(self):
        response = self.client.get(
            "/cy/law/2024/2_I/art_2/", {"from": ACT_URL + "art_1/"}
        )
        self.assertNotContains(response, "back-ref")


IFRS_URL = "/ifrs/ias/16/"
CLOSED_URL = "/ifrs/amend/2025/1/"
CYPRUS_URL = "/cy/law/2001/5_I/"

# тексты непубличных выражений МСФО, которых не должно быть в HTML
# гостя и обычного вошедшего
CLOSED_TEXTS = (
    "IFRS 16 Leases",
    "Lessee remeasures",
    "Lease Amendments",
    "Secret amending text",
)


class IfrsAccessMixin:
    """NOMOS_PUBLIC_ONLY: непубличные выражения актов МСФО видит только
    суперпользователь."""

    prefix = ""

    def site(self, path):
        return self.prefix + path

    @classmethod
    def setUpTestData(cls):
        source = Source.objects.create(code="test", name="Тест")
        cls.ifrs = Act.objects.create(
            uid="ifrs/ias/16",
            jurisdiction="ifrs",
            kind="std",
            number="16",
            structure_lang="en",
        )
        cls.closed = Act.objects.create(
            uid="ifrs/amend/2025/1",
            jurisdiction="ifrs",
            kind="std",
            number="1",
            year="2025",
            structure_lang="en",
        )
        cyprus = Act.objects.create(
            uid="cy/law/2001/5_I",
            jurisdiction="cy",
            kind="law",
            number="5(I)",
            year="2001",
            structure_lang="el",
        )
        ActRelation.objects.create(
            from_act=cls.closed, to_act=cls.ifrs, kind="amends"
        )
        en = cls.expression(cls.ifrs, "en", source, "IFRS 16 Leases", False)
        ru = cls.expression(cls.ifrs, "ru", source, "МСФО 16 Аренда", True)
        closed_en = cls.expression(
            cls.closed, "en", source, "Lease Amendments", False
        )
        cyprus_el = cls.expression(
            cyprus, "el", source, "Κυπριακός Νόμος", False
        )
        closed_article = cls.article(cls.closed, "cl00000001")
        cls.version(
            closed_article,
            cls.base(cls.closed),
            {closed_en: "Secret amending text"},
        )
        cls.version(
            cls.article(cyprus, "cn00000001"),
            cls.base(cyprus),
            {cyprus_el: "Κυπριακό κείμενο"},
        )
        article = cls.article(cls.ifrs, "if00000001")
        cls.version(
            article,
            cls.base(cls.ifrs),
            {en: "Lessee recognises", ru: "Арендатор признаёт"},
        )
        replace = Amendment.objects.create(
            amending_act=cls.closed,
            amending_fragment=closed_article,
            kind="replace",
            key="art_1:replace",
            marker="art_1:replace",
            published_on=datetime.date(2024, 12, 1),
            in_force_from=datetime.date(2025, 1, 1),
        )
        anchor = ', см. <ref id="r1">поправку</ref>'
        texts = cls.version(
            article,
            replace,
            {
                en: "Lessee remeasures" + anchor,
                ru: "Арендатор переоценивает" + anchor,
            },
            key="2025-01-01",
            valid_from=datetime.date(2025, 1, 1),
        )
        for text in texts:
            Reference.objects.create(
                from_text=text,
                anchor="r1",
                to_act=cls.closed,
                to_fragment=closed_article,
                resolved=True,
            )
        make_nav()
        section = NavNode.objects.get(key="ifrs")
        for order, act in enumerate([cls.ifrs, cls.closed, cyprus]):
            NavNode.objects.create(
                parent=section,
                label=f"Пункт {order}",
                order=order,
                act_uid=act.uid,
                act=act,
            )
        cls.reader = User.objects.create_user("reader")
        cls.admin = User.objects.create_superuser("admin")

    @staticmethod
    def expression(act, lang, source, title, public):
        return Expression.objects.create(
            act=act,
            lang=lang,
            status="official_text",
            source=source,
            title=title,
            public=public,
        )

    @staticmethod
    def base(act):
        return Amendment.objects.create(
            amending_act=act, kind="base", key="base", marker="base"
        )

    @staticmethod
    def article(act, uid):
        return Fragment.objects.create(
            uid=uid, act=act, kind="art", eid="art_1", order_key="0001"
        )

    @staticmethod
    def version(fragment, amendment, bodies, key="base", **fields):
        version = FragmentVersion.objects.create(
            fragment=fragment,
            key=key,
            amendment=amendment,
            num="1",
            known_from=datetime.date(2024, 1, 1),
            **fields,
        )
        return [
            FragmentText.objects.create(
                version=version,
                expression=expression,
                body=f"<p>{body}</p>",
                text_hash=key,
            )
            for expression, body in bodies.items()
        ]

    def assert_ifrs_closed(self):
        """Раздел МСФО закрыт: адреса /ifrs/... — 404, слов МСФО в HTML
        нет."""
        for page in [
            "/ifrs/",
            "/ifrs/x/",
            IFRS_URL,
            IFRS_URL + "art_1/",
            IFRS_URL + "art_1/history/",
            IFRS_URL + "en/",
            CLOSED_URL,
            CLOSED_URL + "art_1/",
        ]:
            with self.subTest(page=page):
                self.assertEqual(self.client.get(page).status_code, 404)
        for page in ["/", "/about/", "/cy/", "/robots.txt"]:
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                for word in ["МСФО", "IFRS", "IAS", "/ifrs/"]:
                    self.assertNotIn(word, html)
                self.assertNotContains(response, self.site(CLOSED_URL))

    def test_guest(self):
        self.assert_ifrs_closed()
        self.assertEqual(self.client.get(CYPRUS_URL).status_code, 404)

    def test_signed_in_sees_no_ifrs(self):
        self.client.force_login(self.reader)
        self.assert_ifrs_closed()
        response = self.client.get(CYPRUS_URL + "art_1/")
        self.assertContains(response, "Κυπριακό κείμενο")

    def test_superuser_sees_everything(self):
        self.client.force_login(self.admin)
        closed_link = f'href="{self.site(CLOSED_URL)}'
        for page in ["/", "/ifrs/"]:
            with self.subTest(page=page):
                self.assertContains(self.client.get(page), closed_link)
        response = self.client.get(IFRS_URL + "art_1/")
        self.assertContains(response, "Lessee remeasures")
        self.assertRegex(
            response.content.decode(),
            r'<a class="ref" id="[^"]*" href="'
            + re.escape(self.site(CLOSED_URL)),
        )
        response = self.client.get(IFRS_URL + "art_1/history/")
        self.assertContains(response, closed_link)
        response = self.client.get(CLOSED_URL + "art_1/")
        self.assertContains(response, "Secret amending text")
        response = self.client.get(CYPRUS_URL + "art_1/")
        self.assertContains(response, "Κυπριακό κείμενο")
        menu = self.client.get("/")
        self.assertContains(menu, "МСФО")

    def test_setting_off_still_closes_ifrs(self):
        with self.settings(NOMOS_PUBLIC_ONLY=False):
            response = self.client.get(CLOSED_URL + "art_1/")
            self.assertEqual(response.status_code, 404)
            self.assertNotContains(self.client.get("/"), "МСФО")
            self.client.force_login(self.admin)
            response = self.client.get(CLOSED_URL + "art_1/")
        self.assertContains(response, "Secret amending text")


@override_settings(
    DJANGO_VITE={"default": {"dev_mode": True}}, NOMOS_PUBLIC_ONLY=True
)
class IfrsAccessTests(IfrsAccessMixin, TestCase):
    """Сайт в корне домена."""


@under_prefix
@override_settings(
    DJANGO_VITE={"default": {"dev_mode": True}}, NOMOS_PUBLIC_ONLY=True
)
class IfrsAccessPrefixTests(IfrsAccessMixin, TestCase):
    """Сайт в подпути /resources/nomos."""

    prefix = PREFIX


def run_python(code, env):
    """Код в отдельном процессе Python из src/, с переменными env поверх
    окружения и без
    DJANGO_SETTINGS_MODULE: настройки читаются заново."""
    env = dict(os.environ, **env)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
    )


class UrlPrefixSettingsTests(TestCase):
    """settings.py: подпуть из .env без лишних слешей; пусто — корень."""

    def settings_for(self, value):
        code = (
            "from nomos import settings as s; "
            "print(repr(s.NOMOS_URL_PREFIX), s.FORCE_SCRIPT_NAME, "
            "s.STATIC_URL, "
            "s.SESSION_COOKIE_PATH, s.CSRF_COOKIE_PATH)"
        )
        result = run_python(code, {"NOMOS_URL_PREFIX": value})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def test_prefix(self):
        expected = [
            repr(PREFIX),
            PREFIX,
            PREFIX + "/static/",
            PREFIX + "/",
            PREFIX + "/",
        ]
        self.assertEqual(self.settings_for("/resources/nomos/"), expected)
        self.assertEqual(self.settings_for("resources/nomos"), expected)

    def test_root(self):
        self.assertEqual(
            self.settings_for(""), ["''", "None", "/static/", "/", "/"]
        )


class HomeLinkSettingsTests(TestCase):
    """settings.py: ссылка на внешний сайт из .env; подпись по умолчанию —
    адрес."""

    def settings_for(self, url, label):
        code = (
            "from nomos import settings as s; "
            "print(repr((s.NOMOS_HOME_URL, s.NOMOS_HOME_LABEL)))"
        )
        result = run_python(
            code, {"NOMOS_HOME_URL": url, "NOMOS_HOME_LABEL": label}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_values(self):
        self.assertEqual(
            self.settings_for(" https://vatelse.eu/ ", "VATelse"),
            repr(("https://vatelse.eu/", "VATelse")),
        )
        self.assertEqual(
            self.settings_for("https://vatelse.eu/", ""),
            repr(("https://vatelse.eu/", "https://vatelse.eu/")),
        )
        self.assertEqual(self.settings_for("", ""), repr(("", "")))
