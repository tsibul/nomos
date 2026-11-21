"""Сводные редакции EUR-Lex → редакции фрагментов.

Опорный язык — en: дерево и адреса берутся из него. Текст el сопоставляется с en по порядку
детей одного вида у каждого родителя (в el пункты нумеруются буквами α, β…).

Первая редакция — база (`key="base"`, изменение `<акт>#base`). Каждая следующая сравнивается с
предыдущей: изменённый, новый или исчезнувший фрагмент получает редакцию с ключом — датой
начала сводной (START.DATE). Причина — акт, метки которого появились в этой сводной
(у сводной, продолжающей действовать, SOURCE.END нет); если новых актов нет — акт новых
меток (следующий этап внесённого акта); иначе — SOURCE.END предыдущей;
изменение — по метке этого акта на фрагменте (`art_1.pnt_3:replace`), без метки — `#base`.
"""

import re
from dataclasses import dataclass, field

from nomos.sources.cylaw.history import BASE_KEY
from nomos.sources.eurlex import formex

ACTIONS = {
    'INSERTED': 'insert',
    'REPLACED': 'replace',
    'DELETED': 'delete',
}
# ACTIVE.LOC: «AR:1;PT:6;PT:a» → art_1.pnt_6.pnt_a
LOC_KINDS = {
    'AR': 'art',
    'PA': 'par',
    'PT': 'pnt',
    'DA': 'ind',
    'AL': 'sub',
}
FN_RE = re.compile(r'<fn id="fn\d+">')


@dataclass
class Edition:
    """Одна сводная редакция: en и el."""

    start: str
    end: str
    source_end: str
    en: object
    el: object
    el_by_eid: dict = field(default_factory=dict)


@dataclass
class Version:
    key: str
    amendment: str
    num: str = ''
    heading: str = ''
    state: str = 'active'
    valid_from: object = None
    applies_from: object = None
    known_from: object = None
    # язык → (абзацы, заголовок на этом языке)
    texts: dict = field(default_factory=dict)


@dataclass(eq=False)
class Fragment:
    """Фрагмент за все редакции: вид, адрес, родитель, порядок — по последней, где он есть."""

    kind: str
    eid: str
    parent_eid: str
    order_key: str
    num: str
    heading: str
    versions: list = field(default_factory=list)


def iso(value):
    """20250414 → 2025-04-14."""
    if not value or len(value) != 8:
        return value or None
    return '%s-%s-%s' % (value[:4], value[4:6], value[6:])


def loc_to_eid(loc):
    parts = []
    for item in loc.split(';'):
        code, _, value = item.partition(':')
        kind = LOC_KINDS.get(code)
        if kind is None or not value:
            return ''
        parts.append('%s_%s' % (kind, value.lower()))
    return '.'.join(parts)


def marker_label(marker_id):
    """ID метки Formex «O002001M001000» → «M2»: номер изменяющего акта в сводной."""
    match = re.match(r'[OC](\d{3})', marker_id)
    return 'M%d' % int(match.group(1)) if match else ''


def renumber_notes(paragraphs):
    """Сноски — fn1, fn2… внутри фрагмента: вставка сноски выше не меняет чужой текст."""
    counter = iter(range(1, 1000))
    return [
        FN_RE.sub(lambda match: '<fn id="fn%d">' % next(counter), paragraph)
        for paragraph in paragraphs
    ]


def split_subparagraphs(en_node, el_node):
    """В en абзацы — подабзацы sub_N (несколько ALINEA), в el — один ALINEA с теми же P
    (ст. 61(1) 282/2011): el-абзацы делятся по подабзацам en по порядку, пункты el —
    подабзацу en, у которого пункты того же вида.

    Без этого у подабзацев нет el-текста, а у владельца — en-текста. Подабзац, на который
    el-абзацев не хватило, остаётся без el (ст. 61a(2) 282/2011: в el нет второго)."""
    subs = [child for child in en_node.children if child.kind == 'sub']
    if not subs or en_node.paragraphs or not el_node.paragraphs:
        return
    if any(child.kind == 'sub' for child in el_node.children):
        return
    if sum(len(sub.paragraphs) for sub in subs) < len(el_node.paragraphs):
        return
    owners = {}
    for sub in subs:
        for child in sub.children:
            owners.setdefault(child.kind, sub)
    if any(child.kind not in owners for child in el_node.children):
        return
    paragraphs = el_node.paragraphs
    children = el_node.children
    el_node.paragraphs = []
    el_node.children = []
    start = 0
    for sub in subs:
        end = start + len(sub.paragraphs)
        own = [child for child in children if owners[child.kind] is sub]
        if start >= len(paragraphs) and not own:
            break
        node = formex.Node('sub', sub.token, num=sub.num, parent=el_node)
        node.paragraphs = paragraphs[start:end]
        node.markers = list(el_node.markers)
        for child in own:
            child.parent = node
        node.children = own
        el_node.children.append(node)
        start = end


