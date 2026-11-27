"""Полнота русского перевода актов по XML Nomos-texts, без БД.

Правило полноты — nomos.sources.translation_check. Неполные акты выводятся
списком;
если они есть, команда завершается с кодом 1.
Акты без ru не ошибка: они только считаются.
"""

from django.core.management.base import BaseCommand, CommandError

from nomos.sources.cylaw import texts_xml
from nomos.sources.translation_check import missing

from .load_texts import add_texts_dir, texts_root


class Command(BaseCommand):
    help = "Список актов с неполным русским переводом (по XML Nomos-texts)"

    requires_system_checks = []

    def add_arguments(self, parser):
        add_texts_dir(parser)

    def handle(self, texts_dir, **options):
        root = texts_root(texts_dir)
        complete = 0
        incomplete = 0
        without = 0
        for path in texts_xml.act_paths(root):
            element = texts_xml.read_act(path)
            reasons = missing(element, path.parent)
            if reasons is None:
                without += 1
            elif reasons:
                incomplete += 1
                self.stdout.write(
                    "%s: %s" % (element.get("uid"), "; ".join(reasons))
                )
            else:
                complete += 1
        self.stdout.write(
            "ru полный: %d, неполный: %d, без ru: %d"
            % (complete, incomplete, without)
        )
        if incomplete:
            raise CommandError("неполный русский перевод: %d" % incomplete)
