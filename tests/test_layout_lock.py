"""Shot 8 layout lock: the closing layout sentence is appended after PE and survives any rewrite."""
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_DEVICE", "cpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inference as I  # noqa: E402
import product_category as P  # noqa: E402
import pytest  # noqa: E402

CATEGORIES = [P.GENERIC] + sorted(P.CATEGORIES)


def test_only_launch_announcement_is_locked():
    for slug, _, _ in I.CASE_INTENTS:
        assert bool(P.layout_clause(slug)) == (slug == "launch_announcement")


@pytest.mark.parametrize("category", CATEGORIES)
def test_launch_case_carries_layout_for_every_category(category):
    cases = {c["slug"]: c for c in I.build_case_prompts("sản phẩm", category=category)}
    layout = cases["launch_announcement"]["layout"]
    assert layout.startswith(P.LAYOUT_BASE)
    assert cases["launch_announcement"]["fallback"].endswith(layout)
    assert all(c["layout"] == "" for s, c in cases.items() if s != "launch_announcement")
    assert all(not c["fallback"].endswith(P.LAYOUT_BASE) for s, c in cases.items() if s != "launch_announcement")


def test_shape_hint_only_for_overflow_prone_categories():
    assert P.layout_clause("launch_announcement", "fashion_apparel").endswith(P.LAYOUT_SHAPE["fashion_apparel"])
    assert P.layout_clause("launch_announcement", "beauty") == P.LAYOUT_BASE
    assert P.layout_clause("launch_announcement", P.GENERIC) == P.LAYOUT_BASE


@pytest.mark.parametrize("rewritten", [
    "A long, totally different scene description.",
    "  spaced \n out   text ",
    "Short.",
])
def test_layout_is_last_whatever_pe_wrote(rewritten):
    layout = P.layout_clause("launch_announcement", "bags_footwear")
    out = I.append_fixed_clauses(rewritten, n_refs=3, color_note="Colors: red.", layout_clause=layout)
    assert out.endswith(layout)
    assert out.index(I.PRESERVE_CLAUSE) < out.index(I.MULTI_REF_CLAUSE) < out.index("Colors: red.") < out.index(layout)


def test_no_layout_leaves_old_output_unchanged():
    assert I.append_fixed_clauses("x", 1, "", "") == "x " + I.PRESERVE_CLAUSE


def test_enhance_case_prompts_passes_layout_to_pe(monkeypatch):
    seen = {}

    def fake_pe(intent, image, **kw):
        seen[intent] = kw["layout_clause"]
        return {"parse_ok": True, "rewritten_prompt": "r", "wh_ratio": "", "ratio_follow": ""}

    monkeypatch.setattr(I, "enhance_prompt", fake_pe)
    cases = I.build_case_prompts("áo thun", category="fashion_apparel")
    I.enhance_case_prompts(cases, None)
    launch = next(c for c in cases if c["slug"] == "launch_announcement")
    assert seen[launch["intent"]] == launch["layout"] != ""
    assert sum(1 for v in seen.values() if v) == 1