def align(en, el, report, label):
    """eid опорного en → узел el; по порядку детей одного вида."""
    found = {}
    # без пары по порядку: en-узлы и лишние el-узлы
    missing = []
    extras = []

    def walk(en_node, el_node):
        split_subparagraphs(en_node, el_node)
        groups = {}
        for child in el_node.children:
            groups.setdefault(child.kind, []).append(child)
        positions = {}
        for child in en_node.children:
            index = positions.get(child.kind, 0)
            positions[child.kind] = index + 1
            candidates = groups.get(child.kind, [])
            if index >= len(candidates):
                missing.append(child)
                # в el абзац не разбит на подабзацы (ст. 61а 282/2011): дети — у родителя
                if child.kind == 'sub' and 'sub' not in groups:
                    walk(child, el_node)
                continue
            found[child.eid] = candidates[index]
            walk(child, candidates[index])
        for kind, candidates in groups.items():
            extras.extend(candidates[positions.get(kind, 0):])

    walk(en.root, el.root)
    # раздел, вынесенный в el не на своё место («ΤΜΗΜΑ 3» главы XI 282/2011 — на верх акта):
    # пара по виду и номеру среди лишних el-узлов
    for child in missing:
        pair = None
        for candidate in extras:
            if candidate.kind == child.kind and candidate.num == child.num:
                pair = candidate
                break
        if pair is None:
            report.append('%s: нет el для %s' % (label, child.eid))
            continue
        extras.remove(pair)
        report.append('%s: el для %s взят не со своего места' % (label, child.eid))
        found[child.eid] = pair
        walk(child, pair)
    paired = {id(node) for node in found.values()}
    for candidate in extras:
        if id(candidate) in paired:
            continue
        report.append('%s: в el лишний %s %s' % (label, candidate.kind, candidate.num))
    return found


def snapshot(edition, node):
    """Содержимое фрагмента в редакции: num, заголовки и абзацы обоих языков."""
    el_node = edition.el_by_eid.get(node.eid)
    texts = {'en': (renumber_notes(node.paragraphs), node.heading)}
    if el_node is not None:
        texts['el'] = (renumber_notes(el_node.paragraphs), el_node.heading)
    return node.num, node.heading, texts


def cause_marker(node, cause):
    """Метка акта-причины на фрагменте: самая глубокая (последняя открытая).

    Без своей метки — метка соседа: «; and» уходит из пункта (e), когда после него
    вставлены (f) и (g)."""
    found = [marker for marker in node.markers if marker.doc == cause]
    if found:
        return found[-1]
    if node.parent is None:
        return None
    for sibling in node.parent.children:
        found = [marker for marker in sibling.markers if marker.doc == cause]
        if found:
            return found[-1]
    return None


