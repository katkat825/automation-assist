"""
Data tables for the static QA checks.

This module holds *only* data: word lists, terminology pairs, compiled
regexes and thresholds. It contains no logic and imports nothing from the
rest of the package, so it can be edited safely without touching any check.

Roughly 640 lines of the original checks.py were tables like these
interleaved between function definitions. Keeping them here means a
vocabulary tweak never requires reading around a check implementation.

All word sets are lowercase; matching is case-insensitive at the call site.
"""

import re



# ==========================================================================
# Terminology pairs
# ==========================================================================

# Default pairs to check: (canonical form, list of variant spellings)
_TERM_PAIRS = [
    ("email",           ["e-mail"]),
    ("log in",          ["login", "log-in"]),
    ("online",          ["on-line", "on line"]),
    ("web site",        ["website"]),
    ("username",        ["user name", "user-name"]),
    ("internet",        ["Internet"]),   # capitalisation
    ("Wi-Fi",           ["wifi", "Wifi", "wi-fi", "WiFi"]),
    ("cybersecurity",   ["cyber security", "cyber-security", "cyberSecurity", "CyberSecurity"]),
]


# ==========================================================================
# Duplicate / doubled-word ignore lists
# ==========================================================================

# Words to completely skip when checking for doubled words.
# Add any word here that legitimately appears twice in close proximity
# (e.g. UI button labels, field names, instructional phrases).
# All entries should be lowercase — matching is case-insensitive.
_DOUBLED_WORD_IGNORE: set[str] = {
    "submit",
    "start",
    "passwords",
    "information",
    "data",
}

# Slide titles that are expected to repeat across screens — skip these
_IGNORE_DUP_TITLE = re.compile(
    r"^(?:"
    r"introduction$"
    r"|question\s+\S"
    r"|Best Practice"
    r"|Pre-Test Question"
    r"|popup"
    r")",
    re.IGNORECASE,
)
# Sentence/paragraph prefixes that are expected to repeat across screens — skip these
_IGNORE_DUP_SENTENCE = re.compile(
    r"^(?:"
    r"Select each"
    r"|Select\s+[\u201c\u201d\u2018\u2019\"']"  # Select "X" to …
    r"|Pre-Test:\s*Answer"
    r"|NOTE:\s*This question was automatically"
    r"|Which of the following statements"
    r"|Congratulations, you passed the pre-test"
    r"|Click the arrow to"
    r"|After skipping, you can always view"
    r"|Because you answered this question incorrectly"
    r"|You chose the correct"
    r"|You chose an incorrect"
    r"|The pre-test is optional"
    r"|You may want to review"
    r"|Consider revisiting"
    r"|This question has more than one"
    r"|: When the correct answer is"
    r"|button to answer"
    r"|button to simply take the module"
    r"|Now let[\u2019']s discuss what makes"
    r")",
    re.IGNORECASE,
)

# Phrases that — if found *anywhere* in a paragraph — mark it as expected
# boilerplate and exempt it from duplicate detection.
# Use re.search (not re.match) so the term can appear mid-sentence.
_IGNORE_DUP_SENTENCE_CONTAINS = re.compile(
    r"(?:"
    r"pre.?test"  # "pre-test", "pretest", "pre test" etc.
    r"|discuss what makes"
    r")",
    re.IGNORECASE,
)


# ==========================================================================
# Spell-check ignore list
# ==========================================================================

# ---------------------------------------------------------------------------
# Spell-check ignore list
#
# Add words here that the spell checker flags incorrectly.
# Organised by category so it's easy to find and extend.
# All entries should be lowercase — the checker normalises before lookup.
# ---------------------------------------------------------------------------

_SPELL_IGNORE: set[str] = set()

# --- eLearning / SCORM platform terms ---
_SPELL_IGNORE.update({
    "scorm", "xapi", "aicc", "cmi",
    "lms", "lcms",
    "elearning", "storyline", "articulate",
    "pretest", "pretests", "retest", "relaunch",
    "learner", "learners",
    "seekbar", "playback", "dropdown", "dropdowns",
    "checkbox", "checkboxes",
    "onclick", "iframe", "gamestore", "checkmarks",
    "menuprogress", "playervars", "passpercent",
    "percentscore", "phishinggame", "sharingdata",
    "resetaccount"
})

