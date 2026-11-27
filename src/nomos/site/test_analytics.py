"""Аналитика: страницы «Что меняется» и сравнение «было — стало»."""

import datetime

from django.test import TestCase, override_settings

from nomos.acts.models import (
    Act,
    Amendment,
    Expression,
    Fragment,
    FragmentText,
    NavNode,
)
from nomos.site import analytics
from nomos.site import tests as site_tests

ON = datetime.date(2025, 1, 1)
PAGE_URL = "/analytics/changes/2025-01-01/"


@override_settings(DJANGO_VITE={"default": {"dev_mode": True}})
class AnalyticsTests(TestCase):
    """Данные ViewerTests: п. (1) ст. 1 заменён с 01.01.2025; плюс
    вставленная с той же даты ст. 2 с пунктом."""

    setUpTestData = site_tests.ViewerTests.__dict__["setUpTestData"]
    add_version = site_tests.ViewerTests.__dict__["add_version"]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        analytics_node = NavNode.objects.get(key="analytics")
        for order, year in enumerate((2025, 2026), start=1):
            NavNode.objects.create(
                parent=analytics_node,
                order=order,
                changes_on=datetime.date(year, 1, 1),
                scope="cy/taxes",
            )
        insert = Amendment.objects.create(
            amending_act=cls.amending,
            kind="insert",
            key="art_3:insert",
            marker="art_3:insert",
            published_on=datetime.date(2024, 12, 1),
            in_force_from=ON,
        )
        part = Fragment.objects.get(uid="lw00000001")
        cls.inserted = Fragment.objects.create(
            uid="lw00000010",
            act=cls.law,
            kind="art",
            eid="art_2",
            parent=part,
            order_key="0002",
        )
        cls.add_version(
            cls.inserted,
            "2025-01-01",
            insert,
            "2",
            cls.el,
            "",
            heading="Νέο άρθρο",
            valid_from=ON,
        )
        child = Fragment.objects.create(
            uid="lw00000011",
            act=cls.law,
            kind="par",
            eid="art_2.par_1",
            parent=cls.inserted,
            order_key="0001",
        )
        cls.add_version(
            child,
            "2025-01-01",
            insert,
            "(1)",
            cls.el,
            "Προστιθέμενο κείμενο",
            valid_from=ON,
        )

    def entries(self, response):
        groups = response.context["groups"]
        return [
            entry
            for group in groups
            for part in group["parts"]
            for entry in part["entries"]
        ]

    def test_menu_marks_analytics_active(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="/analytics/"')

    def test_home_has_no_changes_cards(self):
        response = self.client.get("/")
        self.assertContains(response, "acts__card--missing", count=1)
        self.assertNotContains(
            response, 'acts__heading-link"\n              href="/analytics/'
        )

    def test_index_lists_pages(self):
        response = self.client.get("/analytics/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{PAGE_URL}"')
        self.assertContains(response, 'href="/analytics/changes/2026-01-01/"')

    def test_changes_groups_by_law(self):
        response = self.client.get(PAGE_URL)
        self.assertEqual(response.status_code, 200)
        groups = response.context["groups"]
        self.assertEqual([group["label"] for group in groups], ["НДС"])
        parts = groups[0]["parts"]
        self.assertEqual([part["main"] for part in parts], [True])
        self.assertFalse(response.context["future"])

    def test_changes_kinds_and_sources(self):
        response = self.client.get(PAGE_URL)
        entries = self.entries(response)
        urls = [entry["url"] for entry in entries]
        self.assertEqual(
            urls,
            [
                PAGE_URL + "lw00000003/",
                PAGE_URL + "lw00000010/",
            ],
        )
        kinds = [(entry["kind"], entry["modifier"]) for entry in entries]
        self.assertEqual(
            kinds, [("заменён", "replace"), ("вставлен", "insert")]
        )
        self.assertContains(response, "changes__item--insert")
        self.assertContains(response, "Άρθρο 1. Ερμηνεία, (1)")

    def test_nested_changes_are_folded(self):
        entries = self.entries(self.client.get(PAGE_URL))
        self.assertEqual([entry["nested"] for entry in entries], [0, 1])
        self.assertNotIn(
            PAGE_URL + "lw00000011/", [entry["url"] for entry in entries]
        )

    def test_base_text_is_not_a_change(self):
        other = Act.objects.create(
            uid="cy/law/2025/9_I", jurisdiction="cy", kind="law"
        )
        NavNode.objects.create(
            parent=NavNode.objects.get(key="taxes"),
            order=90,
            act_uid=other.uid,
            act=other,
        )
        base = Amendment.objects.create(
            amending_act=other, kind="base", key="base", marker="base"
        )
        fragment = Fragment.objects.create(
            uid="nw00000001", act=other, kind="art", eid="art_1"
        )
        self.add_version(fragment, "base", base, "1", None, "", valid_from=ON)
        response = self.client.get(PAGE_URL)
        self.assertEqual(len(response.context["groups"]), 1)

    def test_empty_date(self):
        response = self.client.get("/analytics/changes/2026-01-01/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "На эту дату изменений нет.")

    def test_unknown_dates(self):
        for url in (
            "/analytics/changes/2027-01-01/",
            "/analytics/changes/2025-13-01/",
            "/analytics/changes/xx/",
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_compare_old_and_new(self):
        response = self.client.get(PAGE_URL + "lw00000003/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["lang"], "el")
        self.assertContains(response, 'class="compare__removed">Παλαιό<')
        self.assertContains(response, 'class="compare__added">Νέο<')
        self.assertEqual(response.context["langs"][0]["code"], "el")
        self.assertEqual(len(response.context["langs"]), 1)
        self.assertTrue(response.context["old_url"])

    def test_compare_inserted_has_empty_old_side(self):
        response = self.client.get(PAGE_URL + "lw00000010/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["old"], [])
        self.assertEqual(response.context["old_url"], "")
        self.assertContains(response, "Текста нет.")
        self.assertContains(response, "Προστιθέμενο")

    def test_compare_offers_russian_when_both_sides_translated(self):
        ru = Expression.objects.create(
            act=self.law,
            lang="ru",
            status="our_translation",
            source=self.el.source,
            title="Закон",
        )
        for version, body in ((self.old, "Старый"), (self.new, "Новый")):
            FragmentText.objects.create(
                version=version,
                expression=ru,
                body=f"<p>{body} текст</p>",
                text_hash="ru" + version.key,
            )
        response = self.client.get(PAGE_URL + "lw00000003/")
        codes = [item["code"] for item in response.context["langs"]]
        self.assertEqual(codes, ["el", "ru"])
        response = self.client.get(PAGE_URL + "lw00000003/ru/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="compare__added">Новый<')

    def test_compare_russian_missing(self):
        url = PAGE_URL + "lw00000003/ru/"
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_compare_not_found(self):
        for uid in ("xx00000000", "am00000001", "lw00000002"):
            url = PAGE_URL + uid + "/"
            self.assertEqual(self.client.get(url).status_code, 404, uid)

    def test_urls(self):
        self.assertEqual(analytics.changes_url(ON), PAGE_URL)
        self.assertEqual(
            analytics.compare_url(ON, self.inserted, "ru"),
            PAGE_URL + "lw00000010/ru/",
        )