class History:
    def __init__(self, act_uid, editions, amending, report):
        """amending: CELEX → {'uid', 'published_on', 'in_force_from'}."""
        self.act_uid = act_uid
        self.editions = editions
        self.amending = amending
        self.report = report
        self.fragments = {}
        self.order = []
        # CELEX → {(положение, вид): (метка M…, применяется с)}: изменения, найденные по меткам
        self.used = {}

    def build(self):
        for edition in self.editions:
            label = edition.start
            edition.el_by_eid = align(edition.en, edition.el, self.report, label)
        base = self.editions[0]
        previous = {}
        for node in base.en.nodes():
            num, heading, texts = snapshot(base, node)
            fragment = self.fragment(node)
            fragment.versions.append(Version(
                key=BASE_KEY,
                amendment='%s#%s' % (self.act_uid, BASE_KEY),
                num=num,
                heading=heading,
                texts=texts,
            ))
            previous[node.eid] = (num, heading, texts)
        self.order = [node.eid for node in base.en.nodes()]
        for before, edition in zip(self.editions, self.editions[1:]):
            previous = self.compare(before, edition, previous)
        return [self.fragments[eid] for eid in self.order]

    def fragment(self, node):
        fragment = self.fragments.get(node.eid)
        if fragment is None:
            fragment = Fragment(
                kind=node.kind,
                eid=node.eid,
                parent_eid=node.parent_eid,
                order_key=node.order_key,
                num=node.num,
                heading=node.heading,
            )
            self.fragments[node.eid] = fragment
        else:
            fragment.parent_eid = node.parent_eid
            fragment.order_key = node.order_key
            fragment.num = node.num
            fragment.heading = node.heading
        return fragment

    def dates(self, cause, start):
        act = self.amending.get(cause)
        if act is None:
            self.report.append('%s: нет изменяющего акта %s в манифесте' % (start, cause))
            return None, None, start, None
        return act['uid'], act['in_force_from'], iso(start), act['published_on']

    def amendment(self, act_uid, cause, node, applies_from):
        marker = cause_marker(node, cause) if node is not None else None
        if marker is None:
            return '%s#%s' % (act_uid, BASE_KEY), False
        provision = loc_to_eid(marker.loc)
        kind = ACTIONS.get(marker.action)
        if not provision or kind is None:
            return '%s#%s' % (act_uid, BASE_KEY), False
        self.used.setdefault(cause, {})[(provision, kind)] = (
            marker_label(marker.id),
            applies_from,
        )
        return '%s#%s:%s' % (act_uid, provision, kind), True

    def node_cause(self, node, cause, before):
        """Акт, изменивший фрагмент: общий акт редакции, если у фрагмента есть его метка;
        иначе — новая метка другого акта из манифеста (этап раньше принятого изменения)."""
        if cause_marker(node, cause) is not None:
            return cause
        fresh = [
            marker for marker in node.markers
            if marker.id not in before.en.markers and marker.doc in self.amending
        ]
        return fresh[-1].doc if fresh else cause

    def cause(self, before, edition):
        docs = {marker.doc for marker in edition.en.markers.values()}
        docs -= {marker.doc for marker in before.en.markers.values()}
        if len(docs) == 1:
            return docs.pop()
        # новых актов нет (новый этап уже внесённого): акт новых меток, если он один
        fresh = {
            marker.doc for marker_id, marker in edition.en.markers.items()
            if marker_id not in before.en.markers
        }
        if not docs and len(fresh) == 1:
            return fresh.pop()
        self.report.append('%s: новых изменяющих актов в метках %d (%s), причина — %s' % (
            edition.start, len(docs), ', '.join(sorted(docs)), before.source_end,
        ))
        return before.source_end

    def compare(self, before, edition, previous):
        cause = self.cause(before, edition)
        act_uid, valid_from, applies_from, known_from = self.dates(cause, edition.start)
        current = {}
        nodes = edition.en.nodes()
        order = []
        old_order = list(self.order)
        for node in nodes:
            current[node.eid] = snapshot(edition, node)
            order.append(node.eid)
        changed = 0
        unmarked = []
        # изменения по меткам других актов (например, этап более раннего акта): акт → число
        others = {}
        for node in nodes:
            state = current[node.eid]
            if previous.get(node.eid) == state:
                self.fragment(node)
                continue
            changed += 1
            node_cause = self.node_cause(node, cause, before)
            if node_cause == cause:
                node_dates = (act_uid, valid_from, applies_from, known_from)
            else:
                node_dates = self.dates(node_cause, edition.start)
                others[node_cause] = others.get(node_cause, 0) + 1
            node_uid, node_valid_from, node_applies_from, node_known_from = node_dates
            if node_uid is None:
                continue
            amendment, marked = self.amendment(node_uid, node_cause, node, node_applies_from)
            if not marked:
                unmarked.append(node.eid)
            num, heading, texts = state
            self.fragment(node).versions.append(Version(
                key=node_applies_from,
                amendment=amendment,
                num=num,
                heading=heading,
                valid_from=node_valid_from,
                applies_from=node_applies_from,
                known_from=node_known_from,
                texts=texts,
            ))
        gone = [eid for eid in previous if eid not in current]
        for eid in gone:
            if act_uid is None:
                continue
            parent = self.fragments[eid].parent_eid
            parent_node = edition.en.by_eid().get(parent)
            amendment, marked = self.amendment(
                act_uid, cause, parent_node, applies_from,
            )
            if not marked:
                unmarked.append(eid)
            fragment = self.fragments[eid]
            fragment.versions.append(Version(
                key=applies_from,
                amendment=amendment,
                num=fragment.num,
                heading=fragment.heading,
                state='repealed',
                valid_from=valid_from,
                applies_from=applies_from,
                known_from=known_from,
            ))
        self.order = merge_order(order, old_order)
        self.report.append('%s (%s): изменено и добавлено %d, исчезло %d, без метки %d%s' % (
            edition.start,
            cause,
            changed,
            len(gone),
            len(unmarked),
            (': ' + ', '.join(unmarked[:12])) if unmarked else '',
        ))
        for other, count in sorted(others.items()):
            self.report.append('%s: из них по меткам %s — %d' % (edition.start, other, count))
        result = dict(current)
        return result


def merge_order(new, old):
    """Порядок записи: новый обход, исчезнувшие — после своего прежнего соседа."""
    merged = list(new)
    present = set(new)
    for index, eid in enumerate(old):
        if eid in present:
            continue
        position = 0
        for earlier in reversed(old[:index]):
            if earlier in present:
                position = merged.index(earlier) + 1
                break
        merged.insert(position, eid)
        present.add(eid)
    return merged
