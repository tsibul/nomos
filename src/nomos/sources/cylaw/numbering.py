"""Номера фрагментов: транслитерация для eid, ключ сортировки, uid.

Правила — docs/design/data-model.md, разделы 2.2 и 2.3.
"""

import base64
import hashlib
import re
import unicodedata

# Латинские буквы, похожие на греческие, в номерах заменяются греческими:
# в вестнике встречается «11A» латиницей при «11Α» в сводном тексте.
LATIN_TO_GREEK = str.maketrans('ABEZHIKMNOPTYX', 'ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ')

GREEK_UPPER = [
    'Α', 'Β', 'Γ', 'Δ', 'Ε', 'ΣΤ', 'Ζ', 'Η', 'Θ', 'Ι', 'Κ', 'Λ',
    'Μ', 'Ν', 'Ξ', 'Ο', 'Π', 'Ρ', 'Σ', 'Τ', 'Υ', 'Φ', 'Χ', 'Ψ', 'Ω',
]

TRANSLIT = {
    'Α': 'A',
    'Β': 'B',
    'Γ': 'G',
    'Δ': 'D',
    'Ε': 'E',
    'ΣΤ': 'ST',
    'Ζ': 'Z',
    'Η': 'H',
    'Θ': 'TH',
    'Ι': 'I',
    'Κ': 'K',
    'Λ': 'L',
    'Μ': 'M',
    'Ν': 'N',
    'Ξ': 'X',
    'Ο': 'O',
    'Π': 'P',
    'Ρ': 'R',
    'Σ': 'S',
    'Τ': 'T',
    'Υ': 'Y',
    'Φ': 'F',
    'Χ': 'CH',
    'Ψ': 'PS',
    'Ω': 'W',
}

# Греческая алфавитная нумерация пунктов: (α)=1 … (θ)=9, (ι)=10, (ια)=11, (κ)=20.
GREEK_NUMERALS = {
    'α': 1,
    'β': 2,
    'γ': 3,
    'δ': 4,
    'ε': 5,
    'στ': 6,
    'ζ': 7,
    'η': 8,
    'θ': 9,
    'ι': 10,
    'κ': 20,
    'λ': 30,
    'μ': 40,
}

ROMAN = {
    'i': 1,
    'v': 5,
    'x': 10,
    'l': 50,
}

ROMAN_RE = re.compile(r'^[ivxl]+$')
GREEK_LOWER_RE = re.compile(r'^[α-ω]+$')
ARABIC_RE = re.compile(r'^(\d+)([Α-ΩA-Z]*)$')

UPPER_ROMAN_RE = re.compile(r'^[IVXΙΧ]+$')


def normalize_number(raw):
    """Номер без скобок и точек, с греческими буквами вместо похожих латинских."""
    text = raw.strip().strip('.:').replace('΄', '').replace("'", '')
    text = text.replace('(', '').replace(')', '')
    match = re.match(r'^(\d+)(.*)$', text)
    if match:
        return match.group(1) + match.group(2).upper().translate(LATIN_TO_GREEK)
    return text


def split_letters(suffix):
    """«ΣΤ» — одна буква, остальные — по одной."""
    letters = []
    rest = suffix
    while rest:
        if rest.startswith('ΣΤ'):
            letters.append('ΣΤ')
            rest = rest[2:]
        else:
            letters.append(rest[0])
            rest = rest[1:]
    return letters


def translit_upper(suffix):
    return ''.join(TRANSLIT.get(letter, letter) for letter in split_letters(suffix))


def roman_value(text):
    total = 0
    previous = 0
    for char in reversed(text.lower()):
        value = ROMAN[char]
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    return total


def greek_value(text):
    """Значение греческой буквенной нумерации: «ιβ» = 12."""
    if text in GREEK_NUMERALS:
        return GREEK_NUMERALS[text]
    total = 0
    rest = text
    while rest:
        for size in (2, 1):
            part = rest[:size]
            if part in GREEK_NUMERALS:
                total += GREEK_NUMERALS[part]
                rest = rest[size:]
                break
        else:
            return None
    return total


def classify(num):
    """Тип нумерации: arabic, greek, roman или None."""
    if ARABIC_RE.match(num):
        return 'arabic'
    if ROMAN_RE.match(num):
        return 'roman'
    if GREEK_LOWER_RE.match(num):
        return 'greek'
    return None


def eid_token(num):
    """Часть eid для номера: «7Γ» → «7G», «ια» → «ia», «ii» → «ii»."""
    match = ARABIC_RE.match(num)
    if match:
        return match.group(1) + translit_upper(match.group(2))
    if ROMAN_RE.match(num):
        return num
    if GREEK_LOWER_RE.match(num):
        return translit_upper(num.upper()).lower()
    upper = num.upper().translate(str.maketrans('ΙΧ', 'IX'))
    if UPPER_ROMAN_RE.match(upper):
        return upper
    return translit_upper(upper)


def make_uid(act_uid, eid):
    """10 знаков base32. Выводится из адреса при первом появлении фрагмента;
    дальше берётся из уже записанного act.xml и не меняется."""
    digest = hashlib.sha1(('%s#%s' % (act_uid, eid)).encode()).digest()
    return base64.b32encode(digest).decode().lower()[:10]


def slug(term, limit=40):
    """Ключ определения из термина: «αγρότης» → «agrotis» (латиницей, без ударений)."""
    plain = unicodedata.normalize('NFD', term)
    plain = ''.join(char for char in plain if not unicodedata.combining(char)).upper()
    text = ''.join(TRANSLIT.get(char, char) for char in plain).lower()
    text = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    return text[:limit].rstrip('_')