# --- General tech / dev terms ---
# Add any common programming or software terms that get flagged.
_SPELL_IGNORE.update({
    "html", "css", "javascript", "js", "typescript", "jsx", "tsx",
    "url", "urls", "uri", "http", "https",
    "pdf", "pdfs", "xml", "json", "yaml", "csv",
    "api", "apis", "sdk", "ide", "cli", "gui", "ui", "ux",
    "frontend", "backend", "fullstack", "middleware",
    "boolean", "bool", "nullable", "enum", "async", "struct",
    "regex", "refactor", "refactoring", "deduplication",
    "codebase", "repo", "repos", "devops", "cicd",
    "dropdown", "tooltip", "sidebar", "navbar", "modal",
    "metadata", "dataset", "datasets", "timestamp", "timestamps",
    "screenshot", "screenshots", "screencap",
    "checkbox", "checkboxes", "textarea",
    "npm", "webpack", "minified",
    "app", "apps", "infosec", "noreply",
    "firstname", "lastname", "comms", "admin",
    "docx", "php", "popup",
    "checkmark", "multifactor", "helpdesk",
    "inbox", "personalemail", "admins", "bot", 
    "superuser", "tmp", "cisos", "csirts", 
})

# --- Networking & protocols ---
# Add subnets, port names, protocol names, etc. as needed.
_SPELL_IGNORE.update({
    "tcp", "udp", "icmp", "ip", "ipv4", "ipv6",
    "dns", "dnssec", "dhcp",
    "smtp", "imap", "ftp", "sftp", "ssh",
    "nat", "vlan", "vpn", "vpns",
    "subnet", "subnetting", "subnets",
    "firewall", "firewalls",
    "router", "routers", "gateway",
    "localhost", "hostname", "hostnames",
    "osi", "dmz", "www", "telecom",
})

# --- Cybersecurity terms ---
# Common security terminology that isn't in standard dictionaries.
# Add course-specific threat names, CVE references, tool names, etc. here.
_SPELL_IGNORE.update({
    # Threat types
    "ddos", "dos", "botnet", "botnets",
    "ransomware", "malware", "spyware", "adware",
    "rootkit", "rootkits", "keylogger", "keyloggers",
    "trojan", "trojans", "worm", "worms",
    "phishing", "spearphishing", "smishing", "vishing",
    "backdoor", "backdoors", "deepfake", "deepfakes",
    "exploit", "exploits", "exploiting",
    "payload", "payloads", "pretexting",
    "brute", "bruteforce",
    # Attack techniques / acronyms
    "xss", "csrf", "ssrf", "sqli", "idor",
    "mitm", "arp",
    "osint",
    # Defensive / compliance
    "siem", "soc", "noc",
    "mfa", "sso", "rbac", "abac", "iam",
    "owasp", "nist", "cis", "gdpr", "hipaa", "pci",
    "apt", "ioc", "iocs", "ttp", "ttps",
    "pentest", "pentesting", "pentester", "pentesters",
    "vuln", "vulns", "vulnerability", "vulnerabilities",
    "remediation", "hardening",
    "pii",
    # Crypto & auth
    "oauth", "jwt", "saml", "ldap",
    "tls", "ssl", "logins",
    "sha", "md5", "aes", "rsa",
    "pki", "certs", "cert",
    "hash", "hashing", "hashed",
    "encrypt", "encrypts", "encrypted", "encryption",
    "decrypt", "decrypts", "decrypted", "decryption",
    "unencrypted",
    # Cloud / infrastructure
    "saas", "paas", "iaas", "faas",
    "kubernetes", "docker", "containerized", "microservice", "microservices",
    "serverless", "autoscaling",
    "sso",
    # To Be Categorised
    "cyber", "cyberattack", "cyberattacks",
    "cybercrime", "cybercriminal", "cybercriminals",
    "cybersecurity", "hacktivists", "quishing",
    "robocalls", "scammers", "cybercrimes",
    "failsafe", "cyberdefense", 
})

# --- Brand Names ---
# Add any brand names
_SPELL_IGNORE.update({
    "docusign", "sharepoint", "gmail", "onedrive", "quickbooks",
    "netflixalerts", "lincpass", "msupdate",
    "acmecorp", "adlearn", "bitcoin", "equifax",
    "dhl", "fedex", "ups", "usps", "github",
    "maersk", "solarwinds", "spidersilk", "unitedhealth",
    "sarbanes", "fedramp", "oxley",
})

