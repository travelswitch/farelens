"""Normalisation of raw fare-rules text and stable cache keys.

Fare rules arrive from GDS/NDC feeds as HTML-ish, inconsistently cased and
whitespace-heavy blobs. Two different normalisations are used:

* `for_prompt`  - readable text for the model (keeps line structure).
* `for_cache`   - aggressive canonical form so trivially different copies of
                  the same rule (whitespace, casing, tags) share one cache entry.
"""

from __future__ import annotations

import hashlib
import re
from html import unescape

_HTML_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>")
_HTML_TAG_RE = re.compile(r"(?s)<[^>]+>")
_LINE_ENDING_RE = re.compile(r"\r\n?")
_TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")
_ALL_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_prompt(text: str) -> str:
    out = unescape(str(text or ""))
    out = _HTML_BREAK_RE.sub("\n", out)
    out = _HTML_TAG_RE.sub(" ", out)
    out = _LINE_ENDING_RE.sub("\n", out)
    out = _MULTI_SPACE_RE.sub(" ", out)
    out = _TRAILING_SPACES_RE.sub("\n", out)
    out = _EXCESS_NEWLINES_RE.sub("\n\n", out)
    return out.strip()


def normalize_for_cache(text: str) -> str:
    out = unescape(str(text or ""))
    out = _HTML_TAG_RE.sub(" ", out)
    out = out.upper()
    out = _ALL_WHITESPACE_RE.sub("", out)
    return out


def build_summary_cache_key(fare_rules_text: str, *, namespace: str, lang: str, is_mobile_view: bool) -> str:
    canonical = normalize_for_cache(fare_rules_text)
    material = "\n".join(
        [
            (namespace or "").strip().lower(),
            (lang or "en").strip().lower(),
            "mobile" if is_mobile_view else "desktop",
            canonical,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
