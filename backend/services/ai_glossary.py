"""
ai_glossary.py
==============
Optical-shop domain glossary for all three supported languages:
  - English
  - Hindi (romanized + Devanagari keywords)
  - Gujarati (romanized + native script keywords)

The glossary is injected into the LLM system prompt so the model
understands trade-specific terminology even when spoken/typed in
the local language.
"""

# ---------------------------------------------------------------------------
# Romanized Gujarati  →  English intent
# ---------------------------------------------------------------------------
GUJARATI_ROMAN_TO_ENGLISH = {
    # Actions
    "umarao": "add",
    "banavo": "create / make",
    "juo": "view / show",
    "shodhao": "search / find",
    "badlao": "edit / update",
    "kado": "delete / remove",
    "dikhao": "show",

    # Entities
    "grahak": "customer",
    "kharidar": "customer / buyer",
    "order": "order",
    "bill": "bill / order",
    "chasma": "spectacles / glasses",
    "frame": "frame",
    "lens": "lens",
    "goggles": "sunglasses",
    "stock": "inventory / stock",
    "maal": "goods / inventory",
    "davar": "inventory item",

    # Prescription / eye terms
    "aankh": "eye",
    "number": "prescription / power",
    "nazar": "vision / prescription",
    "daawa": "right (side)",
    "dava": "right eye",
    "daba": "left (side)",
    "dabi": "left eye",

    # Payment
    "paisaa": "money / amount",
    "advance": "advance payment",
    "baaki": "balance / due",
    "bakshi": "discount",
    "total": "total",

    # Status / misc
    "pending": "pending",
    "ready": "ready",
    "deliver": "delivered",
    "aaje": "today",
    "su che": "what is",
    "ketla": "how many",
    "navu": "new",
    "vivo": "more",
    "bhai": "brother (informal address for any male)",
    "ben": "sister (informal address for any female)",
}

# ---------------------------------------------------------------------------
# Romanized Hindi  →  English intent
# ---------------------------------------------------------------------------
HINDI_ROMAN_TO_ENGLISH = {
    # Actions
    "banao": "create / make",
    "umarao": "add",
    "dekho": "view / show",
    "dikhao": "show",
    "dhundho": "search / find",
    "badlo": "edit / update",
    "hato": "delete / remove",

    # Entities
    "grahak": "customer",
    "customer": "customer",
    "order": "order",
    "bill": "bill / order",
    "chashma": "spectacles / glasses",
    "frame": "frame",
    "lens": "lens",

    # Prescription
    "aankh": "eye",
    "number": "prescription / power",
    "nazar": "vision / prescription",
    "daaya": "right",
    "baaya": "left",

    # Payment
    "paisa": "money / amount",
    "advance": "advance payment",
    "baaki": "balance / due",
    "chhoot": "discount",
    "total": "total",

    # Status / misc
    "pending": "pending",
    "ready": "ready",
    "delivered": "delivered",
    "aaj": "today",
    "kitne": "how many",
    "naya": "new",
    "bhai": "brother (informal address)",
    "didi": "elder sister (informal)",
    "mera": "my",
    "uska": "his/her",
    "ka": "of / for (genitive marker)",
    "ke liye": "for",
}

# ---------------------------------------------------------------------------
# Common optical prescription terminology (same across languages)
# ---------------------------------------------------------------------------
OPTICAL_TERMS = {
    "SPH": "sphere power (+ for far-sighted, - for near-sighted, range -20 to +20)",
    "CYL": "cylinder power (astigmatism correction, range -10 to +10)",
    "AXIS": "axis angle of astigmatism (0 to 180 degrees)",
    "ADD": "near-vision addition power (+0.50 to +4.00)",
    "RE": "right eye (OD in Latin notation)",
    "LE": "left eye (OS in Latin notation)",
    "OD": "right eye (oculus dexter)",
    "OS": "left eye (oculus sinister)",
    "VA": "visual acuity",
    "PD": "pupillary distance",
}

# ---------------------------------------------------------------------------
# System prompt fragment injected into LLM context
# ---------------------------------------------------------------------------
MULTILINGUAL_GLOSSARY_PROMPT = """
LANGUAGE SUPPORT:
You understand English, Hindi, and Gujarati — including romanized (Latin-script) versions of both.

COMMON ROMANIZED GUJARATI PHRASES (with English meaning):
- "navu customer umarao" → add new customer
- "chasma nu bill banavo" → create a glasses/spectacles order
- "stock maan su che?" → what is in stock? / check inventory
- "aankh no number nakhvo" → enter eye prescription
- "Rajesh bhai no order" → Rajesh's order
- "aaje ketla order aavya?" → how many orders came today?
- "advance levo" → collect advance payment
- "baaki ketlu che?" → how much balance is remaining?

COMMON ROMANIZED HINDI PHRASES:
- "naya customer banao" → create new customer
- "chashma ka bill banao" → create spectacles order
- "stock dekho" → check inventory
- "aankh ka number daalo" → enter eye prescription
- "Ramesh bhai ka order" → Ramesh's order
- "aaj kitne order aaye?" → how many orders today?
- "advance lo" → collect advance
- "baaki kitna hai?" → how much balance?

RULES FOR DATA ENTRY:
1. ALL data written to database must be in English (names, descriptions, notes).
2. Respond to the user in the SAME language/style they used.
3. Translate Gujarati/Hindi input to English before calling any tool.
4. "Rajesh bhai" → customer name = "Rajesh" (drop bhai/ben/ji honorifics for DB).
5. Prices spoken as words ("fifteen hundred") → convert to number (1500).
6. Dates like "kal" (tomorrow), "aaje" (today) → compute actual YYYY-MM-DD.
"""
