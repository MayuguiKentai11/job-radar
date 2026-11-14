"""Text normalization and extraction helpers (accent-insensitive, ES/EN)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Optional

from bs4 import BeautifulSoup


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: Optional[str]) -> str:
    """Lowercase, accent-free, collapsed whitespace."""
    if not s:
        return ""
    s = strip_accents(s.lower())
    s = re.sub(r"[^\w+#./\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def html_to_text(s: Optional[str]) -> str:
    if not s:
        return ""
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "lxml").get_text("\n")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def contains_term(haystack_norm: str, term: str) -> bool:
    """Word-boundary, accent-insensitive match. `haystack_norm` must be norm()'d."""
    t = norm(term)
    if not t:
        return False
    pattern = r"(?<![\w])" + re.escape(t) + r"(?![\w])"
    return re.search(pattern, haystack_norm) is not None


def split_list(value) -> list[str]:
    """Accept list or comma/newline separated string; return clean items."""
    if not value:
        return []
    if isinstance(value, str):
        value = re.split(r"[,\n;]", value)
    return [v.strip() for v in value if v and v.strip()]


# ---------- job title relevance ----------
# Words that mean the same thing in job titles (Spanish / English / common abbreviations).
# A group may contain multi-word phrases; matching is accent-insensitive and on whole words.
_SYNONYM_GROUPS = [
    {"analista", "analyst"},
    {"datos", "data", "bi", "business intelligence", "inteligencia de negocios", "analytics", "analitica", "power bi"},
    {"cientifico", "scientist", "data science", "ciencia de datos"},
    {"ingeniero", "engineer", "ing"},
    {"desarrollador", "developer", "programador", "dev"},
    {"especialista", "specialist", "expert"},
    {"asistente", "assistant", "auxiliar"},
    {"practicante", "intern", "trainee", "practicas", "pasante"},
    {"gerente", "manager"},
    {"jefe", "head", "lider", "lead"},
    {"coordinador", "coordinator"},
    {"consultor", "consultant"},
    {"ejecutivo", "executive"},
    {"administrador", "administrator", "admin"},
    {"contador", "accountant", "contable", "contabilidad"},
    {"finanzas", "finance", "financiero", "financial"},
    {"ventas", "sales", "comercial"},
    {"marketing", "mercadeo"},
    {"recursos humanos", "rrhh", "hr", "talento humano", "human resources"},
    {"sistemas", "ti", "it", "tecnologia"},
    {"proyectos", "project", "projects"},
    {"producto", "product"},
    {"calidad", "quality", "qa"},
    {"soporte", "support", "help desk", "mesa de ayuda"},
    {"operaciones", "operations"},
    {"logistica", "logistics"},
    {"compras", "procurement", "purchasing"},
    {"diseñador", "designer"},
    {"seguridad", "security"},
    {"negocios", "business"},
    {"procesos", "process", "processes"},
    {"riesgos", "risk", "risks"},
]
_TITLE_STOPWORDS = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "con", "a", "o", "of", "the", "and", "in", "for", "to", "/", "-", "|"}
_SYN_INDEX: dict[str, set[str]] = {}
for _g in _SYNONYM_GROUPS:
    _gn = {norm(w) for w in _g}
    for _w in _gn:
        _SYN_INDEX.setdefault(_w, set()).update(_gn)


def _variants(word: str) -> set[str]:
    """Singular/plural and masculine/feminine forms: analista(s), ingeniero(a)(s), datos/dato."""
    out = {word}
    if len(word) > 3:
        if word.endswith("es"):
            out.add(word[:-2])
        if word.endswith("s"):
            out.add(word[:-1])
        else:
            out.update({word + "s", word + "es"})
        base = word[:-1] if word.endswith("s") else word
        if base.endswith("o"):
            out.update({base[:-1] + "a", base[:-1] + "as"})
        if base.endswith("or"):
            out.update({base + "a", base + "as"})
    return out


def _concept_terms(word: str) -> set[str]:
    """All the ways a keyword word may appear in a title."""
    terms: set[str] = set()
    for v in _variants(word):
        terms.update(_SYN_INDEX.get(v, {v}))
    expanded: set[str] = set()
    for t in terms:
        expanded.update(_variants(t) if " " not in t else {t})
    return expanded


