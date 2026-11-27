"""Акты у основного акта меню: блоки по связям (docs/design/site-navigation.md,
3.2).

Основной акт — пункт nav.xml. Неосновной акт стоит у основного акта, к которому
ведёт его
связь; связь к неосновному акту (ΚΔΠ, изменяющая ΚΔΠ) ведёт дальше по цепочке,
и акт
попадает в блок, где стоит промежуточный акт.
"""

import datetime
from collections import defaultdict

from .models import Act, ActRelation, NavNode

AMENDING = "amending"
SUBSIDIARY = "subsidiary"
FORMS = "forms"
EU = "eu"
OTHER = "other"

# порядок блоков на странице (пункты 2–7 проекта; справочной информации пока
# нет)
BLOCKS = (AMENDING, SUBSIDIARY, FORMS, EU, OTHER)

# 'order' — приказы: вида в Act.Kind ещё нет
SUBSIDIARY_KINDS = {Act.Kind.REGULATION_BY_LAW, "order"}
FORM_KINDS = {Act.Kind.FORM, Act.Kind.INSTRUCTION}


def relation_block(kind, act):
    """Блок для акта act, связанного с основным актом связью вида kind."""
    if kind == ActRelation.Kind.AMENDS:
        return AMENDING
    if kind == ActRelation.Kind.BASED_ON and act.kind in SUBSIDIARY_KINDS:
        return SUBSIDIARY
    explaining = (ActRelation.Kind.BASED_ON, ActRelation.Kind.EXPLAINS)
    if kind in explaining and act.kind in FORM_KINDS:
        return FORMS
    return OTHER


def main_act_ids():
    """id актов — пунктов меню, загруженных в базу."""
    return set(
        NavNode.objects.filter(act__isnull=False).values_list(
            "act_id", flat=True
        )
    )


def sort_key(act):
    return (act.published_on or datetime.date.min, act.uid)


class Relations:
    """Все связи базы, загруженные один раз: входящие и исходящие по id
    акта."""

    def __init__(self):
        self.incoming = defaultdict(list)
        self.outgoing = defaultdict(list)
        for relation in ActRelation.objects.select_related(
            "from_act", "to_act"
        ):
            self.incoming[relation.to_act_id].append(relation)
            self.outgoing[relation.from_act_id].append(relation)

    def blocks(self, act, main_ids):
        """{блок: [акты]} у акта act; акты в блоке — по дате публикации."""
        found = {}
        seen = {act.id}
        queue = [(act.id, None)]
        while queue:
            target, inherited = queue.pop(0)
            for relation in self.incoming[target]:
                other = relation.from_act
                # «связанный акт» виден только у акта, от которого идёт связь
                if (
                    relation.kind == ActRelation.Kind.RELATED
                    or other.id in seen
                ):
                    continue
                seen.add(other.id)
                block = inherited or relation_block(relation.kind, other)
                found[other.id] = (block, other)
                # другой основной акт стоит у себя: его связи сюда не тянутся
                if other.id not in main_ids:
                    queue.append((other.id, block))
        for relation in self.outgoing[act.id]:
            if (
                relation.kind != ActRelation.Kind.RELATED
                or relation.to_act_id in seen
            ):
                continue
            other = relation.to_act
            seen.add(other.id)
            block = EU if other.jurisdiction == Act.Jurisdiction.EU else OTHER
            found[other.id] = (block, other)
        grouped = defaultdict(list)
        for block, other in found.values():
            grouped[block].append(other)
        return {
            block: sorted(acts, key=sort_key)
            for block, acts in grouped.items()
        }


def unattached():
    """Акты, которые не попадают в меню: не основные и без связи к основному
    акту."""
    main_ids = main_act_ids()
    relations = Relations()
    attached = set(main_ids)
    for act in Act.objects.filter(id__in=main_ids):
        for acts in relations.blocks(act, main_ids).values():
            attached.update(other.id for other in acts)
    return list(Act.objects.exclude(id__in=attached).order_by("uid"))
