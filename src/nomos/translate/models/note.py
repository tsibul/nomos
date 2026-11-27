from django.db import models


class Note(models.Model):
    class Kind(models.TextChoices):
        TRANSLATION_DOUBT = "doubt", "Сомнение в переводе"
        SOURCE_COMMENT = "source_comment", "Комментарий источника"
        EDITOR_COMMENT = "editor_comment", "Редакторский комментарий"

    class Origin(models.TextChoices):
        LLM = "llm", "LLM второй проход"
        HUMAN = "human", "Человек"
        SOURCE = "source", "Источник"

    class Status(models.TextChoices):
        OPEN = "open", "Открыто"
        CONFIRMED = "confirmed", "Подтверждено"
        REJECTED = "rejected", "Отклонено"
        FIXED = "fixed", "Исправлено"

    target = models.ForeignKey(
        "acts.FragmentText", on_delete=models.CASCADE, related_name="notes"
    )
    segment = models.PositiveIntegerField(null=True, blank=True)
    span = models.TextField(blank=True)
    kind = models.CharField(max_length=20, choices=Kind)
    origin = models.CharField(max_length=10, choices=Origin)
    status = models.CharField(
        max_length=10, choices=Status, default=Status.OPEN
    )
    body = models.TextField()
    suggestion = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    author = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.target}: {self.kind}"
