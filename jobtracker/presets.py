"""Field and level presets. A profile is built from one field, one or more levels and locations.

Presets are only a starting point. `python -m jobtracker.init` writes them into a profile file
as plain lists, so every term can be edited afterwards.
"""
from __future__ import annotations

FIELDS: dict[str, dict] = {
    "software_ai": {
        "label": "Software, AI and data",
        "search_terms": ["Software Engineer", "Machine Learning", "Data Science", "AI"],
        "keywords": [
            "Software", "Developer", "Entwickler", "Backend", "Frontend", "Full Stack", "Fullstack",
            "Machine Learning", "AI", "KI", "ML", "LLM", "NLP", "Data Scien", "Data Engineer",
            "Data Analy", "Deep Learning", "Computer Vision", "DevOps", "Cloud", "Python", "Informatik",
        ],
        "companies": [
            {"name": "Celonis", "ats": "greenhouse", "slug": "celonis"},
            {"name": "N26", "ats": "greenhouse", "slug": "n26"},
            {"name": "GetYourGuide", "ats": "greenhouse", "slug": "getyourguide"},
            {"name": "Helsing", "ats": "greenhouse", "slug": "helsing"},
            {"name": "Databricks", "ats": "greenhouse", "slug": "databricks"},
            {"name": "Datadog", "ats": "greenhouse", "slug": "datadog"},
            {"name": "Stripe", "ats": "greenhouse", "slug": "stripe"},
            {"name": "Cloudflare", "ats": "greenhouse", "slug": "cloudflare"},
            {"name": "DeepL", "ats": "ashby", "slug": "DeepL"},
            {"name": "Aleph Alpha", "ats": "ashby", "slug": "AlephAlpha"},
        ],
    },
    "mechanical": {
        "label": "Mechanical engineering",
        "search_terms": ["Maschinenbau", "Mechanical Engineer", "Konstruktion", "Mechatronik"],
        "keywords": [
            "Mechanical", "Maschinenbau", "Konstruktion", "Konstrukteur", "CAD", "Mechanik",
            "Mechatronik", "Fertigung", "Manufacturing", "Produktionstechnik", "Simulation", "FEM",
            "CFD", "Thermodynam", "Fahrzeugtechnik", "Automotive", "Entwicklungsingenieur",
            "Design Engineer", "Verfahrenstechnik", "Werkstoff",
        ],
        "companies": [
            {"name": "Bosch", "ats": "smartrecruiters", "slug": "BoschGroup", "params": {"country": "de"}},
        ],
    },
    "electrical": {
        "label": "Electrical engineering and embedded systems",
        "search_terms": ["Elektrotechnik", "Electrical Engineer", "Embedded Systems", "Automatisierungstechnik"],
        "keywords": [
            "Elektrotechnik", "Electrical", "Embedded", "Hardware", "Elektronik", "Electronics",
            "Automatisierung", "Automation", "SPS", "PLC", "Regelungstechnik", "Energietechnik",
            "Leistungselektronik", "FPGA", "Firmware", "Messtechnik",
        ],
        "companies": [
            {"name": "Bosch", "ats": "smartrecruiters", "slug": "BoschGroup", "params": {"country": "de"}},
        ],
    },
    "business": {
        "label": "Business, marketing and management",
        "search_terms": ["Marketing", "Business Development", "Controlling", "Projektmanagement"],
        "keywords": [
            "Marketing", "Sales", "Vertrieb", "Business Development", "Controlling", "Finance",
            "Finanzen", "Human Resources", "Personalwesen", "Projektmanagement", "Project Manag",
            "Consult", "Beratung", "Product Manag", "Supply Chain", "Logistik", "Einkauf",
        ],
        "companies": [],
    },
}

LEVELS: dict[str, dict] = {
    "working_student": {
        "label": "Working student (Werkstudent)",
        "query_prefix": "Werkstudent",
        "require": ["Werkstudent", "Working Student", "Studentische"],
        "junior": True,
    },
    "internship": {
        "label": "Internship (Praktikum)",
        "query_prefix": "Praktikum",
        "require": ["Intern", "Internship", "Praktik"],
        "junior": True,
    },
    "thesis": {
        "label": "Bachelor or master thesis",
        "query_prefix": "Abschlussarbeit",
        "require": ["Thesis", "Abschlussarbeit", "Masterarbeit", "Bachelorarbeit"],
        "junior": True,
    },
    "entry": {
        "label": "Entry level, junior, graduate",
        "query_prefix": "Junior",
        "require": [],          # many entry jobs carry no marker in the title
        "junior": True,
    },
    "experienced": {
        "label": "Experienced, any seniority",
        "query_prefix": "",
        "require": [],
        "junior": False,
    },
}

