from django.db import models


class Act(models.Model):
    class Jurisdiction(models.TextChoices):
        EU = 'eu', 'EU'
        CY = 'cy', 'Cyprus'
        IFRS = 'ifrs', 'IFRS'
        US = 'us', 'US'

    class Kind(models.TextChoices):
        LAW = 'law', 'Закон'
        REGULATION_BY_LAW = 'kdp', 'Подзаконный акт (ΚΔΠ)'
        DIRECTIVE = 'dir', 'Директива'
        REGULATION = 'reg', 'Регламент'
        IMPLEMENTING_REGULATION = 'reg_impl', 'Имплементационный регламент'
        STANDARD = 'std', 'Стандарт'
        INSTRUCTION = 'instr', 'Инструкция'
        CIRCULAR = 'circ', 'Циркуляр'
        DECISION = 'dec', 'Решение'

    uid = models.CharField(max_length=100, unique=True)
    jurisdiction = models.CharField(max_length=10, choices=Jurisdiction)
    kind = models.CharField(max_length=20, choices=Kind)
    number = models.CharField(max_length=50, blank=True)
    year = models.CharField(max_length=10, blank=True)
    date_adopted = models.DateField(null=True, blank=True)
    published_on = models.DateField(null=True, blank=True)
    published_in = models.CharField(max_length=255, blank=True)
    in_force_from = models.DateField(null=True, blank=True)
    in_force_rule = models.TextField(blank=True)
    repealed_on = models.DateField(null=True, blank=True)
    eli = models.CharField(max_length=255, blank=True)
    celex = models.CharField(max_length=50, blank=True)
    part_of = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='parts',
    )
    structure_lang = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return self.uid
