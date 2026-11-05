"""Normalization utilities for products, regions, and company/broker names."""

from __future__ import annotations

import re
from typing import NamedTuple


# ---------------------------------------------------------------------------
# 1. Regions mapping (14 administrative regions of Uzbekistan)
# ---------------------------------------------------------------------------
class RegionEntry(NamedTuple):
    region_id: str
    region_uz: str
    region_ru: str
    aliases: str  # pipe-separated search aliases (lowercase, normalized apostrophes)


REGIONS_DATA: list[RegionEntry] = [
    RegionEntry(
        "andijan",
        "Andijon viloyati",
        "Андижанская область",
        "andijon|andijan|andijon viloyati|андижан|андижанская область|андижанская|андижон",
    ),
    RegionEntry(
        "bukhara",
        "Buxoro viloyati",
        "Бухарская область",
        "buxoro|bukhara|buxoro viloyati|бухара|бухарская область|бухарская|бухоро",
    ),
    RegionEntry(
        "fergana",
        "Farg‘ona viloyati",
        "Ферганская область",
        "farg'ona|fargona|fergana|farg'ona viloyati|фергана|ферганская область|ферганская|фаргона|фарғона",
    ),
    RegionEntry(
        "jizzakh",
        "Jizzax viloyati",
        "Джизакская область",
        "jizzax|jizzakh|djizak|jizzax viloyati|джизак|джизакская область|джизакская|жиззах",
    ),
    RegionEntry(
        "namangan",
        "Namangan viloyati",
        "Наманганская область",
        "namangan|namangan viloyati|наманган|наманганская область|наманганская",
    ),
    RegionEntry(
        "navoi",
        "Navoiy viloyati",
        "Навоийская область",
        "navoiy|navoi|navoiy viloyati|навои|навоийская область|навоийская|навоий",
    ),
    RegionEntry(
        "kashkadarya",
        "Qashqadaryo viloyati",
        "Кашкадарьинская область",
        "qashqadaryo|kashkadarya|qarshi|qashqadaryo viloyati|кашкадарья|кашкадарьинская область|кашкадарьинская|карши|қашқадарё",
    ),
    RegionEntry(
        "karakalpakstan",
        "Qoraqalpog‘iston Respublikasi",
        "Республика Каракалпакстан",
        "qoraqalpog'iston|qoraqalpogiston|karakalpakstan|nukus|qoraqalpog'iston respublikasi|каракалпакстан|республика каракалпакстан|нукус|қорақалпоғистон",
    ),
    RegionEntry(
        "samarkand",
        "Samarqand viloyati",
        "Самаркандская область",
        "samarqand|samarkand|samarqand viloyati|самарканд|самаркандская область|самаркандская|самарқанд",
    ),
    RegionEntry(
        "syrdarya",
        "Sirdaryo viloyati",
        "Сырдарьинская область",
        "sirdaryo|syrdarya|guliston|sirdaryo viloyati|сырдарья|сырдарьинская область|сырдарьинская|гулистан|сирдарё",
    ),
    RegionEntry(
        "surkhandarya",
        "Surxondaryo viloyati",
        "Сурхандарьинская область",
        "surxondaryo|surkhandarya|termiz|surxondaryo viloyati|сурхандарья|сурхандарьинская область|сурхандарьинская|термез|сурхондарё",
    ),
    RegionEntry(
        "tashkent_city",
        "Toshkent shahri",
        "г. Ташкент",
        "toshkent shahri|toshkent sh|toshkent shahar|tashkent city|г. ташкент|город ташкент|ташкент г|тошкент шахри|тошкент шаҳри",
    ),
    RegionEntry(
        "tashkent_region",
        "Toshkent viloyati",
        "Ташкентская область",
        "toshkent viloyati|tashkent region|nurafshon|ташкентская область|ташкентская|тошкент вилояти",
    ),
    RegionEntry(
        "khorezm",
        "Xorazm viloyati",
        "Хорезмская область",
        "xorazm|khorezm|urganch|xorazm viloyati|хорезм|хорезмская область|хорезмская|ургенч|хоразм",
    ),
]

UZ_TO_RU_REGION: dict[str, str] = {r.region_uz: r.region_ru for r in REGIONS_DATA}
RU_TO_UZ_REGION: dict[str, str] = {r.region_ru: r.region_uz for r in REGIONS_DATA}


# ---------------------------------------------------------------------------
# 2. Text & Apostrophe & Transliteration Helpers
# ---------------------------------------------------------------------------
APOSTROPHE_CHARS = ("‘", "’", "ʼ", "ʻ", "`", "´", "ʹ", "ʽ")


