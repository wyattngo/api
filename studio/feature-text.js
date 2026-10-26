/* Tạo ảnh: text to image (mode "text"). Always on; the fine level needs the Qwen route; "FLUX (thử)" shows only for allowlisted keys. */
(function () {
  var S = window.Studio;
  S.register({
    id: "text", label: "Tạo ảnh", icon: "text", cost: 1, kind: "single",
    images: { min: 0, max: 0, hint: "" },
    promptRequired: true, promptMissing: ["Thiếu mô tả", "Nhập mô tả ảnh rồi bấm Gửi."],
    placeholder: "Mô tả ảnh bạn muốn tạo…",
    emptyTitle: "Bạn muốn tạo ảnh gì?", emptyText: "Mô tả ảnh ở ô soạn bên dưới, hoặc thử một gợi ý.",
    samples: ["Ly cà phê sữa đá bên cửa sổ, nắng sáng", "Giày chạy bộ màu cam trên nền xám, ảnh studio", "Hộp quà giấy kraft buộc dây gai, nền gỗ sáng"],
    enabled: function () { return true; },
    quality: { options: [["fast", "Nhanh"], ["fine", "Tinh"], ["flux", "FLUX (thử)"]], default: "fast", needsQwen: true, hideUnless: { flux: "fluxOn" },
      notes: { fast: "Nhanh cho ra ảnh trong vài giây.", fine: "Tinh chậm hơn, chi tiết hơn.", flux: "Mô hình thử nghiệm, chỉ để so sánh chất lượng và tốc độ." } },
    fields: [
      { id: "ar", label: "Tỉ lệ", type: "select", options: ["1:1", "4:3", "3:4", "16:9", "9:16"], default: "1:1", chip: true },
      { id: "res", label: "Độ phân giải", type: "select", options: ["1024", "1536", "2048"], default: "1024", chip: true },
      { id: "steps", label: "Số bước (áp dụng cho mức Tinh)", type: "select", options: ["20", "30", "40"], default: "30", adv: true },
      { id: "seed", label: "Seed", type: "number", placeholder: "Tự chọn", adv: true },
      { id: "neg", label: "Điều cần tránh", type: "text", placeholder: "Ví dụ: chữ, logo, ảnh mờ", adv: true }
    ],
    build: function (c) {
      var body = { prompt: c.prompt, aspect_ratio: c.val("ar"), resolution: +c.val("res"), steps: +c.val("steps"), quality: c.quality };
      var sd = S.seed(c.val("seed")); if (sd.error) return { error: sd.error }; if (sd.seed !== undefined) body.seed = sd.seed;
      var neg = String(c.val("neg") || "").trim(); if (neg) body.negative_prompt = neg;
      return { body: body };
    }
  });
})();
