"""Дерево фрагментов акта, общее для сводного текста и изменяющих актов."""

from dataclasses import dataclass, field

from . import numbering

# Фрагменты, которые не входят в eid статьи (2.2 модели): части и разделы тела закона.
STRUCTURAL = {'prt', 'tis', 'cpt', 'sct', 'cit'}


# eq=False: узлы сравниваются по identity и служат ключами словарей
@dataclass(eq=False)
class Node:
    kind: str
    num: str = ''
    token: str = ''
    heading: str = ''
    paragraphs: list = field(default_factory=list)
    children: list = field(default_factory=list)
    parent: 'Node' = None
    indent: int = 0
    level: float = 0
    order_key: str = ''
    repealed: bool = False
    in_schedule: bool = False
    unnumbered: bool = False

    def add(self, child):
        child.parent = self
        child.in_schedule = self.in_schedule or child.kind == 'sch'
        if not child.token:
            child.token = self.free_token(child)
        if not child.order_key:
            child.order_key = self.next_order_key(child)
        self.children.append(child)
        return child

    def free_token(self, child):
        if child.unnumbered:
            count = sum(1 for node in self.children if node.unnumbered) + 1
            base = '%s_%d' % (child.kind, count)
        else:
            base = '%s_%s' % (child.kind, numbering.eid_token(child.num_plain()))
        token = base
        suffix = 2
        taken = {node.token for node in self.children}
        while token in taken:
            token = '%s_%d' % (base, suffix)
            suffix += 1
        return token

    def next_order_key(self, child):
        if not child.unnumbered:
            key = numbering.order_key(child.num_plain())
            if key:
                return key
        # после ближайшего нумерованного соседа: «0007~1», «0007~2»
        base = '0000'
        count = 1
        for node in reversed(self.children):
            if not node.unnumbered:
                base = node.order_key
                break
            count += 1
        return '%s~%03d' % (base, count)

    def num_plain(self):
        return numbering.normalize_number(self.num)

    @property
    def eid(self):
        """Адрес от ближайшей статьи или приложения; части и разделы тела закона —
        только для самих себя."""
        tokens = []
        node = self
        while node is not None and node.kind != 'act':
            if (
                node is self
                or node.in_schedule
                or node.kind not in STRUCTURAL
                or self.kind in STRUCTURAL
            ):
                tokens.append(node.token)
            if node.kind in ('art', 'sch'):
                break
            node = node.parent
        return '.'.join(reversed(tokens))

    @property
    def parent_eid(self):
        if self.parent is None or self.parent.kind == 'act':
            return ''
        return self.parent.eid

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def text(self):
        return '\n'.join(self.paragraphs)