def normalize_apostrophes(text: str) -> str:
    for ch in APOSTROPHE_CHARS:
        text = text.replace(ch, "'")
    return text


# Uzbek/Russian Cyrillic -> Uzbek Latin transliteration table for entity search
CYR_TO_LAT_MAP: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "ғ": "g'",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "j",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "қ": "q",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "ў": "o'",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "x",
    "ҳ": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "'",
    "ы": "i",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def cyr_to_lat(text: str) -> str:
    """Transliterate Cyrillic characters in text to Uzbek Latin."""
    s = normalize_apostrophes(text.lower())
    out: list[str] = []
    for i, ch in enumerate(s):
        if ch == "е" and (i == 0 or s[i - 1] in " aeiouy'\"-(/"):
            # Initial 'е' in Uzbek Cyrillic can be 'ye' or 'e' (e.g. ЕНЕРГО -> ENERGO, ЕДЎДЎ -> YEDO'DO')
            # Keep 'e' in main transliteration and handle 'ye' in search variants
            out.append("e")
        elif ch in CYR_TO_LAT_MAP:
            out.append(CYR_TO_LAT_MAP[ch])
        else:
            out.append(ch)
    return "".join(out)


LEGAL_FORMS_PATTERN = re.compile(
    r"\b("
    r"mas'uliyati\s+cheklangan\s+jamiyati|"
    r"masuliyati\s+cheklangan\s+jamiyati|"
    r"aksiyadorlik\s+jamiyati|"
    r"oilaviy\s+korxona|"
    r"xususiy\s+korxona|"
    r"qo'shma\s+korxona|"
    r"qoshma\s+korxona|"
    r"mchj|мчж|ооо|ooo|aj|аж|ao|ао|qk|қк|xk|хк|ok|ок|гуп|dup|uk"
    r")\b",
    flags=re.IGNORECASE,
)


def normalize_company_name(raw_name: str) -> tuple[str, str]:
    """Return (name_clean, name_search) for a participant or broker.

    - name_clean: lowercase, normalized apostrophes, quotes and legal forms stripped.
    - name_search: Latin-transliterated + apostrophe-stripped variant so both Cyrillic
      and Latin queries match cleanly.
    """
    s = normalize_apostrophes(raw_name).lower()
    s = s.replace('"', " ").replace("«", " ").replace("»", " ")
    s = LEGAL_FORMS_PATTERN.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    lat = cyr_to_lat(s)
    # Also create an apostrophe-free ASCII token version so o' / o / ye / e match
    lat_ascii = lat.replace("'", "")
    if lat_ascii.startswith("e"):
        lat_ye = "y" + lat_ascii
    elif lat_ascii.startswith("ye"):
        lat_ye = lat_ascii[1:]
    else:
        lat_ye = lat_ascii

    search_blob = f"{s} | {lat} | {lat_ascii} | {lat_ye}".strip()
    return s, search_blob


# ---------------------------------------------------------------------------
# 3. Product Normalization (raw -> product_canonical & product_base)
# ---------------------------------------------------------------------------
# Hand-verified mapping from (Код ТН ВЭД, keywords) to canonical product_base (41 families)
HS_TO_PRODUCT_BASE: dict[str, str] = {
    "100199": "пшеница",
    "100630": "рис",
    "110100": "мука пшеничная",
    "151219": "масло подсолнечное",
    "151229": "масло хлопковое",
    "170199": "сахар",
    "220190": "вода питьевая",
    "230610": "шрот хлопковый",
    "250100": "соль пищевая",
    "252020": "гипс строительный",
    "252210": "известь",
    "252329": "портландцемент",
    "271012": "бензин автомобильный",
    "271112": "газ сжиженный",
    "271320": "битум нефтяной",
    "280421": "аргон газообразный",
    "280430": "азот",
    "280700": "кислота серная",
    "281511": "сода каустическая",
    "310210": "карбамид",
    "310230": "селитра аммиачная",
    "320810": "эмаль",
    "390120": "полиэтилен",
    "390210": "полипропилен",
    "390410": "поливинилхлорид",
    "391721": "трубы полиэтиленовые",
    "440711": "пиломатериалы",
    "520100": "хлопок-сырец",
    "520512": "пряжа хлопчатобумажная",
    "690410": "кирпич керамический",
    "700529": "стекло листовое",
    "720853": "лист стальной",
    "721420": "арматура стальная",
    "721710": "проволока арматурная",
    "740311": "катод медный",
    "760421": "профиль алюминиевый",
    "848210": "подшипник",
    "854449": "кабель силовой",
}