# --- People Names and Email names ---
_SPELL_IGNORE.update({
    "alina", "briannamoseley", "briansmith",
    "chrisjones", "emmamorrison", "jackdoe",
    "janedoe", "janesmith", "johndoe",
    "lorihill", "marydoe", "minh",
    "nelia", "neliarichards", "pauldaniels",
    "leela", "lehana", "adeyemo", "yellin",
})


# --- Other - org acronyms and common false positives ---
_SPELL_IGNORE.update({
    "aarp", "usda", "org", "isn", "doesn", "etc",
    "aaaa", "abcd", "reputational", "lifecycle",
    "faqs",
})

#------ Real Words ----------
_SPELL_IGNORE.update({
    "barcode", "cafes", "contactless",
    "coworking", "foodborne", "iloveyou",
    "passcode", "passcodes", "sanitization",
    "smartphone", "smartphones", "voicemail",
    "pre", "quo", "pcs", "dii", "hardcode", "hardcoded",
    "hardcoding", "recertified", "multi",
    "oversharing", "roadmap", "timeline", "timelines",
    "recoverability", "telework", "timeframe", "timeframes",
    "interagency", "auditable", "remediate", "didn", # doesn't recognize contractions
    "atms", "atm",
})


# ==========================================================================
# Terms that stay English in translations
# ==========================================================================

# ---------------------------------------------------------------------------
# Non-English courses: terms that legitimately stay in English even when the
# rest of the course is translated. Used by check_untranslated_english to
# avoid flagging brand names, acronyms, and loanwords that are accepted in
# the target language.
#
# Add words here the same way you add to _SPELL_IGNORE — lowercase, grouped
# by category for readability. Matching is case-insensitive.
# ---------------------------------------------------------------------------

_ENGLISH_OK_TERMS: set[str] = set()

# --- Acronyms / abbreviations that stay in English internationally ---
_ENGLISH_OK_TERMS.update({
    "api", "url", "urls", "uri", "http", "https",
    "html", "css", "js", "json", "xml", "pdf",
    "ip", "tcp", "udp", "dns", "vpn", "wifi",
    "ok", "id", "ids", "ui", "ux",
    "mfa", "sso", "vpn", "lms", "scorm",
    "ceo", "cfo", "cio", "ciso", "cto", "hr", "it",
    "usa", "us", "uk", "eu", "un",
    "am", "pm", "etc", "vpn",
})

# --- Loanwords commonly kept in English across many languages ---
_ENGLISH_OK_TERMS.update({
    "email", "internet", "online", "offline",
    "password", "username", "login",
    "software", "hardware", "download", "upload",
    "click", "ok", "cancel",
    "phishing", "smishing", "vishing", "ransomware",
    "malware", "spam", "hacker", "hackers",
    "smartphone", "smartphones", "laptop", "laptops",
    "wifi", "bluetooth", "wi-fi",
    "ok", "okay", "solo", "fines", "final",
    "dragon", "qwerty", "princess", "gateway",
    "virtual", "private", "network", "firmware",
    "gateway", "router", "routers",

    # French (fr-CA)
    "identifier", "signaler", "module", "base", "information",
    "menace", "assurer", "quatre", "questions", "points",
    "attention", "rapport", "clients", "millions", "protection",
    "modification", "destruction", "types", "motivations", "amateurs",
    "sensations", "fortes", "intention", "argent", "vengeance",
    "suppression", "classification", "profession", "plans", "voyage",
    "routine", "amis", "documents", "armoire", "fort",
    "aide", "dossiers", "mots", "rapports", "financiers",
    "identification", "carte", "petit", "probable", "brochures",
    "pages", "public", "internes", "tels", "place",
    "objet", "message", "faux", "principe", "travail",
    "commenter", "bonne", "permission", "donner", "option",
    "continuer", "large", "chances", "liens", "demander",
    "connecter", "dossier", "pendant", "discussion", "modifier",
    "cadre", "formation", "comment", "important", "prudent",
    "photos", "absent", "publication", "prudence", "conservation",
    "techniques", "effacer", "purger", "supports", "standard",
    "effacement", "blocs", "physique", "aura", "type",
    "doit", "exigences", "documentation", "service", "possible",
    "nous", "habitude", "alimentation", "applications", "causer",
    "application", "code", "simple", "unique", "portables",
    "transporter", "temps", "causes", "rend", "aider",
    "font", "articles", "pirates", "excellent", "bravo",
    "longueur", "forts", "long", "brut", "souvenir",
    "danger", "services", "faille", "usage", "sous",
    "forme", "bases", "device", "authentication",
    "feature", "compromise", "accounts", "fedex",
})

