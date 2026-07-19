"""Keyword and skill extraction for tier-1 scoring.

Deliberately not embeddings. At this scale a curated skill vocabulary plus TF-IDF
overlap is more useful than semantic similarity, because ATS keyword matching is
itself literal: recruiters and parsers search for the exact token "PostgreSQL",
and a vector that merely knows Postgres is database-like does not help the user
get past the filter.
"""

from __future__ import annotations

import re
from collections import Counter

# Canonical form -> surface variants an ATS or JD might use. Matching is done on
# variants; reporting uses the canonical form so the UI is not noisy.
SKILL_VOCAB: dict[str, tuple[str, ...]] = {
    "python": ("python", "python3"),
    "javascript": ("javascript", "js", "es6"),
    "typescript": ("typescript", "ts"),
    "java": ("java",),
    "kotlin": ("kotlin",),
    "go": ("golang", "go"),
    "rust": ("rust",),
    "c++": ("c++", "cpp"),
    "c#": ("c#", "csharp", ".net", "dotnet"),
    "php": ("php", "laravel"),
    "ruby": ("ruby", "rails", "ruby on rails"),
    "swift": ("swift",),
    "scala": ("scala",),
    "r": ("rstats",),
    "sql": ("sql",),
    "react": ("react", "reactjs", "react.js"),
    "vue": ("vue", "vuejs", "vue.js"),
    "angular": ("angular", "angularjs"),
    "svelte": ("svelte", "sveltekit"),
    "next.js": ("next.js", "nextjs"),
    "node.js": ("node.js", "nodejs", "node"),
    "django": ("django",),
    "flask": ("flask",),
    "fastapi": ("fastapi",),
    "spring": ("spring", "spring boot", "springboot"),
    "express": ("express", "expressjs"),
    "graphql": ("graphql",),
    "rest": ("rest", "restful", "rest api"),
    "grpc": ("grpc",),
    "postgresql": ("postgresql", "postgres", "psql"),
    "mysql": ("mysql", "mariadb"),
    "mongodb": ("mongodb", "mongo"),
    "redis": ("redis",),
    "elasticsearch": ("elasticsearch", "opensearch"),
    "kafka": ("kafka",),
    "rabbitmq": ("rabbitmq",),
    "sqlite": ("sqlite",),
    "aws": ("aws", "amazon web services", "ec2", "s3", "lambda"),
    "gcp": ("gcp", "google cloud"),
    "azure": ("azure",),
    "docker": ("docker", "containerization", "containerisation"),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform",),
    "ansible": ("ansible",),
    "ci/cd": ("ci/cd", "cicd", "continuous integration", "continuous delivery"),
    "github actions": ("github actions",),
    "jenkins": ("jenkins",),
    "linux": ("linux", "unix"),
    "git": ("git", "version control"),
    "machine learning": ("machine learning", "ml"),
    "deep learning": ("deep learning", "neural network", "neural networks"),
    "pytorch": ("pytorch", "torch"),
    "tensorflow": ("tensorflow", "keras"),
    "scikit-learn": ("scikit-learn", "sklearn"),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    "nlp": ("nlp", "natural language processing"),
    "computer vision": ("computer vision", "opencv"),
    "llm": ("llm", "large language model", "generative ai", "genai", "rag"),
    "data engineering": ("etl", "elt", "data pipeline", "data pipelines"),
    "spark": ("spark", "pyspark"),
    "airflow": ("airflow",),
    "dbt": ("dbt",),
    "snowflake": ("snowflake",),
    "tableau": ("tableau",),
    "power bi": ("power bi", "powerbi"),
    "excel": ("excel", "spreadsheet"),
    "microservices": ("microservices", "microservice"),
    "system design": ("system design", "distributed systems"),
    "agile": ("agile", "scrum", "kanban"),
    "testing": ("unit testing", "integration testing", "tdd", "pytest", "jest"),
    "security": ("security", "owasp", "penetration testing", "appsec"),
    "android": ("android",),
    "ios": ("ios", "swiftui"),
    "flutter": ("flutter", "dart"),
    "react native": ("react native",),
    "figma": ("figma",),
    "html/css": ("html", "css", "sass", "tailwind"),
}

