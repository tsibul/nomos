"""Упоминания других актов → ссылка на акт целиком, `<ref id="r1" to="<uid>">`.

Размечается только акт, который есть в Nomos-texts:
- кипрский закон по названию: «ο περί Κοινωνικών Ασφαλίσεων Νόμος (του 2010)»,
«οι περί
  ΦΠΑ Νόμοι του 2000 έως 2024» — базовый закон; «ο περί ΦΠΑ (Τροποποιητικός)
  (Αρ. 2) Νόμος
  του 2025» — изменяющий; по номеру: «59(Ι)/2010»;
- акт ЕС по номеру: «Οδηγία (ΕΕ) 2022/2523», «Κανονισμού (ΕΕ) αριθ. 904/2010».

Год у названия базового закона, если указан, должен совпасть с годом закона:
«Νόμος του
1980» с тем же названием — прежний закон, не размечается. Упоминание самого
акта не
размечается. В переводе закон по названию размечается, только если та же ссылка
есть в
оригинале этого текста (названия переведены по-разному); акт ЕС — по номеру,
как в el.
"""

import re
from collections import Counter
from dataclasses import dataclass

DEFENCE = "Έκτακτης Εισφοράς για την Άμυνα της Δημοκρατίας"
INCOME = "Φορολογίας του Εισοδήματος"
VAT = "Φόρου Προστιθέμενης Αξίας"
COLLECTION = "Βεβαιώσεως και Εισπράξεως Φόρων"

# базовые законы: ядро названия в el (после «περί») → (uid, год)
EL_BASE_LAWS = {
    COLLECTION: ("cy/law/1978/4", "1978"),
    "Ανάπτυξης Ανθρώπινου Δυναμικού": ("cy/law/1999/125_I", "1999"),
    VAT: ("cy/law/2000/95_I", "2000"),
    "Γενικού Συστήματος Υγείας": ("cy/law/2001/89_I", "2001"),
    DEFENCE: ("cy/law/2002/117_I", "2002"),
    INCOME: ("cy/law/2002/118_I", "2002"),
    "Φόρων Κατανάλωσης": ("cy/law/2004/91_I", "2004"),
    "Τελωνειακού Κώδικα": ("cy/law/2004/94_I", "2004"),
    "Κοινωνικών Ασφαλίσεων": ("cy/law/2010/59_I", "2010"),
}

# сокращённые названия
EL_ALIASES = {"ΦΠΑ": VAT}

# те же законы в русском переводе: варианты ядра названия (после «Закон…»)
RU_BASE_LAWS = {
    "о подтверждении и взимании налогов": COLLECTION,
    "об установлении и взимании налогов": COLLECTION,
    "о развитии человеческих ресурсов": "Ανάπτυξης Ανθρώπινου Δυναμικού",
    "о налоге на добавленную стоимость": VAT,
    "о НДС": VAT,
    "об Общей системе здравоохранения": "Γενικού Συστήματος Υγείας",
    "о специальном взносе на оборону Республики": DEFENCE,
    "о специальном взносе на оборону": DEFENCE,
    "о подоходном налоге": INCOME,
    "о налогообложении доходов": INCOME,
    "о социальном страховании": "Κοινωνικών Ασφαλίσεων",
}

EL_LAW_RE = re.compile(
    r"(?<![\wά-ώ])περί\s+(?P<core>[Α-ΩΆ-Ώ][^()<>«»]{0,80}?)"
    r"(?P<amend>\s*\(Τροποποιητικ(?:ός|ού|οί|ών|ούς)\))?"
    r"(?:\s*\(Αρ\.\s*(?P<num>\d+)\))?"
    r"\s+[Νν]όμ(?:ος|ου|ο|οι|ων|ους)(?![\wά-ώ])"
    r"(?:\s+(?:του|των|τους)\s+(?P<year>\d{4})(?![\d/])"
    r"(?:\s+(?:έως|μέχρι)\s+(?:\(Αρ\.\s*\d+\)\s+)?(?:του\s+)?\d{4}(?![\d/]))?)?"
)

RU_LAW_RE = re.compile(
    r"(?<![\wё])[Зз]акон\w*\s+(?P<core>%s)(?![\wё])"
    r"(?P<amend>\s*\((?:изменяющ\w+|о\s+внесении\s+изменений)\))?"
    r"(?:\s*\(№\s*(?P<num>\d+)\))?"
    r"(?:\s+(?P<year>\d{4})(?![\d/])(?:\s*(?:года|годов|гг?\.))?"
    r"(?:\s*(?:–|—|-|до|по)\s*(?:\(№\s*\d+\)\s*)?\d{4}(?![\d/])"
    r"(?:\s*(?:года|годов|гг?\.))?)?)?"
    % "|".join(
        re.escape(core) for core in sorted(RU_BASE_LAWS, key=len, reverse=True)
    )
)

# номер закона: «59(Ι)/2010», «118(I)/2025»
NUMBER_RE = re.compile(
    r"(?<![\d/(])(?P<number>\d+)\([ΙI]\)/(?P<year>\d{4})(?![\d/])"
)

