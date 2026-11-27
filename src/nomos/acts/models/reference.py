from django.db import models


class Reference(models.Model):
    class Mode(models.TextChoices):
        CURRENT = "current", "Действующая на дату чтения"
        AT_DATE = "at_date", "На дату"
        VERSION = "version", "Конкретная редакция"

    from_text = models.ForeignKey(
        "FragmentText",
        on_delete=models.CASCADE,
        related_name="references",
    )
    anchor = models.CharField(max_length=50)
    to_act = models.ForeignKey(
        "Act",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="references_in",
    )
    to_fragment = models.ForeignKey(
        "Fragment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="references_in",
    )
    mode = models.CharField(max_length=10, choices=Mode, default=Mode.CURRENT)
    to_date = models.DateField(null=True, blank=True)
    to_version = models.ForeignKey(
        "FragmentVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="references_in",
    )
    external_url = models.URLField(blank=True)
    resolved = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.from_text}#{self.anchor}"
