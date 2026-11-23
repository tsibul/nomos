from django.db import models


class ActRelation(models.Model):
    class Kind(models.TextChoices):
        AMENDS = 'amends', 'Изменяет'
        TRANSPOSES = 'transposes', 'Вводит/транспонирует'
        BASED_ON = 'based_on', 'Издан на основании'
        REPEALS = 'repeals', 'Отменяет'
        CORRECTS = 'corrects', 'Исправляет (corrigendum)'
        EXPLAINS = 'explains', 'Разъясняет'
        # из кипрского закона к связанному акту: ЕС, МСФО и др.
        RELATED = 'related', 'Связанный акт'

    from_act = models.ForeignKey('Act', on_delete=models.CASCADE, related_name='relations_out')
    to_act = models.ForeignKey('Act', on_delete=models.CASCADE, related_name='relations_in')
    kind = models.CharField(max_length=20, choices=Kind)
    from_fragment = models.ForeignKey(
        'Fragment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='relations_out',
    )
    to_fragment = models.ForeignKey(
        'Fragment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='relations_in',
    )

    def __str__(self):
        return f'{self.from_act} {self.kind} {self.to_act}'
