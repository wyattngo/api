"""Danh mục sản phẩm cho bộ 8 ảnh.

Tám "case" trong `inference.CASE_INTENTS` viết cho một sản phẩm bất kỳ, nên với áo, đồng hồ hay
bánh kẹo, câu như "only a hand and forearm visible" hoặc "a few props related to how the product
is used" để mô hình tự đoán. Module này cho từng danh mục một bộ câu bổ sung theo case, nối vào
cuối intent gốc (hoặc thay hẳn intent, chỉ với case `human_in_use`).

Hai nguyên tắc:
- `generic` (hoặc không khớp danh mục nào) cho đúng intent gốc, từng chữ. Danh mục chỉ làm prompt
  cụ thể hơn, không bao giờ làm mất hành vi hiện tại.
- Không có torch/PIL ở đây: chạy và test được mà không cần GPU.

Chọn danh mục: client gửi `category` (một slug bên dưới, `generic`, hoặc `auto`). `auto` (mặc định)
đoán từ `description` bằng từ khoá; không khớp thì `generic`.
"""

import re
import unicodedata

AUTO = "auto"
GENERIC = "generic"

# Mỗi danh mục:
#   label     tên hiển thị
#   keywords  từ khoá tìm trong description (không dấu, chữ thường; Việt + Anh)
#   notes     {case_slug: câu nối vào cuối intent gốc}
#   overrides {case_slug: intent thay thế hoàn toàn} (chỉ khi câu gốc sai cho danh mục đó)
CATEGORIES = {
    "beauty": {
        "label": "Mỹ phẩm, chăm sóc da, nước hoa",
        "keywords": (
            "my pham", "cham soc da", "skincare", "serum", "kem duong", "kem chong nang",
            "sua rua mat", "toner", "mat na", "lotion", "son moi", "son duong", "phan nuoc",
            "nuoc hoa", "perfume", "dau goi", "dau xa", "tinh dau", "essential oil", "cushion",
            "mascara", "cosmetic", "moisturizer", "sunscreen",
        ),
        "notes": {
            "hero_brand": "Choose the soft materials of a beauty counter for the surface, such as polished stone, frosted glass or satin, with a gentle gradient that echoes the product's own tones.",
            "detail_closeup": "Focus on the cap, pump, nozzle or dropper, the finish of the glass or plastic, and the printed label.",
            "flat_lay": "Props come from a daily routine, such as a folded towel, cotton pads, a sprig of greenery or a small ceramic dish, kept minimal and in matching tones.",
            "lifestyle_context": "Set it on a bathroom shelf or a vanity beside a window, with a mirror or tile softly blurred behind.",
            "human_in_use": "The hand holds the product by its natural grip with the label facing the camera.",
            "concept_benefit": "Express the benefit with fresh natural elements that match the product's purpose, such as water droplets, petals or leaves, never covering the label.",
        },
        "overrides": {},
    },
    "food_beverage": {
        "label": "Thực phẩm, đồ uống",
        "keywords": (
            "thuc pham", "do uong", "do an", "banh", "keo deo", "socola", "chocolate", "ca phe",
            "coffee", "tra xanh", "tra sua", "tra tui loc", "nuoc ep", "nuoc ngot", "sua tuoi", "sua chua", "sua hat", "snack", "mut", "mat ong", "honey",
            "gia vi", "nuoc mam", "mi goi", "bot mi", "ngu coc", "granola", "food", "beverage",
            "drink", "tea", "juice", "sauce",
        ),
        "notes": {
            "hero_brand": "Use a clean tabletop in warm neutral tones with natural, appetizing light; the package or the served product is the hero.",
            "detail_closeup": "Show the texture of the food and the printed label and seal, with a few crumbs or droplets that look natural.",
            "flat_lay": "Props are the ingredients and tableware that belong to the product, such as raw ingredients, a wooden board, a napkin and a spoon.",
            "lifestyle_context": "Set it on a kitchen counter or a dining table at a real meal or break moment.",
            "concept_benefit": "Convey freshness or flavor with the key ingredients arranged around the product.",
        },
        "overrides": {
            "human_in_use": "Show one adult person about to eat or drink the product, or holding a serving of it, with only hands and forearms visible. The hands have realistic skin texture, natural fingers and realistic anatomy, and the person wears neutral-colored clothing. The background is softly blurred, lit by natural window light, and the product stays sharp and dominant, so its real size and how it is enjoyed are easy to understand.",
        },
    },
    "fashion_apparel": {
        "label": "Thời trang, quần áo",
        "keywords": (
            "ao thun", "ao so mi", "ao khoac", "ao dai", "ao len", "ao polo", "quan jean",
            "quan tay", "quan short", "vay", "dam du", "do bo", "hoodie", "t-shirt", "tshirt",
            "shirt", "jacket", "dress", "jeans", "sweater", "thoi trang", "apparel",
        ),
        "notes": {
            "hero_brand": "Present the garment neatly styled on a clean backdrop, on a hanger, a mannequin form or softly draped, with folds that show its cut and drape.",
            "detail_closeup": "Focus on the fabric weave, stitching, buttons, zipper or print, with raking light that reveals texture.",
            "flat_lay": "Lay the garment flat and neatly arranged, with two or three coordinating accessories placed around it.",
            "lifestyle_context": "Show the garment in a bright real-life setting such as a wardrobe rail, a bedroom or a cafe terrace.",
            "concept_benefit": "Express the occasion or season the garment is made for through the light and the setting.",
        },
        "overrides": {
            "human_in_use": "Show the garment worn by one adult person, framed from the neck down so the fit, drape and length are clear. The posture is natural, the hands and anatomy are realistic, and the background is softly blurred in natural window light. The garment stays sharp, with its colors, print and cut exactly as in the reference image.",
        },
    },
    "bags_footwear": {
        "label": "Túi xách, giày dép",
        "keywords": (
            "tui xach", "tui deo", "balo", "vi da", "vi nam", "vi nu", "giay the thao", "giay da", "giay cao got", "giay luoi", "sneaker",
            "boot", "sandal", "handbag", "backpack", "wallet", "purse", "shoes", "shoe", "tote",
        ),
        "notes": {
            "hero_brand": "Stand the item upright on a low plinth or platform, seen from a three-quarter view that shows its full silhouette.",
            "detail_closeup": "Focus on stitching, hardware, buckles, the sole or the leather grain.",
            "flat_lay": "Arrange it with a few coordinating everyday items such as sunglasses, keys or a notebook, with generous spacing.",
            "lifestyle_context": "Place it on a bench, a doorstep or a clean floor in a real scene.",
        },
        "overrides": {
            "human_in_use": "Show the item in use by one adult person: a bag carried on the shoulder or in the hand, or shoes worn and seen from the knee down. The pose is natural, the anatomy is realistic, and the background is softly blurred in natural light. The product stays sharp and dominant, so its real size and how it is worn are easy to understand.",
        },
    },
    "jewelry_watches": {
        "label": "Trang sức, đồng hồ",
        "keywords": (
            "trang suc", "day chuyen", "vong tay", "vong co", "hoa tai", "nhan cuoi", "nhan vang",
            "nhan bac", "mat day", "dong ho", "ring", "necklace", "bracelet", "earring", "jewelry",
            "jewellery", "watch", "pendant",
        ),
        "notes": {
            "hero_brand": "Place it on a small pedestal of stone or velvet against a dark or softly graded backdrop, with precise specular highlights.",
            "detail_closeup": "Use macro focus on the stones, clasp, engraving, dial or case finish, with clean metal reflections and extremely sharp detail.",
            "flat_lay": "Set it on a textured surface with a small tray, a silk ribbon or a dried flower, with generous spacing.",
            "lifestyle_context": "Place it on a dresser or vanity beside a softly blurred mirror.",
        },
        "overrides": {
            "human_in_use": "Show the piece worn by one adult person and framed tightly: a necklace at the collarbone, a ring on a hand, or a bracelet or watch on a wrist. The skin has realistic texture, the anatomy is realistic, and the background is softly blurred in natural light. The piece stays sharp and dominant, so its real size and how it sits are easy to understand.",
        },
    },
    "electronics": {
        "label": "Điện tử, phụ kiện công nghệ",
        "keywords": (
            "dong ho thong minh", "tai nghe", "dien thoai", "sac du phong", "cu sac", "cap sac",
            "loa", "may anh", "may tinh", "laptop", "chuot", "ban phim", "man hinh", "may in",
            "smartwatch", "headphone", "earbuds", "speaker", "keyboard", "mouse", "camera",
            "charger", "gadget", "smartphone",
        ),
        "notes": {
            "hero_brand": "Use a dark, clean, modern surface with a soft gradient and a crisp edge light that defines the contours.",
            "detail_closeup": "Focus on the ports, buttons, screen bezel, materials and logo; any screen shows the same content as the reference image or stays dark.",
            "flat_lay": "Shoot top-down on a tidy desk surface with a couple of related accessories such as a cable or a notebook.",
            "lifestyle_context": "Place it on a modern desk with a laptop and a plant softly blurred behind.",
            "human_in_use": "Any screen shows the same content as the reference image or stays dark.",
            "concept_benefit": "Express speed, battery or sound with clean light trails or simple abstract shapes, without any text.",
        },
        "overrides": {},
    },
    "home_living": {
        "label": "Nhà cửa, đồ gia dụng, trang trí",
        "keywords": (
            "nen thom tinh dau", "nen thom", "candle", "binh hoa", "lo hoa", "coc su", "ly su", "binh giu nhiet", "tumbler",
            "noi chien", "chao", "dao keo", "do gia dung", "gia dung", "trang tri", "decor",
            "den ngu", "den ban", "goi om", "chan ga", "ga giuong", "khan tam", "tham trai san", "ghe",
            "sofa", "ke sach", "furniture", "kitchenware", "homeware", "vase", "lamp", "cushion cover",
        ),
        "notes": {
            "hero_brand": "Use a calm, tidy interior surface such as a wooden shelf or a console in tones that suit the product's style.",
            "detail_closeup": "Focus on the material grain, joints, glaze or the texture of the fabric.",
            "flat_lay": "For a small item shoot top-down; for a large item show a three-quarter overhead view of the whole piece instead of a strict top-down.",
            "lifestyle_context": "Show it in a complete, uncluttered room that suits its style and scale.",
        },
        "overrides": {
            "human_in_use": "Show one adult person interacting with the product in a natural way, such as sitting, pouring, switching it on or placing it, with the person partly cropped. The anatomy is realistic and the person wears neutral-colored clothing. The background is softly blurred in natural window light, and the product stays sharp and dominant, so its real size and use are easy to understand.",
        },
    },
}


