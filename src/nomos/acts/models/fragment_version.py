from django.db import models


class FragmentVersion(models.Model):
    class State(models.TextChoices):
        ACTIVE = 'active', 'Действует'
        REPEALED = 'repealed', 'Исключён'

    fragment = models.ForeignKey('Fragment', on_delete=models.CASCADE, related_name='versions')
    key = models.CharField(max_length=30)
    amendment = models.ForeignKey(
        'Amendment',
        on_delete=models.PROTECT,
        related_name='versions',
    )
    num = models.CharField(max_length=50, blank=True)
    heading = models.CharField(max_length=500, blank=True)
    state = models.CharField(max_length=10, choices=State, default=State.ACTIVE)
    valid_from = models.DateField(null=True, blank=True)
    applies_from = models.DateField(null=True, blank=True)
    known_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['fragment', 'key'], name='version_fragment_key_uniq'),
        ]
        indexes = [
            models.Index(fields=['fragment', 'valid_from']),
        ]

    def start(self, axis):
        """Начало редакции по оси: применение (если не задано — вступление в силу) или вступление."""
        if axis == 'valid':
            return self.valid_from
        return self.applies_from or self.valid_from

    def __str__(self):
        return f'{self.fragment}@{self.key}'
