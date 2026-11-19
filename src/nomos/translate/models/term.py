from django.db import models


class Term(models.Model):
    class Scope(models.TextChoices):
        GENERAL = 'general', 'Общий'
        JURISDICTION = 'jurisdiction', 'Юрисдикция'
        ACT = 'act', 'Акт'

    class Status(models.TextChoices):
        APPROVED = 'approved', 'Утверждён'
        PROPOSED = 'proposed', 'Предложен'
        FORBIDDEN = 'forbidden', 'Запрещён (ложный друг)'

    src_lang = models.CharField(max_length=10)
    src = models.CharField(max_length=500)
    tgt_lang = models.CharField(max_length=10)
    tgt = models.CharField(max_length=500)
    scope = models.CharField(max_length=15, choices=Scope, default=Scope.GENERAL)
    status = models.CharField(max_length=10, choices=Status, default=Status.PROPOSED)
    basis = models.ForeignKey(
        'acts.FragmentText',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='terms',
    )
    comment = models.TextField(blank=True)

    def __str__(self):
        return f'{self.src} → {self.tgt}'