def _normalize(text):
    """Chữ thường, bỏ dấu tiếng Việt, đ -> d, ký tự lạ -> khoảng trắng (giữ '-')."""
    text = (text or "").lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " " + re.sub(r"[^a-z0-9\- ]+", " ", text) + " "


def classify_description(description):
    """Danh mục đoán từ mô tả, hoặc None nếu không khớp.

    Điểm = tổng độ dài các từ khoá khớp (cả từ), nên cụm cụ thể thắng cụm chung: "dong ho thong
    minh" (electronics) thắng "dong ho" (jewelry_watches). Hoà điểm thì danh mục khai báo trước thắng.
    """
    haystack = _normalize(description)
    best, best_score = None, 0
    for slug, cat in CATEGORIES.items():
        score = sum(len(kw) for kw in cat["keywords"] if f" {kw} " in haystack)
        if score > best_score:
            best, best_score = slug, score
    return best


def resolve_category(requested, description=""):
    """(slug, source): source là "user" | "description" | "default".

    `requested` là slug, "generic" hoặc "auto"/rỗng. Slug lạ -> ValueError (client gõ sai thì
    báo, không lặng lẽ đổi sang danh mục khác).
    """
    wanted = str(requested or AUTO).strip().lower()
    if wanted == GENERIC:
        return GENERIC, "user"
    if wanted in CATEGORIES:
        return wanted, "user"
    if wanted != AUTO:
        raise ValueError(f"category phải là 'auto', 'generic' hoặc một trong {sorted(CATEGORIES)}")
    guessed = classify_description(description)
    return (guessed, "description") if guessed else (GENERIC, "default")


