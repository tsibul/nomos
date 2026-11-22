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
        CELL = 'cel', 'Ячейка таблицы'
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
        chain = self.versions_until(date, axis, known)
        if not chain or chain[-1].state == chain[-1].State.REPEALED:
            return None
        return chain[-1]

    def repeal_on(self, date, axis=Axis.APPLIES, known=None):
        """Исключение на дату: (исключающая редакция, последняя действовавшая или None).

        Если на дату фрагмент не исключён (действует или ещё не введён) — None.
        """
        chain = self.versions_until(date, axis, known)
        if not chain or chain[-1].state != chain[-1].State.REPEALED:
            return None
        earlier = [
            version for version in chain[:-1] if version.state != version.State.REPEALED
        ]
        return chain[-1], earlier[-1] if earlier else None

    def versions_until(self, date, axis=Axis.APPLIES, known=None):
        """Редакции, начавшиеся к дате `date`, по порядку; последняя — действующая на дату."""
        known = known or datetime.date.today()
        chain = []
        for position, version in enumerate(self.versions.filter(known_from__lte=known)):
            start = version.start(axis)
            if start is not None and start > date:
                continue
            # null — до 01.01.2024, не прослеживается: раньше любой даты;
            # при равных датах действует первая по порядку загрузки — она встаёт последней
            key = (start or datetime.date.min, version.known_from, -position)
            chain.append((key, version))
        chain.sort(key=lambda pair: pair[0])
        return [version for _key, version in chain]

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