_VARIANT_TO_CANON: dict[str, str] = {
    variant: canon for canon, variants in SKILL_VOCAB.items() for variant in variants
}
# Longest-first so "react native" wins over "react", and "google cloud" over "go".
_SORTED_VARIANTS = sorted(_VARIANT_TO_CANON, key=len, reverse=True)

STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "will", "are", "have",
    "this", "that", "from", "who", "all", "can", "has", "was", "were", "been",
    "their", "they", "them", "its", "not", "but", "any", "may", "such", "than",
    "then", "these", "those", "there", "here", "into", "out", "about", "over",
    "team", "work", "working", "role", "job", "company", "candidate", "position",
    "must", "should", "would", "could", "also", "well", "more", "most", "other",
    "help", "make", "including", "etc", "able", "using", "use", "years", "year",
    "experience", "strong", "good", "great", "excellent", "ability", "skills",
    "plus", "nice", "required", "preferred", "responsibilities", "requirements",
    "benefits", "apply", "please", "email", "resume", "cv", "join", "looking",
}


def _word_boundary_pattern(variant: str) -> re.Pattern[str]:
    """Match a variant as a whole token, tolerating `+` and `#` in names.

    A plain \\b fails on "c++" because `+` is not a word character, so the
    trailing boundary is asserted against punctuation/whitespace instead.
    """
    escaped = re.escape(variant)
    lead = r"(?<![a-z0-9+#.])"
    trail = r"(?![a-z0-9+#])"
    return re.compile(lead + escaped + trail, re.IGNORECASE)


_PATTERNS: dict[str, re.Pattern[str]] = {
    v: _word_boundary_pattern(v) for v in _SORTED_VARIANTS
}


def extract_skills(text: str) -> set[str]:
    """Canonical skills mentioned in `text`."""
    if not text:
        return set()
    low = text.lower()
    found: set[str] = set()
    for variant in _SORTED_VARIANTS:
        if _PATTERNS[variant].search(low):
            found.add(_VARIANT_TO_CANON[variant])
    return found


def tokenize(text: str) -> list[str]:
    """Content words, stopwords and pure numbers removed."""
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9+#./-]*", (text or "").lower())
    return [t.strip("./-") for t in raw if len(t) > 2 and t not in STOPWORDS]


def top_terms(text: str, limit: int = 40) -> list[str]:
    """Most frequent content words, as a proxy for what the JD emphasises."""
    return [term for term, _ in Counter(tokenize(text)).most_common(limit)]


def keyword_overlap(resume_text: str, jd_text: str) -> tuple[float, list[str], list[str]]:
    """Skill-level overlap between a resume and a job description.

    Returns (coverage 0-100, matched canonical skills, missing canonical skills).
    Only skills the JD actually asks for count, so a resume is never penalised for
    lacking something the posting never mentioned.
    """
    jd_skills = extract_skills(jd_text)
    resume_skills = extract_skills(resume_text)

    if not jd_skills:
        # No recognisable skills in the JD: fall back to raw term overlap so a
        # non-technical or vaguely-worded posting still gets a usable signal.
        jd_terms = set(top_terms(jd_text, 30))
        resume_terms = set(tokenize(resume_text))
        if not jd_terms:
            return 0.0, [], []
        hit = jd_terms & resume_terms
        return round(len(hit) / len(jd_terms) * 100, 1), sorted(hit), sorted(jd_terms - hit)

    matched = jd_skills & resume_skills
    missing = jd_skills - resume_skills
    coverage = len(matched) / len(jd_skills) * 100
    return round(coverage, 1), sorted(matched), sorted(missing)
