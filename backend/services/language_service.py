"""
language_service.py
===================
Detects the language/script of user input and provides
normalisation helpers so the AI service and tools can work
with clean, English-ready text.

Supported detection categories:
  'en'            – English
  'hi'            – Hindi (Devanagari script)
  'gu'            – Gujarati (Gujarati script)
  'romanized_hi'  – Hindi written in Latin characters (Hinglish)
  'romanized_gu'  – Gujarati written in Latin characters (Gujlish)
  'mixed'         – Mixture where dominant language is unclear
"""

import re
import unicodedata
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unicode block ranges
# ---------------------------------------------------------------------------
DEVANAGARI_RANGE = (0x0900, 0x097F)   # Hindi / Sanskrit / Marathi …
GUJARATI_RANGE   = (0x0A80, 0x0AFF)   # Gujarati script

# ---------------------------------------------------------------------------
# Small vocabulary lists to help classify romanized Indic text.
# Words that strongly indicate Gujarati-origin romanized text.
# ---------------------------------------------------------------------------
GUJARATI_ROMAN_MARKERS = {
    "umarao", "banavo", "juo", "ketla", "aaje", "navu", "chasma",
    "grahak", "kharidar", "baaki", "bakshi", "maal", "davar",
    "aankh", "daawa", "daba", "su", "che", "bhai", "ben",
    "paisaa", "dukan",
}

# Words that strongly indicate Hindi-origin romanized text.
HINDI_ROMAN_MARKERS = {
    "banao", "dekho", "dhundho", "badlo", "hato", "chashma",
    "nazar", "daaya", "baaya", "paisa", "chhoot", "mera",
    "uska", "naya", "aaj", "kitne", "didi", "karo", "karein",
    "dikhao", "chahiye", "hain", "hai",
}

# English stop words that we *ignore* during language classification
# (they appear frequently in code-switched utterances).
IGNORE_TOKENS = {
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "but",
    "for", "in", "of", "to", "from", "with", "on", "at", "by", "do",
    "not", "no", "yes", "ok", "okay", "please", "can", "i", "my",
    "order", "orders", "bill", "bills", "customer", "customers",
    "stock", "inventory", "advance", "total", "balance", "discount",
    "name", "phone", "number", "price", "quantity", "date",
}


def _count_script_chars(text: str, lo: int, hi: int) -> int:
    """Count characters in text that fall within a Unicode block range."""
    return sum(1 for ch in text if lo <= ord(ch) <= hi)


def detect_language(text: str) -> str:
    """
    Returns one of: 'en', 'hi', 'gu', 'romanized_hi', 'romanized_gu', 'mixed'.

    Strategy:
    1. Check for native script characters.
    2. If all Latin, tokenise and score against Gujarati / Hindi marker sets.
    3. Return the dominant category.
    """
    if not text or not text.strip():
        return "en"

    text_clean = text.strip()

    # --- Step 1: Native script detection ---
    n_devanagari = _count_script_chars(text_clean, *DEVANAGARI_RANGE)
    n_gujarati   = _count_script_chars(text_clean, *GUJARATI_RANGE)
    n_latin      = sum(1 for ch in text_clean if ch.isascii() and ch.isalpha())

    total_alpha = n_devanagari + n_gujarati + n_latin + 1  # avoid div/0

    if n_gujarati / total_alpha > 0.15:
        return "gu"
    if n_devanagari / total_alpha > 0.15:
        return "hi"

    # --- Step 2: Romanized detection ---
    tokens = set(re.findall(r"[a-zA-Z']+", text_clean.lower())) - IGNORE_TOKENS

    gu_hits = len(tokens & GUJARATI_ROMAN_MARKERS)
    hi_hits = len(tokens & HINDI_ROMAN_MARKERS)

    if gu_hits == 0 and hi_hits == 0:
        return "en"

    if gu_hits > hi_hits:
        return "romanized_gu"
    if hi_hits > gu_hits:
        return "romanized_hi"

    # Equal hits → likely mixed / code-switched
    return "mixed"


def extract_english_name(raw_name: str) -> str:
    """
    Strip honorifics commonly added in Hindi/Gujarati speech:
    bhai, ben, ji, sir, madam, didi, saheb, bhen, shree, shri
    Example: "Ramesh bhai" → "Ramesh"
    """
    honorifics = r"\b(bhai|ben|bhen|ji|sir|madam|saheb|sahib|didi|shri|shree|shriman)\b"
    clean = re.sub(honorifics, "", raw_name, flags=re.IGNORECASE).strip()
    # Collapse multiple spaces
    clean = re.sub(r"\s+", " ", clean)
    return clean or raw_name  # fallback to original if nothing remains


def words_to_number(text: str) -> float | None:
    """
    Convert spoken price to float.
    Handles: "fifteen hundred" → 1500, "do hajar" → 2000, "teen sau" → 300
    Returns None if no conversion possible.
    """
    mappings = {
        # English
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
        "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
        "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90,
        "hundred": 100, "thousand": 1000, "lakh": 100000,

        # Hindi romanized
        "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5,
        "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
        "bees": 20, "tees": 30, "chalis": 40, "pachaas": 50,
        "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90,
        "sau": 100, "hajar": 1000,

        # Gujarati romanized (overlap with Hindi intentional)
        "ek": 1, "be": 2, "tran": 3, "char": 4, "pach": 5,
        "chha": 6, "saat": 7, "aath": 8, "nav": 9, "das": 10,
        "vis": 20, "tris": 30, "chalees": 40, "pachas": 50,
        "sao": 100, "hazar": 1000,
    }

    tokens = text.lower().split()
    result = 0.0
    current = 0.0
    matched = False

    for t in tokens:
        t_clean = re.sub(r"[^a-z]", "", t)
        if t_clean in mappings:
            val = mappings[t_clean]
            matched = True
            if val == 100:
                current = (current if current else 1) * 100
            elif val >= 1000:
                result += (current if current else 1) * val
                current = 0.0
            else:
                current += val

    if matched:
        result += current
        return result
    return None


def language_label_to_human(code: str) -> str:
    """Convert internal language code to human-readable label."""
    labels = {
        "en": "English",
        "hi": "Hindi",
        "gu": "Gujarati",
        "romanized_hi": "Hinglish",
        "romanized_gu": "Gujarati (romanized)",
        "mixed": "Mixed",
    }
    return labels.get(code, "Unknown")
