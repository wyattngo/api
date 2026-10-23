"""Product categories: classification, per-case notes, and that `generic` changes nothing."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import product_category as P  # noqa: E402

CASE_SLUGS = ["clean_packshot", "hero_brand", "detail_closeup", "flat_lay",
              "lifestyle_context", "human_in_use", "concept_benefit", "launch_announcement"]


@pytest.mark.parametrize("description,expected", [
    ("Chai serum dưỡng da màu hồng", "beauty"),
    ("Nước hoa nữ 50ml", "beauty"),
    ("Bánh quy bơ hộp thiếc", "food_beverage"),
    ("Cà phê rang xay", "food_beverage"),
    ("Áo thun cotton unisex", "fashion_apparel"),
    ("Giày thể thao nam", "bags_footwear"),
    ("Túi xách da", "bags_footwear"),
    ("Dây chuyền bạc", "jewelry_watches"),
    ("Đồng hồ cơ nam dây da", "jewelry_watches"),
    ("Đồng hồ thông minh", "electronics"),
    ("Tai nghe bluetooth", "electronics"),
    ("Nến thơm tinh dầu", "home_living"),
    ("Bình hoa gốm", "home_living"),
])
def test_classifies_from_description(description, expected):
    assert P.classify_description(description) == expected


@pytest.mark.parametrize("description", ["", None, "Chai nhãn trắng rất đẹp", "Sản phẩm mới"])
def test_no_match_is_none(description):
    # "nhãn" (label) and "đẹp" (pretty) lose their accents to "nhan" and "dep": neither may classify.
    assert P.classify_description(description) is None


def test_resolve_user_choice_wins_over_description():
    assert P.resolve_category("electronics", "Chai serum") == ("electronics", "user")
    assert P.resolve_category("Generic", "Chai serum") == (P.GENERIC, "user")


def test_resolve_auto_and_default():
    assert P.resolve_category("auto", "Áo khoác") == ("fashion_apparel", "description")
    assert P.resolve_category(None, "Áo khoác") == ("fashion_apparel", "description")
    assert P.resolve_category("auto", "") == (P.GENERIC, "default")


def test_unknown_category_is_an_error_not_a_silent_fallback():
    with pytest.raises(ValueError):
        P.resolve_category("toys", "Chai serum")


@pytest.mark.parametrize("slug", CASE_SLUGS)
def test_generic_returns_the_intent_untouched(slug):
    assert P.apply_category(slug, "BASE", P.GENERIC) == "BASE"
    assert P.apply_category(slug, "BASE", "not-a-category") == "BASE"


def test_note_is_appended_after_the_base_intent():
    out = P.apply_category("flat_lay", "BASE.", "beauty")
    assert out.startswith("BASE. ") and out.endswith(P.CATEGORIES["beauty"]["notes"]["flat_lay"])


def test_override_replaces_human_in_use_for_worn_goods():
    # The base case says "only a hand and forearm visible", which is wrong for a garment.
    out = P.apply_category("human_in_use", "BASE", "fashion_apparel")
    assert "BASE" not in out and "worn" in out


def test_every_category_only_names_real_cases_and_has_keywords():
    for slug, cat in P.CATEGORIES.items():
        assert cat["keywords"], slug
        assert set(cat["notes"]) <= set(CASE_SLUGS), slug
        assert set(cat["overrides"]) <= set(CASE_SLUGS), slug
        assert all(kw == kw.lower() and kw == P._normalize(kw).strip() for kw in cat["keywords"]), slug


def test_build_case_prompts_keeps_generic_text_and_adds_category():
    pytest.importorskip("torch")
    import inference as I

    generic = I.build_case_prompts("Chai serum", category=P.GENERIC)
    default = I.build_case_prompts("Chai serum")
    assert generic == default and all(c["category"] == P.GENERIC for c in generic)
    base = {slug: intent for slug, _name, intent in I.CASE_INTENTS}
    assert all(base[c["slug"]] in c["intent"] and base[c["slug"]] in c["fallback"] for c in generic)

    beauty = I.build_case_prompts("Chai serum", category="beauty")
    flat = next(c for c in beauty if c["slug"] == "flat_lay")
    assert P.CATEGORIES["beauty"]["notes"]["flat_lay"] in flat["intent"]
    assert P.CATEGORIES["beauty"]["notes"]["flat_lay"] in flat["fallback"]
