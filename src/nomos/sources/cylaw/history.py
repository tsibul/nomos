"""Редакции фрагментов с 01.01.2024: обратное применение изменений к сводному тексту.

Сводный текст CyLaw — последняя известная редакция (с принятыми будущими изменениями).
Манифест `collect.xml` перечисляет изменения: акт, положение, цель, вид, даты. Изменения
снимаются от новых к старым; что нельзя снять однозначно, не угадывается — редакция
получает примечание, изменение попадает в отчёт.
"""

import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Начало окна загрузки: базовая редакция считается известной с этой даты.
BASE_KNOWN_FROM = datetime.date(2024, 1, 1)
BASE_KEY = 'base'

UNKNOWN_BEFORE = 'Текст до изменения %s неизвестен: изменяющий акт его не приводит.'
NOT_REVERSED = 'Изменение %s не снято: %s. Текст до него не восстановлен.'
ABSENT = 'Изменение %s в сводном тексте источника не отражено.'


@dataclass
class Amending:
    """Изменяющий акт из манифеста."""

    uid: str
    kind: str
    number: str
    year: str
    file: str
    published_on: datetime.date
    published_in: str
    in_force_from: datetime.date
    applies_from: datetime.date = None
    title: str = ''
    note: str = ''
    ops: list = field(default_factory=list)


@dataclass
class Op:
    """Одно изменение: положение изменяющего акта → фрагмент закона."""

    act: Amending
    provision: str
    kind: str
    target: str
    old: str = ''
    new: str = ''
    at: str = ''
    text: str = ''
    valid_from: datetime.date = None
    applies_from: datetime.date = None
    until: datetime.date = None
    note: str = ''
    absent: bool = False
    same: bool = False
    # on="heading": изменение заголовка, old/new — заголовок; old_num — прежний номер
    on: str = ''
    old_num: str = None
    # тексты из изменяющего акта: {eid: [абзацы]}; <p for="eid"> — для потомка цели
    texts: dict = field(default_factory=dict)

    @property
    def key(self):
        """Ключ изменения внутри изменяющего акта: положение, вид, своя дата."""
        key = '%s:%s' % (self.provision, self.kind)
        if self.valid_from != self.act.in_force_from:
            key += '@' + self.valid_from.isoformat()
        return key

    @property
    def ref(self):
        return '%s#%s' % (self.act.uid, self.key)

    @property
    def sort_key(self):
        return (self.applies_from or self.valid_from, self.act.published_on)


@dataclass
class Version:
    key: str
    amendment: str
    valid_from: datetime.date = None
    applies_from: datetime.date = None
    known_from: datetime.date = BASE_KNOWN_FROM
    state: str = 'active'
    # None — текст неизвестен
    paragraphs: list = None
    notes: list = field(default_factory=list)
    # None — как у фрагмента в сводном тексте
    num: str = None
    heading: str = None


def parse_date(value):
    if not value:
        return None
    return datetime.date.fromisoformat(value)


def read_manifest(path):
    """Манифест → (атрибуты закона, список изменяющих актов с изменениями)."""
    root = ET.parse(path).getroot()
    acts = []
    for element in root.iter('amending'):
        published = parse_date(element.get('published'))
        act = Amending(
            uid=element.get('uid'),
            kind=element.get('kind'),
            number=element.get('number'),
            year=element.get('year'),
            file=element.get('file'),
            published_on=published,
            published_in=element.get('gazette', ''),
            in_force_from=parse_date(element.get('in-force')) or published,
            applies_from=parse_date(element.get('applies')),
            note=(element.findtext('note') or '').strip(),
        )
        for item in element.iter('op'):
            act.ops.append(make_op(act, item))
        acts.append(act)
    # kind="doubt": примечание о сомнении ко всем редакциям фрагмента;
    # until="…": срок, указанный в самом сводном тексте, — фрагмент утрачивает силу на
    # следующий день. Текст remark идёт и в отчёт.
    remarks = [
        (item.get('target'), ' '.join(item.text.split()), item.attrib)
        for item in root.iter('remark')
    ]
    return root.attrib, acts, remarks


