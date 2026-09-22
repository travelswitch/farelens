from farelens.services.fare_rules_text import (
    build_summary_cache_key,
    normalize_for_cache,
    normalize_for_prompt,
)


def test_prompt_normalisation_strips_html_and_keeps_lines():
    raw = "<p>CANCELLATIONS<br/>BEFORE DEPARTURE&nbsp;&nbsp;CHARGE USD 100</p><div>NO-SHOW</div>"
    out = normalize_for_prompt(raw)
    assert "<" not in out and ">" not in out
    assert "CANCELLATIONS\nBEFORE DEPARTURE" in out
    assert "NO-SHOW" in out


def test_cache_normalisation_is_whitespace_and_case_insensitive():
    a = normalize_for_cache("Charge usd 100\n  for cancel")
    b = normalize_for_cache("<b>CHARGE</b> USD 100 FOR CANCEL")
    assert a == b


def test_cache_key_depends_on_lang_layout_and_namespace():
    text = "CHARGE USD 100 FOR CANCEL"
    base = build_summary_cache_key(text, namespace="v1", lang="en", is_mobile_view=False)
    assert base == build_summary_cache_key("  charge   usd 100 for cancel ", namespace="v1", lang="EN", is_mobile_view=False)
    assert base != build_summary_cache_key(text, namespace="v1", lang="ar", is_mobile_view=False)
    assert base != build_summary_cache_key(text, namespace="v1", lang="en", is_mobile_view=True)
    assert base != build_summary_cache_key(text, namespace="v2", lang="en", is_mobile_view=False)
    assert len(base) == 64
