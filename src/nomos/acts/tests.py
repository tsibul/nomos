from datetime import date

from django.test import TestCase

from .models import Act, Amendment, Fragment, FragmentVersion


class VersionOnTests(TestCase):
    def make_act(self, uid, jurisdiction, kind):
        return Act.objects.create(
            uid=uid, jurisdiction=jurisdiction, kind=kind
        )

    def make_fragment(self, act, uid, eid, kind, parent=None):
        return Fragment.objects.create(
            uid=uid, act=act, kind=kind, eid=eid, parent=parent
        )

    def make_version(self, fragment, key, amendment, **fields):
        return FragmentVersion.objects.create(
            fragment=fragment,
            key=key,
            amendment=amendment,
            **fields,
        )

    def test_directive_2006_112_article_98(self):
        base = self.make_act("eu/dir/2006/112", "eu", "dir")
        amending = self.make_act("eu/dir/2022/542", "eu", "dir")
        replace = Amendment.objects.create(
            amending_act=amending,
            kind="replace",
            key="art_1:replace",
            marker="M32",
            published_on=date(2022, 4, 6),
            in_force_from=date(2022, 4, 6),
        )
        insert = Amendment.objects.create(
            amending_act=amending,
            kind="insert",
            key="art_1:insert",
            published_on=date(2022, 4, 6),
            in_force_from=date(2022, 4, 6),
            applies_from=date(2025, 1, 1),
        )
        delete = Amendment.objects.create(
            amending_act=amending,
            kind="delete",
            key="art_1:delete",
            published_on=date(2022, 4, 6),
            in_force_from=date(2022, 4, 6),
        )
        art_98 = self.make_fragment(base, "k3v9q2m7xa", "art_98", "art")
        art_98a = self.make_fragment(base, "k3v9q2m7xb", "art_98a", "art")
        art_99 = self.make_fragment(base, "k3v9q2m7xc", "art_99", "art")
        old_98 = self.make_version(
            art_98,
            "2020-01-01",
            replace,
            known_from=date(2020, 1, 1),
            valid_from=None,
        )
        new_98 = self.make_version(
            art_98,
            "2022-04-06",
            replace,
            known_from=date(2022, 4, 6),
            valid_from=date(2022, 4, 6),
        )
        v_98a = self.make_version(
            art_98a,
            "2022-04-06",
            insert,
            known_from=date(2022, 4, 6),
            valid_from=date(2022, 4, 6),
            applies_from=date(2025, 1, 1),
        )
        self.make_version(
            art_99, "2020-01-01", replace, known_from=date(2020, 1, 1)
        )
        self.make_version(
            art_99,
            "2022-04-06",
            delete,
            known_from=date(2022, 4, 6),
            valid_from=date(2022, 4, 6),
            state="repealed",
        )
        day = date(2024, 1, 1)
        today = date(2024, 6, 1)

        self.assertEqual(art_98.version_on(day, known=today), new_98)
        self.assertEqual(
            art_98.version_on(date(2022, 4, 5), known=today), old_98
        )
        # ось «применяется»: ст. 98a на 01.01.2024 нет, ст. 99 исключена
        self.assertIsNone(art_98a.version_on(day, known=today))
        self.assertIsNone(art_99.version_on(day, known=today))
        # ось «вступило в силу»: ст. 98a уже есть
        self.assertEqual(
            art_98a.version_on(day, axis="valid", known=today), v_98a
        )
        # будущая принятая редакция
        self.assertEqual(art_98a.future_version(today=today), v_98a)
        self.assertIsNone(art_98a.future_version(today=date(2025, 6, 1)))
        self.assertIsNone(art_98.future_version(today=today))

    def test_law_95_2000_article_45_paragraph_7g(self):
        law = self.make_act("cy/law/2000/95_I", "cy", "law")
        amending = self.make_act("cy/law/2024/10_I", "cy", "law")
        insert = Amendment.objects.create(
            amending_act=amending,
            kind="insert",
            key="art_1:insert",
            published_on=date(2024, 2, 23),
            in_force_from=date(2024, 2, 23),
        )
        base = Amendment.objects.create(
            amending_act=law, kind="base", key="base"
        )
        art_45 = self.make_fragment(law, "p2n4t8w1aa", "art_45", "art")
        par_7g = self.make_fragment(
            law, "p2n4t8w1ab", "art_45.par_7G", "par", parent=art_45
        )
        v_art = self.make_version(
            art_45, "base", base, known_from=date(2024, 1, 1)
        )
        v_7g = self.make_version(
            par_7g,
            "2024-02-23",
            insert,
            known_from=date(2024, 2, 23),
            valid_from=date(2024, 2, 23),
        )
        today = date(2024, 6, 1)

        # новой редакции ст. 45 нет: у базовой valid_from не прослеживается
        # (null)
        self.assertEqual(
            art_45.version_on(date(2024, 1, 1), known=today), v_art
        )
        self.assertEqual(
            art_45.version_on(date(2024, 3, 1), known=today), v_art
        )
        self.assertIsNone(par_7g.version_on(date(2024, 1, 1), known=today))
        self.assertEqual(
            par_7g.version_on(date(2024, 2, 23), known=today), v_7g
        )
        # «как было известно» до публикации — подпункта ещё нет
        self.assertIsNone(
            par_7g.version_on(date(2024, 3, 1), known=date(2024, 2, 22))
        )

    def test_correction_wins_by_known_from(self):
        act = self.make_act("eu/dir/2006/112", "eu", "dir")
        amend = Amendment.objects.create(
            amending_act=act,
            kind="replace",
            key="art_1:replace",
        )
        fix = Amendment.objects.create(
            amending_act=act,
            kind="correct",
            key="art_1:correct",
        )
        fragment = self.make_fragment(act, "aaaaaaaaaa", "art_1", "art")
        self.make_version(
            fragment,
            "2022-04-06",
            amend,
            valid_from=date(2022, 4, 6),
            known_from=date(2022, 4, 6),
        )
        corrected = self.make_version(
            fragment,
            "2022-04-06~2",
            fix,
            valid_from=date(2022, 4, 6),
            known_from=date(2022, 7, 1),
        )
        self.assertEqual(
            fragment.version_on(date(2024, 1, 1), known=date(2024, 6, 1)),
            corrected,
        )