def make_op(act, item):
    return Op(
        act=act,
        provision=item.get('provision'),
        kind=item.get('kind'),
        target=item.get('target'),
        old=item.get('old', ''),
        new=item.get('new', ''),
        at=item.get('at', ''),
        text=item.get('text', ''),
        valid_from=parse_date(item.get('from')) or act.in_force_from,
        applies_from=parse_date(item.get('applies-from')) or act.applies_from,
        until=parse_date(item.get('until')),
        note=' '.join((item.text or '').split()),
        absent=item.get('absent') == '1',
        same=item.get('same') == '1',
        on=item.get('on', ''),
        old_num=item.get('old-num'),
        texts=op_texts(item),
    )


def op_texts(item):
    texts = {}
    for paragraph in item.findall('p'):
        eid = paragraph.get('for', item.get('target'))
        texts.setdefault(eid, []).append(' '.join(paragraph.text.split()))
    return texts


def ops_by_target(acts):
    found = {}
    for act in acts:
        for op in act.ops:
            found.setdefault(op.target, []).append(op)
    return found


def is_inside(eid, target):
    return eid == target or eid.startswith(target + '.')


def build_versions(node, ops, base_ref, report):
    """Редакции одного фрагмента: от сводного текста назад по изменениям.

    Фрагмент может появляться и исчезать несколько раз (временные Διατάγματα): после
    снятия вставки его нет; более ранняя вставка даёт редакцию с текстом, только если
    в манифесте сказано, что текст тот же (`same="1"`)."""
    later = []
    text = list(node.paragraphs)
    latest_text = text
    present = True
    title = {'num': node.num, 'heading': node.heading}
    for op in sorted(ops, key=lambda item: item.sort_key, reverse=True):
        if op.absent or op.kind == 'note':
            report.append('%s → %s: %s' % (op.ref, node.eid, op.note or 'не применено'))
            continue
        if not present and op.kind == 'delete':
            # отмена фрагмента, которого потом не было, пока его не вставили заново:
            # до отмены он действовал, его текст — из манифеста или неизвестен
            present = True
            text = None
        if not present and op.kind != 'insert':
            report.append('%s → %s: фрагмента в это время нет' % (op.ref, node.eid))
            continue
        if not present:
            text = list(latest_text) if op.same else None
        if node.eid in op.texts:
            # текст редакции приведён в манифесте по изменяющему акту
            text = list(op.texts[node.eid])
        version = version_for(op, text)
        version.num = title['num']
        version.heading = title['heading']
        if op.on == 'heading':
            later.append(version)
            title = reverse_heading(op, title, node, report)
            continue
        if op.until:
            later.append(revert_version(op, node, text))
        later.append(version)
        if op.kind == 'insert':
            present = False
            continue
        if op.kind == 'delete':
            version.state = 'repealed'
            version.paragraphs = []
            text = None
            continue
        if text is None:
            report.append('%s → %s: не снято, текст раньше уже неизвестен' % (op.ref, node.eid))
            continue
        previous, problem = reverse(op, text)
        if problem:
            report.append('%s → %s: %s' % (op.ref, node.eid, problem))
        text = previous
    if present:
        later.append(Version(
            key=BASE_KEY,
            amendment=base_ref,
            paragraphs=text,
            num=title['num'],
            heading=title['heading'],
        ))
    versions = list(reversed(later))
    for version, newer in zip(versions, versions[1:]):
        if version.paragraphs is None and version.state == 'active':
            version.notes.append(missing_note(newer.amendment))
    return attach_notes(node, versions)


def reverse_heading(op, title, node, report):
    """Заголовок (и номер) до изменения заголовка; прежний заголовок может быть пустым."""
    heading = title['heading']
    # значения атрибутов в манифесте переносятся по строкам
    new = ' '.join(op.new.split())
    old = ' '.join(op.old.split())
    count = heading.count(new) if new else 0
    if count != 1:
        report.append('%s → %s: заголовок: «%s» встречается %d раз' % (
            op.ref, node.eid, new, count))
        return title
    num = title['num'] if op.old_num is None else op.old_num
    previous = ' '.join(heading.replace(new, old).split())
    return {'num': num, 'heading': previous}