def resolve_product_base(raw_name: str, hs_code: str = "") -> str:
    """Map any raw product name (and optional HS code) to its canonical commodity family (`product_base`)."""
    low = raw_name.lower().replace("ё", "е").strip()

    # Special multi-commodity HS codes:
    # 271019 has both дизельное топливо and мазут
    if "мазут" in low:
        return "мазут"
    if "дизел" in low:
        return "дизельное топливо"
    # 230990 has комбикорм and кормосмесь
    if "комбикорм" in low:
        return "комбикорм"
    if "кормосмес" in low:
        return "кормосмесь"

    if hs_code and hs_code in HS_TO_PRODUCT_BASE:
        return HS_TO_PRODUCT_BASE[hs_code]

    # Fallback keyword matching when hs_code is not provided (e.g. user query lookup)
    keyword_rules = [
        ("хлопок-сырец", "хлопок-сырец"),
        ("хлопок сырец", "хлопок-сырец"),
        ("хлопковое масло", "масло хлопковое"),
        ("масло хлопковое", "масло хлопковое"),
        ("подсолнечное масло", "масло подсолнечное"),
        ("масло подсолнечное", "масло подсолнечное"),
        ("шрот хлопков", "шрот хлопковый"),
        ("пряжа хлоп", "пряжа хлопчатобумажная"),
        ("мука", "мука пшеничная"),
        ("пшеница", "пшеница"),
        ("рис", "рис"),
        ("сахар", "сахар"),
        ("соль", "соль пищевая"),
        ("вода питьев", "вода питьевая"),
        ("питьевая вода", "вода питьевая"),
        ("портландцемент", "портландцемент"),
        ("цемент", "портландцемент"),
        ("бензин", "бензин автомобильный"),
        ("газ сжижен", "газ сжиженный"),
        ("сжиженный газ", "газ сжиженный"),
        ("сжиженный углеводородный газ", "газ сжиженный"),
        ("битум", "битум нефтяной"),
        ("гипс", "гипс строительный"),
        ("известь", "известь"),
        ("аргон", "аргон газообразный"),
        ("азот", "азот"),
        ("серная кислота", "кислота серная"),
        ("кислота серная", "кислота серная"),
        ("сода каустическая", "сода каустическая"),
        ("карбамид", "карбамид"),
        ("мочевина", "карбамид"),
        ("селитра аммиачная", "селитра аммиачная"),
        ("аммиачная селитра", "селитра аммиачная"),
        ("эмаль", "эмаль"),
        ("труба пэ", "трубы полиэтиленовые"),
        ("трубы полиэтилен", "трубы полиэтиленовые"),
        ("полиэтилен", "полиэтилен"),
        ("полипропилен", "полипропилен"),
        ("поливинилхлорид", "поливинилхлорид"),
        ("пиломатериал", "пиломатериалы"),
        ("кирпич", "кирпич керамический"),
        ("стекло", "стекло листовое"),
        ("лист стальной", "лист стальной"),
        ("проволока арматурная", "проволока арматурная"),
        ("арматура", "арматура стальная"),
        ("катод медный", "катод медный"),
        ("медный катод", "катод медный"),
        ("профиль алюминиев", "профиль алюминиевый"),
        ("алюминиевый профиль", "профиль алюминиевый"),
        ("подшипник", "подшипник"),
        ("кабель силовой", "кабель силовой"),
    ]
    for kw, base in keyword_rules:
        if kw in low:
            return base
    return low