def apply_category(case_slug, intent, category):
    """Intent của một case sau khi áp danh mục. `generic`/lạ trả đúng `intent`, không đổi chữ nào."""
    cat = CATEGORIES.get(category)
    if not cat:
        return intent
    base = cat["overrides"].get(case_slug, intent)
    note = cat["notes"].get(case_slug)
    return f"{base} {note}" if note else base


# Câu bố cục khoá ở CUỐI prompt, nối sau khi Prompt Enhancer đã viết lại (PE viết lại intent thành
# văn xuôi mới nên câu "leave generous empty space" trong intent hay bị bỏ hoặc làm mờ; câu này thì
# nối sau PE nên giữ nguyên từng chữ). Chỉ case có chữ sẽ được thêm sau (launch_announcement) mới có.
LAYOUT_CASE = "launch_announcement"
LAYOUT_BASE = (
    "Composition lock: the entire product, including its outer edges, stays fully inside the frame "
    "and never touches or crosses an edge; it fills no more than about 45% of the frame width and "
    "45% of the frame height and sits in the lower right of the frame. The top third and the left "
    "side of the frame stay plain empty background with no objects, props or graphic elements, "
    "reserved for the headline text. Do not render any text, letters or numbers in the empty area."
)
# Thêm theo hình dáng đặc trưng của danh mục, chỉ để chỗ dễ tràn khung nhất được nhắc tên.
LAYOUT_SHAPE = {
    "fashion_apparel": "The whole garment is visible with its sleeves, hem and collar inside the frame.",
    "bags_footwear": "The whole item is visible with its straps, handles or laces inside the frame.",
    "home_living": "A large piece is scaled down so that all of it fits inside the frame.",
}


def layout_clause(case_slug, category=GENERIC):
    """Câu bố cục cố định cho một case, hoặc "" nếu case đó không khoá bố cục.

    Áp cho cả `generic`: lỗi tràn khung không phụ thuộc danh mục."""
    if case_slug != LAYOUT_CASE:
        return ""
    shape = LAYOUT_SHAPE.get(category)
    return f"{LAYOUT_BASE} {shape}" if shape else LAYOUT_BASE
