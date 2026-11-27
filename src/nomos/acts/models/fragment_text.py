from django.db import models


class FragmentText(models.Model):
    class Alignment(models.TextChoices):
        EXACT = "exact", "Точно"
        AT_ANCESTOR = "at_ancestor", "На предке"
        PARTIAL = "partial", "Неполно"

    class Review(models.TextChoices):
        DRAFT = "draft", "Черновик"
        SECOND_PASS = "second_pass", "Проверен вторым проходом"
        HUMAN = "human", "Проверен человеком"

    version = models.ForeignKey(
        "FragmentVersion", on_delete=models.CASCADE, related_name="texts"
    )
    expression = models.ForeignKey(
        "Expression", on_delete=models.CASCADE, related_name="texts"
    )
    body = models.TextField()
    # заголовок на языке текста, если он не опорный (на опорном —
    # FragmentVersion.heading)
    heading = models.CharField(max_length=500, blank=True)
    text_hash = models.CharField(max_length=64, db_index=True)
    alignment = models.CharField(
        max_length=15, choices=Alignment, default=Alignment.EXACT
    )
    translated_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="translations",
    )
    translated_from_hash = models.CharField(max_length=64, blank=True)
    translator = models.CharField(max_length=255, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)
    review = models.CharField(max_length=15, choices=Review, blank=True)

    @property
    def stale(self):
        """Вычисляется: хеш исходника при переводе ≠ текущий хеш исходника."""
        if self.translated_from is None:
            return False
        return self.translated_from_hash != self.translated_from.text_hash

    def __str__(self):
        return f"{self.version}:{self.expression.lang}"