def version_for(op, text):
    return Version(
        key=op.valid_from.isoformat(),
        amendment=op.ref,
        valid_from=op.valid_from,
        applies_from=op.applies_from,
        known_from=op.act.published_on,
        paragraphs=None if text is None else list(text),
    )


def revert_version(op, node, text):
    """Временное изменение: после `until` действует прежнее состояние."""
    day = op.until + datetime.timedelta(days=1)
    version = Version(
        key=day.isoformat(),
        amendment=op.ref,
        valid_from=day,
        applies_from=day,
        known_from=op.act.published_on,
    )
    if op.kind == 'insert':
        version.state = 'repealed'
        version.paragraphs = []
        return version
    if op.kind == 'delete':
        # после временной отмены действует прежний текст — он неизвестен
        return version
    previous, problem = reverse(op, text)
    version.paragraphs = previous
    if problem:
        version.notes.append(NOT_REVERSED % (op.ref, problem))
    return version


def missing_note(ref):
    return UNKNOWN_BEFORE % ref


def reverse(op, text):
    """Текст до изменения или (None, причина)."""
    if text is None:
        return None, 'текст неизвестен'
    joined = '\n'.join(text)
    if op.kind == 'replace':
        if not op.old and not op.new:
            return None, 'прежний текст не приведён'
        if op.at == 'end':
            if not joined.rstrip().endswith(op.new):
                return None, 'в конце нет «%s»' % op.new
            stripped = joined.rstrip()
            return split(stripped[:len(stripped) - len(op.new)] + op.old), ''
        count = joined.count(op.new)
        if count != 1:
            return None, '«%s» встречается %d раз' % (op.new, count)
        return split(joined.replace(op.new, op.old)), ''
    if op.kind == 'add':
        count = joined.count(op.text)
        if count != 1:
            return None, 'добавленный текст встречается %d раз' % count
        return split(joined.replace(op.text, '')), ''
    return None, 'вид изменения «%s» не снимается' % op.kind


def split(joined):
    return [paragraph.strip() for paragraph in joined.split('\n') if paragraph.strip()]


def attach_notes(node, versions):
    """Неизвестный текст — примечание у редакции. Изменения одного дня — одна редакция
    с итоговым текстом."""
    merged = []
    for version in versions:
        if merged and merged[-1].key == version.key:
            version.notes = merged[-1].notes + version.notes
            merged[-1] = version
            continue
        merged.append(version)
    for version in merged:
        if version.paragraphs is None and not version.notes:
            version.notes.append('Текст этой редакции неизвестен.')
    return merged


def collect(root, acts, base_ref, report):
    """Редакции всех фрагментов дерева: {node: [Version]}.

    Вставка фрагмента действует и на всех его потомков."""
    targets = ops_by_target(acts)
    eids = {node.eid for node in root.walk() if node.kind != 'act'}
    for target, ops in targets.items():
        if target not in eids:
            for op in ops:
                report.append('%s: цель %s в сводном тексте не найдена' % (op.ref, target))
    result = {}
    for node in root.walk():
        if node.kind == 'act':
            continue
        ops = list(targets.get(node.eid, []))
        own_insert = any(op.kind == 'insert' and not op.absent for op in ops)
        # своя вставка задаёт сроки фрагмента точнее, чем вставка предка;
        # из предков — только ближайший со вставкой
        ancestors = sorted(
            (target for target in targets if target != node.eid and is_inside(node.eid, target)),
            key=len,
            reverse=True,
        )
        for target in ancestors:
            if own_insert:
                break
            inherited = [op for op in targets[target] if op.kind in ('insert', 'delete')]
            ops.extend(inherited)
            own_insert = any(op.kind == 'insert' and not op.absent for op in inherited)
        result[node] = build_versions(node, ops, base_ref, report)
    return result
