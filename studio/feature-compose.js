/* Ghép ảnh: combine 2 to 10 images (mode "compose"). Quality "fine" (Qwen); "FLUX (thử)" is offered only to keys the server allows (Studio.fluxOn()). No fast level. Needs gpu_v2 and the Qwen route. */
(function () {
  var S = window.Studio;
  S.register({
    id: "compose", label: "Ghép ảnh", icon: "merge", cost: 1, kind: "single",
    images: { min: 2, max: 10, hint: "Chọn từ 2 đến 10 ảnh để ghép." },
    promptRequired: true, promptMissing: ["Thiếu cách ghép", "Mô tả cách ghép, ví dụ: đặt sản phẩm ở ảnh 1 lên mặt bàn của ảnh 2."],
    placeholder: "Mô tả cách ghép, ví dụ: đặt sản phẩm ở ảnh 1 lên mặt bàn của ảnh 2…",
    emptyTitle: "Ghép nhiều ảnh thành một", emptyText: "Chọn 2 đến 10 ảnh, mô tả cách ghép, rồi bấm Gửi. Thứ tự ảnh là thứ tự trong chip.",
    enabled: function (caps) { return !!(caps.gpu_v2 && caps.qwen); },
    quality: { options: [["fine", "Tinh"], ["flux", "FLUX (thử)"]], default: "fine", needsQwen: false, hideUnless: { flux: "fluxOn" }, notes: { fine: "Tinh ghép bằng Qwen, bám chỉ dẫn tốt hơn.", flux: "Mô hình thử nghiệm, chỉ để so sánh chất lượng và tốc độ." } },
    fields: [
      { id: "ar", label: "Tỉ lệ", type: "select", options: ["Theo ảnh đầu", "1:1", "4:3", "3:4", "16:9"], default: "Theo ảnh đầu", chip: true },
      { id: "res", label: "Độ phân giải", type: "select", options: ["1024", "1536"], default: "1024", chip: true },
      { id: "seed", label: "Seed", type: "number", placeholder: "Tự chọn", adv: true }
    ],
    build: function (c) {
      var sd = S.seed(c.val("seed")); if (sd.error) return { error: sd.error };
      var body = { mode: "compose", prompt: c.prompt, images: c.ids, aspect_ratio: S.ratio(c.val("ar")), resolution: +c.val("res"), quality: c.quality };
      if (sd.seed !== undefined) body.seed = sd.seed;
      return { body: body };
    }
  });
})();
