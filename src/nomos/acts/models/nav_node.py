from django.db import models


class NavNode(models.Model):
    """Узел меню из Nomos-texts/nav.xml: раздел (ключ, без акта) или пункт основного акта.

    Пункт хранит uid акта и тогда, когда акта нет в базе: пункт стоит на своём месте
    с пометкой «не загружен».
    """

    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='children',
    )
    key = models.CharField(max_length=50, blank=True)
    label = models.CharField(max_length=255, blank=True)
    order = models.PositiveIntegerField()
    act_uid = models.CharField(max_length=100, blank=True)
    act = models.ForeignKey(
        'Act',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='nav_nodes',
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.act_uid or self.key

    @property
    def is_section(self):
        return not self.act_uid

    def keys(self):
        """Ключи разделов от верхнего до этого: ['cy', 'taxes']."""
        node = self
        found = []
        while node is not None:
            if node.key:
                found.append(node.key)
            node = node.parent
        return list(reversed(found))
