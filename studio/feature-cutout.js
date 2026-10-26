/* Xóa nền: background removal (mode "transparent"). Fast = rembg, fine = Qwen RGBA. Needs gpu_v2. */
(function () {
  var S = window.Studio;
  S.register({
    id: "cutout", label: "Xóa nền", icon: "cut", cost: 1, kind: "single",
    images: { min: 1, max: 1, hint: "Chọn một ảnh rồi bấm Gửi." },
    promptRequired: false, promptMissing: [],
    placeholder: "Chọn ảnh rồi bấm Gửi. Không cần mô tả.",
    emptyTitle: "Xóa nền ảnh", emptyText: "Chọn một ảnh để tách nền, hoặc kéo ảnh vào đây. Kết quả là ảnh PNG nền trong suốt.",
    enabled: function (caps) { return !!caps.gpu_v2; },
    quality: { options: [["fast", "Nhanh"], ["fine", "Tinh"]], default: "fast", needsQwen: true, notes: { fast: "Nhanh tách nền trong vài giây, viền có thể chưa mượt.", fine: "Tinh cho viền mượt, tóc và lông rõ hơn." } },
    fields: [],
    build: function (c) { return { body: { mode: "transparent", images: [c.ids[0]], quality: c.quality } }; }
  });
})();
