/* Chỉnh ảnh: change one image by words (mode "edit"). Needs gpu_v2 and the Qwen route. "FLUX (thử)" is offered only to keys the server allows (Studio.fluxOn()).
   Editing by words is also just the next turn: "Sửa ảnh này" attaches the picture and puts the cursor in the composer. */
(function () {
  var S = window.Studio;
  S.register({
    id: "edit", label: "Chỉnh ảnh", icon: "edit", cost: 1, kind: "single",
    images: { min: 1, max: 1, hint: "Chọn một ảnh rồi nói điều cần đổi." },
    promptRequired: true, promptMissing: ["Thiếu chỉ dẫn", "Nói điều cần đổi trong ảnh, ví dụ: đổi màu áo thành đỏ."],
    placeholder: "Nói điều cần đổi, ví dụ: làm nền tối hơn…",
    emptyTitle: "Chỉnh sửa ảnh bằng lời", emptyText: "Chọn một ảnh và nói điều cần đổi. Giữ nguyên phần còn lại.",
    enabled: function (caps) { return !!(caps.gpu_v2 && caps.qwen); },
    quality: { options: [["fast", "Nhanh"], ["fine", "Tinh"], ["flux", "FLUX (thử)"]], default: "fast", needsQwen: false, hideUnless: { flux: "fluxOn" }, notes: { fast: "Nhanh đổi nhanh nhưng có thể lệch chỉ dẫn.", fine: "Tinh làm đúng chỉ dẫn hơn.", flux: "Mô hình thử nghiệm, chỉ để so sánh chất lượng và tốc độ." } },
    fields: [{ id: "seed", label: "Seed", type: "number", placeholder: "Tự chọn", adv: true }],
    build: function (c) {
      var sd = S.seed(c.val("seed")); if (sd.error) return { error: sd.error };
      var body = { mode: "edit", prompt: c.prompt, images: [c.ids[0]], quality: c.quality };
      if (sd.seed !== undefined) body.seed = sd.seed;
      return { body: body };
    }
  });
})();