def normalize_product_canonical(raw_name: str) -> str:
    """Rule-based cleanup of raw product names (collapses ~230 seller strings into ~95 canonical forms)."""
    s = raw_name.lower().replace("ё", "е").strip()
    s = re.sub(r"\s+", " ", s)

    # Unify word order / synonyms at the start
    s = re.sub(r"^хлопок\s+сырец\b", "хлопок-сырец", s)
    s = re.sub(r"^хлопковое\s+масло\b", "масло хлопковое", s)
    s = re.sub(r"^подсолнечное\s+масло\b", "масло подсолнечное", s)
    s = re.sub(r"^питьевая\s+вода\b", "вода питьевая", s)
    s = re.sub(r"^топливо\s+дизельное\b", "дизельное топливо", s)
    s = re.sub(r"^серная\s+кислота\b", "кислота серная", s)
    s = re.sub(r"^селитра\s+аммиачная\b", "аммиачная селитра", s)
    s = re.sub(r"^медный\s+катод\b", "катод медный", s)
    s = re.sub(r"^алюминиевый\s+профиль\b", "профиль алюминиевый", s)
    s = re.sub(r"^цемент\s+портландский\b", "портландцемент", s)
    s = re.sub(r"^труба\s+пэ\b", "трубы полиэтиленовые", s)
    s = re.sub(r"^мочевина\s*\(карбамид\)", "карбамид", s)

    # Unify sort spellings: первый/1-сорта/I сорт -> 1 сорт, второй/2-сорта/II сорт -> 2 сорт, etc.
    s = re.sub(r"\bвысшего\s+сорта\b", "высший сорт", s)
    s = re.sub(r"\bпервый\s+сорт\b", "1 сорт", s)
    s = re.sub(r"\bвторой\s+сорт\b", "2 сорт", s)
    s = re.sub(r"\bтретий\s+сорт\b", "3 сорт", s)
    s = re.sub(r"\biii\s*[-]?\s*сорта?\b", "3 сорт", s)
    s = re.sub(r"\bii\s*[-]?\s*сорта?\b", "2 сорт", s)
    s = re.sub(r"\bi\s*[-]?\s*сорта?\b", "1 сорт", s)
    s = re.sub(r"\b([123])\s*-\s*сорта?\b", r"\1 сорт", s)
    s = re.sub(r"\b([123])\s+сорта\b", r"\1 сорт", s)

    # Unify class spellings: I класс / 1-класс -> 1 класс, II класс / 2-класс -> 2 класс
    s = re.sub(r"\biv\s*[-]?\s*класса?\b", "4 класс", s)
    s = re.sub(r"\biii\s*[-]?\s*класса?\b", "3 класс", s)
    s = re.sub(r"\bii\s*[-]?\s*класса?\b", "2 класс", s)
    s = re.sub(r"\bi\s*[-]?\s*класса?\b", "1 класс", s)
    s = re.sub(r"\b([1234])\s*-\s*класса?\b", r"\1 класс", s)

    # Unify spacing in dimensions and grades (e.g. 32,5 н -> 32,5н, ø 110 мм -> ø110мм)
    s = re.sub(r"(\d+),(\d+)\s+н\b", r"\1,\2н", s)
    s = re.sub(r"ø\s*(\d+)\s*мм", r"ø\1мм", s)
    s = re.sub(r"(\d+)\s*мм\b", r"\1мм", s)

    # Fix duplicate word in raw data: "пиломатериал обрезной обрезные хвойных пород"
    s = s.replace("обрезной обрезные", "обрезной")

    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_sort(raw_sort: str, raw_product_name: str = "") -> str:
    """Normalize sort field into a clean value ('высший сорт', '1 сорт', '2 сорт', '3 сорт', or '')."""
    combined = f"{raw_sort} {raw_product_name}".lower().replace("ё", "е")
    if "высш" in combined or re.search(r"\bв/с\b", combined):
        return "высший сорт"
    if re.search(r"\b(первый\s+сорт|1\s*[-]?\s*сорта?|i\s*[-]?\s*сорта?)\b", combined):
        return "1 сорт"
    if re.search(r"\b(второй\s+сорт|2\s*[-]?\s*сорта?|ii\s*[-]?\s*сорта?)\b", combined):
        return "2 сорт"
    if re.search(r"\b(третий\s+сорт|3\s*[-]?\s*сорта?|iii\s*[-]?\s*сорта?)\b", combined):
        return "3 сорт"
    return raw_sort.strip().lower()


def normalize_class(raw_class: str, raw_product_name: str = "") -> str:
    """Normalize class field into ('1 класс', '2 класс', '3 класс', '4 класс', 'а500с', or '')."""
    combined = f"{raw_class} {raw_product_name}".lower().replace("ё", "е")
    if "а500с" in combined or "a500c" in combined:
        return "А500С"
    if re.search(r"\b(4\s*[-]?\s*класса?|iv\s*[-]?\s*класса?)\b", combined):
        return "4 класс"
    if re.search(r"\b(3\s*[-]?\s*класса?|iii\s*[-]?\s*класса?)\b", combined):
        return "3 класс"
    if re.search(r"\b(2\s*[-]?\s*класса?|ii\s*[-]?\s*класса?)\b", combined):
        return "2 класс"
    if re.search(r"\b(1\s*[-]?\s*класса?|i\s*[-]?\s*класса?)\b", combined):
        return "1 класс"
    return raw_class.strip()

