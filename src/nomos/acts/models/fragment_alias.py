from django.db import models


class FragmentAlias(models.Model):
    fragment = models.ForeignKey('Fragment', on_delete=models.CASCADE, related_name='aliases')
    eid = models.CharField(max_length=200)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.eid
