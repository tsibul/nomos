from django.db import models


class TMSegment(models.Model):
    class Status(models.TextChoices):
        MACHINE = 'machine', 'Машинный'
        HUMAN = 'human', 'Проверен человеком'
        OFFICIAL = 'official', 'Официальный'

    src_lang = models.CharField(max_length=10)
    tgt_lang = models.CharField(max_length=10)
    src_hash = models.CharField(max_length=64)
    src = models.TextField()
    tgt = models.TextField()
    origin = models.ForeignKey(
        'acts.FragmentText',
        on_delete=models.CASCADE,
        related_name='tm_segments',
    )
    status = models.CharField(max_length=10, choices=Status, default=Status.MACHINE)

    class Meta:
        indexes = [
            models.Index(fields=['src_hash']),
        ]

    def __str__(self):
        return f'{self.src_lang}→{self.tgt_lang} {self.src_hash[:8]}'