def keyword_concepts(keyword: str) -> list[set[str]]:
    """Split a role ('Analista de datos') into the concepts a title must contain.
    Known multi-word phrases ('recursos humanos', 'power bi') count as one concept."""
    k = norm(keyword)
    words = k.split()
    concepts: list[set[str]] = []
    i = 0
    while i < len(words):
        # longest known phrase starting here
        for size in (3, 2):
            phrase = " ".join(words[i:i + size])
            if size > 1 and len(words) - i >= size and phrase in _SYN_INDEX:
                concepts.append(set(_SYN_INDEX[phrase]))
                i += size
                break
        else:
            w = words[i]
            i += 1
            if w in _TITLE_STOPWORDS or not re.search(r"\w", w):
                continue
            concepts.append(_concept_terms(w))
    return concepts


def title_matches(title: str, keyword: str) -> bool:
    """True when the title contains every concept of the keyword (or a synonym).
    'Analista de datos' matches 'Data Analyst', 'Analista BI' and 'Analista de Datos Sr.',
    but not 'Analista de Inteligencia Artificial' or 'Especialista de Pricing'."""
    t = norm(title)
    concepts = keyword_concepts(keyword)
    if not concepts:
        return False
    return all(any(contains_term(t, term) for term in c) for c in concepts)


# ---------- modality ----------
_REMOTE = ["remoto", "remote", "teletrabajo", "home office", "trabajo desde casa", "100% remoto", "work from home"]
_HYBRID = ["hibrido", "hybrid", "presencial y remoto", "semipresencial", "semi presencial", "mixto"]
_ONSITE = ["presencial", "on-site", "onsite", "en sitio"]


def detect_modality(modality_text: Optional[str], title: str, description: str, is_remote: Optional[bool]) -> Optional[str]:
    """Return 'remoto' | 'hibrido' | 'presencial' | None."""
    explicit = norm(modality_text)
    if explicit:
        if any(k in explicit for k in _HYBRID):
            return "hibrido"
        if any(k in explicit for k in _REMOTE):
            return "remoto"
        if any(k in explicit for k in _ONSITE):
            return "presencial"
    head = norm(title) + " " + norm(description)[:1500]
    if any(contains_term(head, k) for k in _HYBRID):
        return "hibrido"
    if is_remote or any(contains_term(head, k) for k in _REMOTE):
        return "remoto"
    if is_remote is False or any(contains_term(head, k) for k in _ONSITE):
        return "presencial"
    return None


# ---------- seniority ----------
_LEVELS = [
    ("practicante", ["practicante", "practicas", "pasante", "intern", "internship", "trainee", "becario"]),
    ("jefatura", ["jefe", "jefa", "gerente", "manager", "head of", "director", "directora", "subgerente", "lead", "lider", "coordinador", "coordinadora", "supervisor", "supervisora"]),
    ("senior", ["senior", "sr", "sr.", "especialista senior"]),
    ("semi-senior", ["semi senior", "semisenior", "semi-senior", "ssr", "semi sr"]),
    ("junior", ["junior", "jr", "jr.", "asistente", "auxiliar"]),
]


def detect_level(title: str) -> Optional[str]:
    t = norm(title)
    # semi-senior must win over senior
    for key in ("semi-senior",):
        if any(contains_term(t, k) for k in dict(_LEVELS)[key]):
            return key
    for key, words in _LEVELS:
        if any(contains_term(t, w) for w in words):
            return key
    return None


# ---------- job type ----------
_JOB_TYPES = [
    ("practicas", ["practicas", "practicante", "internship", "pasantia", "intern"]),
    ("medio_tiempo", ["medio tiempo", "part-time", "part time", "parttime", "tiempo parcial"]),
    ("temporal", ["temporal", "temporary", "por proyecto", "ocasional", "suplencia"]),
    ("freelance", ["freelance", "independiente", "contractor", "locacion de servicios", "recibo por honorarios", "contract", "autonomo"]),
    ("tiempo_completo", ["tiempo completo", "full-time", "full time", "fulltime", "jornada completa"]),
]


def detect_job_types(job_type_text: Optional[str], title: str, description: str) -> set[str]:
    found: set[str] = set()
    explicit = norm(job_type_text)
    for key, words in _JOB_TYPES:
        if explicit and any(w in explicit for w in words):
            found.add(key)
    if found:
        return found
    head = norm(title) + " " + norm(description)[:2000]
    for key, words in _JOB_TYPES:
        if any(contains_term(head, w) for w in words):
            found.add(key)
    return found


# ---------- years of experience ----------
_NUM_WORDS = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
              "siete": 7, "ocho": 8, "diez": 10, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_N = r"(\d{1,2}|" + "|".join(_NUM_WORDS) + r")"
