from django.db import models


class Amendment(models.Model):
    class Kind(models.TextChoices):
        REPLACE = 'replace', 'Заменить'
        INSERT = 'insert', 'Вставить'
        DELETE = 'delete', 'Исключить'
        RENUMBER = 'renumber', 'Перенумеровать'
        CORRECT = 'correct', 'Исправить (corrigendum)'
        BASE = 'base', 'Базовый текст'

    amending_act = models.ForeignKey('Act', on_delete=models.CASCADE, related_name='amendments')
    amending_fragment = models.ForeignKey(
        'Fragment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='amendments_made',
    )
    kind = models.CharField(max_length=20, choices=Kind)
    published_on = models.DateField(null=True, blank=True)
    in_force_from = models.DateField(null=True, blank=True)
    applies_from = models.DateField(null=True, blank=True)
    # ключ изменения внутри изменяющего акта: «<положение>:<вид>[@<дата>]», «base»
    key = models.CharField(max_length=60)
    # метка источника (EUR-Lex M32, сноска CyLaw)
    marker = models.CharField(max_length=50, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['amending_act', 'key'],
                name='amendment_act_key_unique',
            ),
        ]

    @property
    def retroactive(self):
        """Вычисляется: вступило в силу раньше публикации."""
        if self.in_force_from is None or self.published_on is None:
            return False
        return self.in_force_from < self.published_on

    def __str__(self):
        return f'{self.amending_act} {self.kind} {self.key}'.strip()