# --- Brand / product names ---
_ENGLISH_OK_TERMS.update({
    "microsoft", "outlook", "office", "windows",
    "google", "chrome", "gmail", "youtube",
    "apple", "iphone", "ipad", "mac",
    "facebook", "instagram", "twitter", "linkedin",
    "zoom", "teams", "slack",
    "docusign", "sharepoint", "onedrive",
    "amazon", "netflix", "github",
    "android", "ios", "claude", "gemini", "copilot",
    "walmart", "word", "bloc", "page",
})

_ENGLISH_OK_TERMS.update({
    # common names
    "alice", "dana", "mark",
    "john", "mary", "will", "luke",
    "sarah", "trent",
    "michael", "smith",

    # companies / brands / orgs
    "acme", "biogen", "solutions",
    "paypal", "ferrari",
    "corporation",
    "community", "action", "council",
    "quanta", "computer",

    # locations / regions
    "timor", "leste",
    "lexington", "kentucky",
    "italia", "oriente", "canada",

    # cybersecurity / business terminology
    "threat", "intelligence",
    "disrupting", "ongoing", "operations",
    "security", "actors", "using",
    "legitimate",

    # misc english terms intentionally retained
    "help", "bueno",
    "seaborgium",
    "euros",
})


# ==========================================================================
# Untranslated-English detection
# ==========================================================================

# Matches a whole ASCII-letter word of 4+ characters for the untranslated-
# English check. The \b anchors are Unicode-aware in Python's re, so an
# ASCII run inside an accented word (e.g. "inform" inside "información")
# is NOT matched — "m"→"ó" is letter→letter with no word boundary.
# Minimum length 4 avoids common 2–3 letter foreign words ("de", "la",
# "ag", "pr", "el", "es") that look English by chance.
_ENGLISH_WORD_RE = re.compile(r"\b[a-zA-Z]{4,}\b")


# Words that appear in pyspellchecker's English dictionary but are common
# in Romance-language courses and would otherwise be flagged as English.
# Add to this set when you spot a false positive in a non-English course
# — same edit-the-file workflow as _SPELL_IGNORE / _ENGLISH_OK_TERMS.
_NON_ENGLISH_FALSE_POSITIVES: set[str] = {
    # Spanish — pronouns, demonstratives, prepositions, common adverbs
    "este", "esta", "esto", "estos", "estas",
    "eso", "esa", "esos", "esas",
    "para", "pero", "porque", "como", "donde", "cuando",
    "poco", "pocos", "poca", "pocas",
    "mucho", "mucha", "muchos", "muchas",
    "todo", "toda", "todos", "todas",
    "cada", "caba", "cabo", "tanto", "tanta",
    "casi", "cosa", "cosas", "casa",
    "sobre", "ante", "tras", "hasta", "desde",
    "aqui", "alli", "alla", "ahora", "antes",
    "hace", "haga", "haya", "hizo",
    "ser", "soy", "fue", "fueron",
    "uno", "una", "unos", "unas",
    "otro", "otra", "otros", "otras",
    "mismo", "misma", "mismos", "mismas",
    "siempre", "nunca", "tambien",
    # French — articles, prepositions, common short words
    "avec", "sans", "pour", "dans", "vers", "chez",
    "donc", "alors", "ainsi", "puis",
    "plus", "moins", "tres", "trop", "bien",
    "tout", "tous", "toute", "toutes",
    "mais", "mes", "ses", "ces", "nos", "vos",
    "ainsi", "aussi", "encore", "deja",
    "etre", "avoir", "planes", "mark", "personas",
    "clave", "enlaces", "control", "evita", 
    "inferior", "superior", "principal", 
        # common spanish/french words that pyspellchecker thinks are english
    "solo", "fines", "final", "fiscal",
    "social", "similar", "manual", "local", "personal",
    "vida", "real", "idea", "error", "total",
    "contra", "embargo", "canal", "director",
    "audio", "video", "campus", "gran",
    "mayo", "primero", "durante", "ambos",
    "accede", "responder", "enlace",
    "amigo", "amigos",
    "breve", "segundo",
    "facial", "sensor", "fundamental", "tome",
    "verse", "ella", "llama",
    "largos", "prompts", "jefe", "dato", "sensible",
    "miles",
    "alcalde", "australia", "culpable",
    "plausible", "sessions", "session", "surveillance",
    "situations", "interface", "administration",
    "marque", "exploiter",
    "options", "mobile",
    "pirate", "lieu",
    "mobiles", "portable", "point", "intelligent",
    "exact", "communication", "moderne",
    "communications", "messages",
    "fraction", "dollars", "million", "perdu",
    "performance", "domaine", "sections",
    "article", "existent",
    "affaire", "tort", "corruption",
    "livre", "comestibles",
    "machines", "montage", "unis", "exigence",
    "protections", "machine", "dangers",
    "exploitation", "marche",
    "presses", "contact", "construction",
    "installation", "invite", "direction",
    "tentation", "critique",
    "anecdote", "source", "consultant", "expert",
    "examiner", "guide", "guides",
    "longs", "taper", "masquer",
    "approbation", "actions", "certain", "budget",
    "agents", "minimum", "agent",
    "lire", "sites", "instructions",
    "commence", "initial", "pause",
    "permissions", "exposer",
    "modifications", "petite", "domino",
    "panne", "engagement", "provocateur",
    "moment", "menaces", "injection",
    "billet", "marketing",
    "conditions", "fret", "ensemble",
    "intervention", "multitude",
    "tests", "proactive", "anomalies",
    "sable", "incident", "passer",
    "essayer", "inciter", "installer",
    "marques", "grand", "livraison",
    "tentative", "part", "usurper",
    "signal", "salutations", "photo",
    "relation", "placement",
    "surtout", "sentiment", "lien", "site",
    "codes", "assistance",
    "urgent", "forcer",
    "usurpation", "abnormal", "gang",
    "fusion", "acquisition",
    "notes", "examinant", "journal",
    "instruction", "ignorer", "analyses",
    "personnel", "facture", "minuscule",
    "financier", "appel", "direct",
    "entrepreneurs", "orient", "environ",
    "prison", "caution", "suite",
    "signet", "locale", "canada",
    "union", "visitant", "interne",
    "court", "questionnaire", "score",
    "changer", "violation", "sacs",
    "rester",
}


