import datetime

from django.db import models


class Fragment(models.Model):
    class Kind(models.TextChoices):
        PART = 'prt', 'Часть'
        TITLE = 'tis', 'Раздел'
        CHAPTER = 'cpt', 'Глава'
        SECTION = 'sct', 'Секция'
        SUBSECTION = 'sbs', 'Подсекция'
        ARTICLE = 'art', 'Статья'
        PARAGRAPH = 'par', 'Пункт'
        SUBPARAGRAPH = 'sub', 'Подпункт / абзац'
        POINT = 'pnt', 'Пункт (point)'
        INDENT = 'ind', 'Отступ'
        ANNEX = 'anx', 'Приложение'
        TABLE = 'tbl', 'Таблица'
        DEFINITION = 'def', 'Определение'
        RECITAL = 'rct', 'Соображение'
        CITATION = 'cit', 'Преамбула'
        FOOTNOTE = 'fnt', 'Сноска акта'
        SCHEDULE = 'sch', 'Παράρτημα'
        FIELD = 'fld', 'Поле формы'

    class Axis(models.TextChoices):
        APPLIES = 'applies', 'Применяется'
        VALID = 'valid', 'Вступило в силу'

    uid = models.CharField(max_length=10, unique=True)
    act = models.ForeignKey('Act', on_delete=models.CASCADE, related_name='fragments')
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='children',
    )
    kind = models.CharField(max_length=5, choices=Kind)
    eid = models.CharField(max_length=200)
    order_key = models.CharField(max_length=100, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['act', 'eid']),
        ]

    def version_on(self, date, axis=Axis.APPLIES, known=None):
        """Редакция на дату `date` по оси `axis` при знании на дату `known`.

        Правило — раздел 4 docs/design/data-model.md. Возвращает None, если
        подходящей редакции нет или она исключена.
        """
        known = known or datetime.date.today()
        best = None
        best_key = None
        for version in self.versions.filter(known_from__lte=known):
            start = version.start(axis)
            if start is not None and start > date:
                continue
            # null — до 01.01.2024, не прослеживается: раньше любой даты
            key = (start or datetime.date.min, version.known_from)
            if best_key is None or key > best_key:
                best, best_key = version, key
        if best is None or best.state == best.State.REPEALED:
            return None
        return best

    def future_version(self, axis=Axis.APPLIES, today=None):
        """Принятая редакция, начало которой после сегодняшнего дня (самая ранняя)."""
        today = today or datetime.date.today()
        future = [
            version
            for version in self.versions.filter(known_from__lte=today)
            if version.start(axis) is not None and version.start(axis) > today
        ]
        if not future:
            return None
        return min(future, key=lambda version: version.start(axis))

    def __str__(self):
        return f'{self.act}#{self.eid}'