_YEARS_PATTERNS = [
    _N + r"\s*\+?\s*(?:a\s*\d{1,2}\s*)?anos?\s*(?:de\s*)?(?:experiencia|exp\b)",
    r"experiencia\s*(?:laboral\s*|profesional\s*|minima\s*|comprobada\s*|no\s*menor\s*)*(?:de\s*|a\s*)?(?:minimo\s*|al\s*menos\s*)?" + _N + r"\s*\+?\s*(?:(?:a|-)\s*\d{1,2}\s*)?anos?",
    r"(?:minimo|al\s*menos)\s*(?:de\s*)?" + _N + r"\s*\+?\s*anos?",
    _N + r"\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?\s*(?:of\s*)?(?:\w+\s*){0,2}experience",
]


def _to_int(tok: str) -> Optional[int]:
    if tok.isdigit():
        return int(tok)
    return _NUM_WORDS.get(tok)


def extract_required_years(text: str) -> Optional[int]:
    """Minimum years of experience the offer asks for, or None."""
    t = norm(text)
    found: list[int] = []
    for pat in _YEARS_PATTERNS:
        for m in re.finditer(pat, t):
            v = _to_int(m.group(1))
            if v is not None and 0 < v <= 25:
                found.append(v)
    return min(found) if found else None


# ---------- salary ----------
_SAL_RE = re.compile(
    r"(s/\.?|s\./|pen|usd|us\$|\$)\s*([\d.,]+)(?:\s*(?:-|–|a|hasta)\s*(?:s/\.?|usd|\$)?\s*([\d.,]+))?",
    re.I,
)


def _parse_amount(s: str) -> Optional[float]:
    s = s.strip().strip(".,")
    if not s:
        return None
    # "2.200,00" (es) | "2,200.00" (en) | "2200"
    if re.match(r"^\d{1,3}(\.\d{3})+(,\d+)?$", s):
        s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d{1,3}(,\d{3})+(\.\d+)?$", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_salary_text(text: Optional[str]) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """Parse 'S/. 2.200,00 (Mensual)' -> (2200, None, 'PEN', 'monthly')."""
    if not text:
        return None, None, None, None
    m = _SAL_RE.search(text)
    if not m:
        return None, None, None, None
    cur_raw = m.group(1).lower()
    currency = "USD" if ("usd" in cur_raw or "us$" in cur_raw or cur_raw == "$") else "PEN"
    lo, hi = _parse_amount(m.group(2)), _parse_amount(m.group(3) or "")
    t = norm(text)
    period = "monthly"
    if "anual" in t or "year" in t:
        period = "yearly"
    elif "hora" in t or "hour" in t:
        period = "hourly"
    elif "diari" in t or "dia" in t:
        period = "daily"
    elif "seman" in t:
        period = "weekly"
    return lo, hi, currency, period


_TO_MONTHLY = {"monthly": 1, "yearly": 1 / 12, "hourly": 160, "daily": 22, "weekly": 4.33}


def to_monthly(amount: Optional[float], period: Optional[str]) -> Optional[float]:
    if amount is None:
        return None
    return amount * _TO_MONTHLY.get(period or "monthly", 1)


# ---------- relative dates ("Hace 3 días") ----------
def parse_relative_date(text: Optional[str], today: Optional[date] = None) -> Optional[date]:
    if not text:
        return None
    today = today or date.today()
    t = norm(text)
    if "ayer" in t:
        return today - timedelta(days=1)
    if any(k in t for k in ("hora", "minuto", "segundo", "hoy", "instante")):
        return today
    if "mas de 30" in t:
        return today - timedelta(days=31)
    m = re.search(r"(\d+)\s*(dia|semana|mes)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"dia": 1, "semana": 7, "mes": 30}[unit]
        return today - timedelta(days=days)
    return None


# ---------- language ----------
_ES = {"de", "la", "que", "el", "en", "y", "los", "para", "con", "una", "las", "del", "experiencia", "conocimiento"}
_EN = {"the", "and", "to", "of", "with", "for", "you", "our", "will", "experience", "we", "are", "is"}


def detect_language(text: str) -> Optional[str]:
    words = norm(text[:3000]).split()
    if len(words) < 8:
        return None
    es = sum(w in _ES for w in words)
    en = sum(w in _EN for w in words)
    if es == en == 0:
        return None
    return "es" if es >= en else "en"
