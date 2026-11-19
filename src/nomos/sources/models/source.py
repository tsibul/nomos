from django.db import models


class Source(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    license = models.CharField(max_length=255, blank=True)
    terms_url = models.URLField(blank=True)
    attribution = models.TextField(blank=True)
    checked_on = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.code
