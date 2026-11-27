"""Полнота русского перевода акта по XML Nomos-texts.

Перевод полный, если есть ru-название, на каждый заголовок языка-образца
(structure-lang
акта) есть непустой ru-заголовок и на каждый непустой текст образца — непустой
ru-текст.
Правило общее для check_translations и load_texts при NOMOS_PUBLIC_ONLY.
"""

from nomos.sources.cylaw import texts_xml

LANG = "ru"

# сколько адресов фрагментов показывать в строке причины
SHOWN_EIDS = 5


def missing(element, folder):
    """Чего не хватает ru-переводу акта: [причина]; None — ru у акта нет."""
    expressions = {
        item.get("lang"): item for item in element.iter("expression")
    }
    translation = expressions.get(LANG)
    if translation is None:
        return None
    reference = expressions.get(element.get("structure-lang") or "el")
    original = {}
    if reference is not None:
        original = texts_xml.read_texts(folder / reference.get("file", ""))
    translated = texts_xml.read_texts(folder / translation.get("file", ""))
    headings = {}
    for fragment in element.iter("fragment"):
        for version in fragment.iter("version"):
            key = (fragment.get("uid"), version.get("key"))
            headings[key] = (fragment.get("eid"), version.get("heading", ""))
    no_heading = []
    no_body = []
    for key, (eid, heading) in headings.items():
        body, _notes, _unknown, text_heading = original.get(
            key, ("", [], False, "")
        )
        ru_body, _notes, _unknown, ru_heading = translated.get(
            key, ("", [], False, "")
        )
        if (heading or text_heading).strip() and not ru_heading.strip():
            no_heading.append(eid)
        if body.strip() and not ru_body.strip():
            no_body.append(eid)
    reasons = []
    if not translation.get("title", "").strip():
        reasons.append("нет названия")
    if no_heading:
        reasons.append("нет заголовков: %s" % listed(no_heading))
    if no_body:
        reasons.append("нет текста: %s" % listed(no_body))
    return reasons


def listed(eids):
    """Число фрагментов и первые адреса (у фрагмента может быть несколько
    редакций)."""
    unique = list(dict.fromkeys(eids))
    shown = ", ".join(unique[:SHOWN_EIDS])
    if len(unique) > SHOWN_EIDS:
        shown += ", …"
    return "%d (%s)" % (len(unique), shown)
