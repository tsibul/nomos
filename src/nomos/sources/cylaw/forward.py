"""Прямое наложение изменений на базовый текст закона (сводки нет, 76(Ι)/2016).

Базовый текст — первоначальная редакция закона (layout="text"). Изменения
манифеста
накладываются по порядку изменяющих актов в манифесте: каждый акт меняет текст,
каким он
стал после предыдущих.

Изменения, действующие до 01.01.2024, вливаются в базовую редакцию окна
загрузки и в
историю не попадают. Изменения с 01.01.2024 накладываются так же, а для
обратной истории
(history.collect) превращаются в изменения отдельных фрагментов с прежним
текстом:
прежний текст известен точно, снимать изменение не нужно.

Виды изменений:
- replace: old → new в тексте фрагмента (old встречается один раз; at="end" — в
конце);
- replace с <text>: фрагмент заменяется целиком; <text> — строки в раскладке
сводки CyLaw
  от строки статьи («5.-(2) …»), заголовок статьи — строкой выше;
- note: только в отчёт.
"""

import dataclasses

from . import consolidated, history

# пустая преамбула: Parser начинает разбор после «Προοίμιο»
PREAMBLE = ["      Προοίμιο", ""]
FOLDED = "%s → %s: влито в базовую редакцию (действует до 01.01.2024)"


def in_window(op):
    day = op.applies_from or op.valid_from
    return day is not None and day >= history.BASE_KNOWN_FROM


def apply(root, acts, act_uid, report):
    """Наложить изменения на дерево root. Возвращает изменяющие акты для
    history.collect: только изменения окна, по фрагментам, с прежним
    текстом."""
    nodes = {node.eid: node for node in root.walk() if node.kind != "act"}
    result = []
    for act in acts:
        window_ops = []
        for op in act.ops:
            node = nodes.get(op.target)
            if op.kind == "note" or op.absent:
                report.append(
                    "%s → %s: %s"
                    % (op.ref, op.target, op.note or "не применено")
                )
                continue
            if node is None:
                report.append(
                    "%s: цель %s в тексте не найдена" % (op.ref, op.target)
                )
                continue
            derived = apply_op(op, node, act_uid, report)
            if derived is None:
                continue
            if in_window(op):
                window_ops.extend(derived)
            else:
                report.append(FOLDED % (op.ref, op.target))
            nodes = {
                node.eid: node for node in root.walk() if node.kind != "act"
            }
        if window_ops:
            result.append(dataclasses.replace(act, ops=window_ops))
    return result


def apply_op(op, node, act_uid, report):
    """Изменение → список изменений фрагментов для обратной истории; None — не
    применено."""
    if op.kind != "replace":
        report.append(
            "%s → %s: вид «%s» вперёд не накладывается"
            % (op.ref, node.eid, op.kind)
        )
        return None
    if op.layout:
        new = parse_fragment(op, act_uid)
        if new is None:
            report.append(
                "%s → %s: в <text> нет фрагмента %s"
                % (op.ref, node.eid, op.target)
            )
            return None
        if not in_window(op):
            fold(node, new)
            return []
        return merge(op, node, new)
    paragraphs, problem = replaced(op, node.paragraphs)
    if problem:
        report.append("%s → %s: %s" % (op.ref, node.eid, problem))
        return None
    before = list(node.paragraphs)
    node.paragraphs = paragraphs
    return [derive(op, "replace", node.eid, {node.eid: before})]


def replaced(op, paragraphs):
    """Текст после замены old → new или (None, причина)."""
    joined = "\n".join(paragraphs)
    if op.at == "end":
        stripped = joined.rstrip()
        if not stripped.endswith(op.old):
            return None, "в конце нет «%s»" % op.old
        return history.split(
            stripped[: len(stripped) - len(op.old)] + op.new
        ), ""
    count = joined.count(op.old)
    if count != 1:
        return None, "«%s» встречается %d раз" % (op.old, count)
    return history.split(joined.replace(op.old, op.new)), ""


def parse_fragment(op, act_uid):
    """Фрагмент op.target из строк <text>."""
    root = consolidated.Parser(act_uid).parse(PREAMBLE + op.layout)
    nodes = {node.eid: node for node in root.walk()}
    if op.target.split(".")[0] == "art_2" and "art_2" in nodes:
        consolidated.mark_definitions(nodes["art_2"])
        nodes = {node.eid: node for node in root.walk()}
    return nodes.get(op.target)


def fold(node, new):
    """Замена до окна: фрагмент становится новым целиком, прежних потомков
    нет."""
    node.paragraphs = list(new.paragraphs)
    if new.heading:
        node.heading = new.heading
    node.children = []
    for child in new.children:
        node.add(child)


def merge(op, node, new):
    """Замена в окне: фрагменты сверяются по eid. Изменённый текст — замена с
    прежним
    текстом; нового фрагмента прежде не было — вставка; пропавший фрагмент
    остаётся в
    дереве и отменяется."""
    derived = []
    if node.paragraphs != new.paragraphs:
        derived.append(
            derive(op, "replace", node.eid, {node.eid: list(node.paragraphs)})
        )
        node.paragraphs = list(new.paragraphs)
    if new.heading and new.heading != node.heading:
        heading = derive(op, "replace", node.eid, {})
        heading.on = "heading"
        heading.old = node.heading
        heading.new = new.heading
        derived.append(heading)
        node.heading = new.heading
    old_children = {child.token: child for child in node.children}
    new_tokens = {child.token for child in new.children}
    children = []
    for child in new.children:
        if child.token in old_children:
            old = old_children[child.token]
            derived.extend(merge(op, old, child))
            children.append(old)
            continue
        child.parent = node
        child.in_schedule = node.in_schedule
        children.append(child)
        derived.append(derive(op, "insert", child.eid, {}))
    # пропавшие фрагменты — на прежнем месте: после своего прежнего
    # предшественника
    previous = None
    for child in node.children:
        if child.token not in new_tokens:
            position = (
                children.index(previous) + 1 if previous in children else 0
            )
            children.insert(position, child)
            before = {item.eid: list(item.paragraphs) for item in child.walk()}
            derived.append(derive(op, "delete", child.eid, before))
        previous = child
    node.children = children
    for index, child in enumerate(children, 1):
        child.order_key = "%04d" % index
    return derived


def derive(op, kind, target, before):
    """Изменение одного фрагмента; ключ и ссылка — как у исходного
    изменения."""
    return dataclasses.replace(
        op,
        kind=kind,
        target=target,
        before=before,
        texts={},
        layout=[],
        origin=op.kind,
    )