EL_EU_RE = re.compile(
    r"(?<![\wά-ώ])(?P<word>[Οο]δηγί\w*|[Κκ]ανονισμ\w*|[Εε]κτελεστικ\w*\s+[Κκ]ανονισμ\w*)"
    r"(?:\s+του\s+Συμβουλίου)?"
    r"\s+(?:\((?:ΕΕ|ΕΚ|ΕΟΚ)\)\s+)?(?:αριθ\.\s*)?"
    r"(?P<a>\d+)/(?P<b>\d+)(?:/(?:ΕΕ|ΕΚ|ΕΟΚ))?(?![\d/])"
)

RU_EU_RE = re.compile(
    r"(?<![\wё])(?P<word>[Дд]иректив\w*|[Рр]егламент\w*"
    r"|[Ии]мплементационн\w+\s+регламент\w*|[Ии]сполнительн\w+\s+регламент\w*)"
    r"(?:\s+Совета)?(?:\s+\((?:ЕС|ЕЭС|ЕК)\))?(?:\s+Совета)?"
    r"\s+(?:№\s*)?(?P<a>\d+)/(?P<b>\d+)(?:/(?:ЕС|ЕЭС|ЕК))?(?![\d/])"
)

TITLE_RE = re.compile(r'\btitle="([^"]*)"')
LANG_EL_RE = re.compile(r'<expression\b[^>]*\blang="el"[^>]*>')


def normalize(text):
    # «Προστιθέµενης» в заглавиях — со знаком микро вместо μ
    return " ".join(text.replace("µ", "μ").split())


def canonical(core):
    core = normalize(core)
    return EL_ALIASES.get(core, core)


@dataclass
class Index:
    """Акты Nomos-texts: uid, изменяющие законы по (ядру, году, номеру)."""

    uids: set
    amending: dict

    @classmethod
    def read(cls, texts_dir):
        uids = set()
        amending = {}
        for path in texts_dir.glob("*/*/*/*/act.xml"):
            uid = str(path.parent.relative_to(texts_dir))
            uids.add(uid)
            if not uid.startswith("cy/law/"):
                continue
            expression = LANG_EL_RE.search(path.read_text(encoding="utf-8"))
            title = (
                TITLE_RE.search(expression.group(0)) if expression else None
            )
            match = (
                EL_LAW_RE.search(normalize(title.group(1))) if title else None
            )
            if match and match.group("amend") and match.group("year"):
                core = canonical(match.group("core"))
                key = (core, match.group("year"), match.group("num") or "1")
                amending[key] = uid
        return cls(uids, amending)

    def law(self, core, amend, year, num):
        core = canonical(core)
        if amend:
            return (
                self.amending.get((core, year, num or "1")) if year else None
            )
        if core not in EL_BASE_LAWS:
            return None
        uid, base_year = EL_BASE_LAWS[core]
        if year and year != base_year:
            return None
        return uid if uid in self.uids else None

    def number(self, number, year):
        uid = "cy/law/%s/%s_I" % (year, number)
        return uid if uid in self.uids else None

    def eu(self, word, a, b):
        kind = (
            "dir" if word.lower().startswith(("οδηγ", "директив")) else "reg"
        )
        for year, number in ((a, b), (b, a)):
            uid = "eu/%s/%s/%s" % (kind, year, number)
            if len(year) == 4 and uid in self.uids:
                return uid
        return None


class Mentions:
    """Упоминания актов в тексте одного языка; own — uid размечаемого акта."""

    def __init__(self, index, lang, own):
        self.index = index
        self.lang = lang
        self.own = own
        # счётчик пропусков; Marker подставляет свой
        self.skipped = Counter()

    def translated(self):
        return Mentions(self.index, "ru", self.own)

    def spans(self, text, agreed):
        """(начало, конец, uid) по порядку; agreed — цели оригинала (для
        перевода)."""
        found = []
        for start, end, uid, by_title in self.candidates(text):
            if uid is None:
                continue
            if uid == self.own:
                self.skipped["упоминание самого акта"] += 1
                continue
            if by_title and self.lang == "ru" and uid not in agreed:
                self.skipped[
                    "закон по названию, которого нет в оригинале"
                ] += 1
                continue
            found.append((start, end, uid))
        return found

    def candidates(self, text):
        if self.lang == "el":
            for match in EL_LAW_RE.finditer(text):
                uid = self.index.law(
                    *match.group("core", "amend", "year", "num")
                )
                yield match.start(), match.end(), uid, True
            eu_re = EL_EU_RE
        else:
            for match in RU_LAW_RE.finditer(text):
                core = RU_BASE_LAWS[match.group("core")]
                uid = self.index.law(
                    core, *match.group("amend", "year", "num")
                )
                yield match.start(), match.end(), uid, True
            eu_re = RU_EU_RE
        for match in NUMBER_RE.finditer(text):
            yield (
                match.start(),
                match.end(),
                self.index.number(*match.group(1, 2)),
                False,
            )
        for match in eu_re.finditer(text):
            yield (
                match.start(),
                match.end(),
                self.index.eu(*match.group(1, 2, 3)),
                False,
            )