# ==========================================================================
# Text-hygiene regexes
# ==========================================================================

# Matches plain alphabetic words of 3+ characters (no digits, no punctuation)
_WORD_RE = re.compile(r"[a-zA-Z]{3,}")

# Matches URLs so they can be stripped before other checks run.
# Covers http/https with any path/query/fragment, and bare www. addresses.
# \S+ is intentionally greedy — phishing URLs often contain junk characters.
_URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)

# Matches email addresses so local-part and domain words aren't spell-checked.
# e.g. "jsmith@hfureion.com" — none of those parts should be flagged.
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', re.IGNORECASE)

# Missing space after punctuation: flags things like "tell?he" or "Right!Click".
# Deliberately excludes period (too many false positives from abbreviations,
# decimals, URLs, etc.) — flag ? ! ; , only.
_PUNCT_NO_SPACE_RE = re.compile(r'[?!;,][A-Za-z]')

# --- Dash style checks -----------------------------------------------------
# GLS house style: an em-dash (—, U+2014) is UNSPACED — no space on
# either side ("word—word").  Flags an em-dash that has a whitespace
# character immediately before or after it (e.g. "word — word",
# "word —word", "word— ").  The lookbehind/lookahead consume nothing
# and the alternation consumes the em-dash exactly once, so a fully-spaced
# "word — word" is reported a single time.  Not flagged: a correctly
# unspaced "word—word", and an em-dash sitting tight against the boundary
# of the text run.
_EMDASH_SPACING_RE = re.compile(r'(?<=\s)—|—(?=\s)')

# Heuristic: a hyphen used where an em-dash is likely intended.  Two strong,
# high-confidence signals:
#   1. A double (or longer) hyphen "--" — the common ASCII stand-in.
#   2. A single ASCII hyphen flanked by spaces between two letters
#      ("word - word") — a spaced hyphen is rarely correct.
_WRONG_DASH_RE = re.compile(
    r'--+'                             # double-or-more hyphen
    r'|(?<=[A-Za-z]) - (?=[A-Za-z])'   # spaced ASCII hyphen between words
)

# An UNSPACED hyphen between two words ("risk-patterns") is the hard case: it
# looks identical to a legitimate compound ("real-world").  Per the agreed
# approach we flag every such pair for review UNLESS it is clearly a real
# compound — i.e. unless the first element is a productive prefix (sub-,
# re-, over-...), the last element is a productive suffix (-based, -level...),
# or the whole token is on the _HYPHEN_COMPOUND_OK allowlist below.  Refine by
# adding confirmed-good compounds to the allowlist, just like _SPELL_IGNORE.
_HYPHEN_PAIR_RE = re.compile(r'\b[A-Za-z]+(?:-[A-Za-z]+)+\b')