JUNIOR_EXCLUDES = ["Senior", "Lead", "Head of", "Principal", "Director", "Leiter"]

# Places used to keep career page results inside the chosen country.
REGIONS: dict[str, list[str]] = {
    "germany": ["Germany", "Deutschland", "Berlin", "Munich", "München", "Hamburg", "Frankfurt",
                "Stuttgart", "Cologne", "Köln", "Düsseldorf", "Leipzig", "Dresden", "Hannover",
                "Nuremberg", "Nürnberg", "Erfurt", "Jena", "Karlsruhe", "Bremen"],
    "austria": ["Austria", "Österreich", "Vienna", "Wien", "Graz", "Linz", "Salzburg", "Innsbruck"],
    "switzerland": ["Switzerland", "Schweiz", "Zurich", "Zürich", "Basel", "Bern", "Geneva", "Genf", "Lausanne"],
    "netherlands": ["Netherlands", "Niederlande", "Amsterdam", "Rotterdam", "Eindhoven", "Utrecht", "The Hague"],
}
_ALIASES = {"deutschland": "germany", "österreich": "austria", "schweiz": "switzerland"}


def location_terms(locations: list[str]) -> list[str]:
    terms: list[str] = []
    for location in locations:
        key = _ALIASES.get(location.strip().lower(), location.strip().lower())
        for term in REGIONS.get(key, [location.strip()]):
            if term and term not in terms:
                terms.append(term)
    return terms


def _unique(values) -> list[str]:
    out: list[str] = []
    for value in values:
        value = " ".join(str(value).split())
        if value and value.lower() not in (v.lower() for v in out):
            out.append(value)
    return out


def build_profile(name: str, field: str, levels: list[str], locations: list[str],
                  keywords: list[str] | None = None, search_terms: list[str] | None = None,
                  field_label: str | None = None, max_queries: int = 6) -> dict:
    """Return a complete profile config for one person.

    field is a key of FIELDS, or "custom" together with keywords (and optionally search_terms).
    """
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip().lower()) or "default"
    if field in FIELDS:
        preset = FIELDS[field]
        keywords = _unique(list(keywords or []) + preset["keywords"])
        search_terms = _unique(list(search_terms or []) + preset["search_terms"])
        companies = [dict(c) for c in preset["companies"]]
        field_label = field_label or preset["label"]
    else:
        keywords = _unique(keywords or [])
        if not keywords:
            raise ValueError("a custom field needs at least one keyword")
        search_terms = _unique(search_terms or keywords[:4])
        companies = []
        field_label = field_label or ", ".join(keywords[:3])
    unknown = [level for level in levels if level not in LEVELS]
    if unknown:
        raise ValueError(f"unknown level(s): {unknown}. Choose from {list(LEVELS)}")
    levels = levels or ["experienced"]
    locations = _unique(locations) or ["Germany"]

    # One query per (search term, level), interleaved so every level is covered within the cap.
    prefixes = _unique(LEVELS[level]["query_prefix"] for level in levels) or [""]
    if any(not LEVELS[level]["query_prefix"] for level in levels):
        prefixes.append("")
    queries = _unique(f"{prefix} {term}".strip() for term in search_terms for prefix in prefixes)

    require: list[str] = []
    if all(LEVELS[level]["require"] for level in levels):
        require = _unique(t for level in levels for t in LEVELS[level]["require"])
    exclude = JUNIOR_EXCLUDES if all(LEVELS[level]["junior"] for level in levels) else []

    return {
        "profile": {"name": name, "field": field_label, "field_key": field if field in FIELDS else "custom",
                    "levels": levels},
        "search": {"queries": queries[:max_queries], "locations": locations},
        "filters": {
            "title_include_any": keywords,
            "title_require_any": require,
            "title_exclude_any": list(exclude),
            "location_match": location_terms(locations),
            "include_remote": True,
        },
        "sources": {"career_pages": {"companies": companies}},
        "paths": {
            "db": f"../data/{name}/jobs.db",
            "csv": f"../data/{name}/jobs.csv",
            "log": f"../logs/{name}.log",
            "ai_cache": f"../data/{name}/ai_cache.json",
        },
    }
