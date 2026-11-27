from django.db import models


class Expression(models.Model):
    class Status(models.TextChoices):
        OFFICIAL_TEXT = "official_text", "Официальный текст"
        OFFICIAL_TRANSLATION = "official_translation", "Официальный перевод"
        UNOFFICIAL_TRANSLATION = (
            "unofficial_translation",
            "Неофициальный перевод",
        )
        OUR_TRANSLATION = "our_translation", "Наш перевод"

    class Structure(models.TextChoices):
        SAME = "same", "Совпадает с опорной"
        ALIGNED = "aligned", "Сопоставлена"

    act = models.ForeignKey(
        "Act", on_delete=models.CASCADE, related_name="expressions"
    )
    lang = models.CharField(max_length=10)
    status = models.CharField(max_length=30, choices=Status)
    source = models.ForeignKey(
        "sources.Source",
        on_delete=models.PROTECT,
        related_name="expressions",
    )
    title = models.CharField(max_length=500, blank=True)
    short_title = models.CharField(max_length=255, blank=True)
    structure = models.CharField(
        max_length=10, choices=Structure, default=Structure.SAME
    )
    file = models.CharField(max_length=500, blank=True)
    # показывать без входа; по умолчанию текст виден только после входа
    # db_default: вставка из кода без этого поля (другие ветки на общей БД) не
    # падает
    public = models.BooleanField(default=False, db_default=False)
    # адрес этого текста (страницы или файла акта) у источника; пусто —
    # известен только сайт
    url = models.URLField(max_length=500, blank=True, db_default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["act", "lang", "status"],
                name="expression_act_lang_status_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.act}:{self.lang}"