# First-element prefixes that form ordinary compounds (never an em-dash).
_HYPHEN_PREFIXES: set[str] = {
    "anti", "bi", "co", "counter", "cross", "de", "ex", "extra", "hyper",
    "inter", "intra", "macro", "mega", "micro", "mid", "mini", "multi", "non",
    "over", "post", "pre", "pro", "pseudo", "re", "self", "semi", "sub",
    "super", "trans", "tri", "ultra", "un", "under", "uni",
}

# Second-element suffix words that form ordinary productive compounds.
_HYPHEN_SUFFIXES: set[str] = {
    "based", "level", "wide", "free", "like", "driven", "oriented", "ready",
    "proof", "aware", "friendly", "related", "specific", "grade", "type",
    "sized", "shaped", "style", "bound", "born", "made", "led",
}

# Confirmed-good compounds — add here to silence false positives.
# Lowercase, matched case-insensitively.
_HYPHEN_COMPOUND_OK: set[str] = {
    "real-world", "real-time", "in-depth", "one-time", "long-term",
    "short-term", "full-time", "part-time", "well-known", "up-to-date",
    "day-to-day", "end-to-end", "state-of-the-art", "decision-making",
    "high-quality", "low-cost", "on-site", "off-site", "opt-in", "opt-out",
    "sign-in", "sign-on", "log-in", "e-mail", "e-learning",
    "so-called", "third-party", "two-factor", "multi-factor", "single-use",
}


# ==========================================================================
# Untranslated-English threshold
# ==========================================================================

# Minimum number of English-dictionary words in a single text element to
# trigger a flag. 3 catches phrases / sentences while ignoring stray
# acronyms or single brand names that may legitimately appear.
_UNTRANSLATED_MIN_WORDS = 3


# ==========================================================================
# Target-language support for the untranslated-English check
# ==========================================================================
#
# The untranslated-English check needs a dictionary for the course's target
# language: a word only counts as "untranslated English" if it is an English
# word AND is NOT a valid word in the target language (so a Spanish course's
# "final", "total", "social" — all real Spanish words — are not flagged just
# because they also exist in English).
#
# pyspellchecker only ships dictionaries for a subset of languages. Base ISO
# code -> display name for the ones we can check. English is intentionally
# excluded (that is the normal, checkbox-off path). Any non-English course
# whose base language is NOT a key here has no dictionary, so the
# untranslated-English check is skipped for it.
#
# NOTE: this list reflects pyspellchecker's documented bundled dictionaries.
# check_untranslated_english also loads the dictionary inside a try/except and
# skips gracefully if a language named here is not actually installed, so an
# out-of-date entry degrades safely rather than crashing.
_TARGET_LANGUAGES: dict[str, str] = {
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "nl": "Dutch",
    "ru": "Russian",
}

# A language subtag pair like en-US / de-DE / es-LA / fr-CA / pt-BR / zh-CN,
# hyphen or underscore, not embedded in a longer letter run. Used to pull the
# language out of GLS course filenames (…_de-DE_…) and to normalise a code the
# UI / CLI passes in.
_LANG_PAIR_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]{2})[-_]([A-Za-z]{2})(?![A-Za-z])")


def base_language_code(code):
    """Normalise a language code to its lowercase 2-letter base.

    'de-DE' / 'de_DE' / 'DE' / 'de' -> 'de'. Returns None if `code` is empty
    or not a recognisable language code.
    """
    if not code:
        return None
    code = code.strip()
    m = re.fullmatch(r"([A-Za-z]{2})(?:[-_][A-Za-z]{2})?", code)
    return m.group(1).lower() if m else None


def detect_language_from_filename(name):
    """Return the base language code embedded in a SCORM filename, or None.

    GLS names embed the language as a delimited subtag, e.g.
    'GLSsh_11594_de-DE_AntiPhishEss_v4-03_sc24.zip' -> 'de'. The first such
    xx-YY pair wins; unrelated tokens like 'FOR-QA' or 'v4-03' don't match.
    """
    if not name:
        return None
    m = _LANG_PAIR_RE.search(name)
    return m.group(1).lower() if m else None


