/* Production: 8 product pictures from 1 to 10 photos (mode "production"), then a full-quality re-render of the
   cases you pick (POST /v1/jobs/{id}/rerender). Costs 8 credits; each re-rendered picture costs 1. Needs gpu_v2 + Qwen. "FLUX (thử)" makes the whole set on FLUX.2 klein for keys the server allows (Studio.fluxOn()); re-render stays on Qwen. */
(function () {
  var S = window.Studio;
  function applyFinals(turn, rj) {
    (rj.results || []).forEach(function (r) {
      if (!r.result_url) return;
      (turn.job.results || []).forEach(function (x) { if (x.case === r.case) { x.result_url = r.result_url; x.final = true; } });
      if (turn.picked) delete turn.picked[r.case];
    });
    var n = (rj.results || []).filter(function (x) { return x.result_url; }).length;
    turn.text = "Đã làm lại " + n + " ảnh ở bản cuối.";
    turn.rerendering = false;
  }
  function rerender(t) {
    var cases = Object.keys(t.picked || {}).map(Number).sort(function (a, b) { return a - b; });
    if (!cases.length || S.busy) return;
    if (S.quotaLocked()) { S.toast("Hết lượt hôm nay. Nhắn Zalo để thêm lượt."); return; }
    S.busy = true; t.rerendering = true; t.text = "Đang gửi yêu cầu làm lại…"; t.pct = 10; S.renderAll();
    S.postJson("/v1/jobs/" + t.job.job_id + "/rerender", { cases: cases, steps: +S.val("prod", "steps") }).then(function (r) {
      if (!r.ok) { var d = S.describeError(r); S.toast(d[0] + ". " + d[1]); S.busy = false; t.text = ""; S.renderAll(); return; }
      S.track(t, r.body.job_id, { parent: t, startedAt: Date.now(), done: function (j) { applyFinals(t, j); } });
    }).catch(function () { S.toast("Không kết nối được. Thử lại sau."); S.busy = false; t.rerendering = false; t.text = ""; S.renderAll(); });
  }
  /* The set as a 4x2 grid (design: ResultGrid layout "set"): a real checkbox on every tile, "Bản cuối" tag on re-rendered ones,
     status on the left and the primary button on the right, cost note under it. */
  function renderSet(St, t) {
    var el = St.el, icon = St.icon, names = St.CASE_NAMES;
    t.picked = t.picked || {};
    var rs = (t.job.results || []).filter(function (x) { return x.result_url; }).sort(function (a, b) { return a.case - b.case; });
    var working = !!t.rerendering && St.busy, n = Object.keys(t.picked).length;
    var wrap = el("div", "setview");
    var grid = el("div", "cu-results is-set is-fill");
    rs.forEach(function (x) {
      var on = !!t.picked[x.case], isBusy = working && on;
      var fig = el("figure", "cu-tile is-set-tile" + (on ? " is-selected" : "") + (x.final ? " is-final" : "") + (isBusy ? " is-busy" : ""));
      var box = el("div", "cu-tile-img");
      if (isBusy) box.appendChild(el("span", "cu-thinking", "Đang làm lại…"));
      else {
        var a = el("a", "tile-open"); a.href = x.result_url; a.target = "_blank"; a.rel = "noopener"; a.setAttribute("aria-label", "Mở ảnh " + x.case + " ở cỡ đầy đủ");
        var im = el("img"); im.src = x.result_url; im.alt = names[x.case - 1] || ("Ảnh " + x.case); im.loading = "lazy"; a.appendChild(im); box.appendChild(a);
      }
      if (x.final) box.appendChild(el("span", "cu-tile-final", "Bản cuối"));
      if (!working) {
        var lab = el("label", "cu-tile-pick"), inp = el("input"); inp.type = "checkbox"; inp.checked = on; inp.setAttribute("aria-label", "Chọn ảnh " + x.case + " để làm lại bản cuối");
        var bx = el("span", "cu-tile-box"); bx.setAttribute("aria-hidden", "true"); bx.innerHTML = on ? icon("check", 16) : "";
        inp.addEventListener("change", function () { if (inp.checked) t.picked[x.case] = true; else delete t.picked[x.case]; St.renderCanvas(); var again = document.querySelector('.cu-tile-pick input[aria-label="Chọn ảnh ' + x.case + ' để làm lại bản cuối"]'); if (again) again.focus(); });
        lab.appendChild(inp); lab.appendChild(bx); box.appendChild(lab);
      }
      fig.appendChild(box); fig.appendChild(el("figcaption", "cu-tile-cap", names[x.case - 1] || x.name || ""));
      grid.appendChild(fig);
    });
    wrap.appendChild(grid);
    var bar = el("div", "setbar");
    bar.appendChild(el("span", "setstat", working ? (t.text || "Đang làm lại bản cuối…") : (n ? "Đã chọn " + n + " ảnh." : (t.text || "Chưa chọn ảnh nào."))));
    var btn = el("button", "cu-btn cu-btn-primary"); btn.type = "button";
    btn.textContent = n ? "Làm lại bản cuối cho " + n + " ảnh" : (working ? "Làm lại bản cuối" : "Chọn ảnh để làm lại bản cuối");
    btn.disabled = working || !n;
    btn.addEventListener("click", function () { rerender(t); });
    bar.appendChild(btn); wrap.appendChild(bar);
    wrap.appendChild(el("span", "setnote", n ? "Tốn " + n + " lượt. Ảnh gốc được giữ 24 giờ, sau đó không làm lại được." : "Mỗi ảnh làm lại tốn 1 lượt."));
    return wrap;
  }
  S.register({
    id: "prod", label: "Production", icon: "grid", cost: 8, kind: "set", renderSet: renderSet,
    images: { min: 1, max: 10, hint: "Chọn từ 1 đến 10 ảnh sản phẩm rồi bấm Gửi." },
    promptRequired: false, promptMissing: [],
    placeholder: "Ghi chú sản phẩm (không bắt buộc). Để trống: hệ thống tự nhìn ảnh để nhận diện sản phẩm.",
    emptyTitle: "Bộ 8 ảnh sản phẩm", emptyText: "Từ ảnh sản phẩm, tạo 8 kiểu ảnh dùng cho sàn, quảng cáo và mạng xã hội. Bản nháp xem nhanh cả bộ, sau đó chọn ảnh ưng ý để làm lại bản cuối.",
    kinds: S.CASE_NAMES,
    enabled: function (caps) { return !!(caps.gpu_v2 && caps.qwen); },
    quality: { options: [["fast", "Nháp"], ["fine", "Bản cuối"], ["flux", "FLUX (thử)"]], default: "fast", needsQwen: false, hideUnless: { flux: "fluxOn" }, notes: { fast: "Nháp để xem nhanh cả bộ. Sau đó chọn ảnh ưng ý để làm lại bản cuối.", fine: "Bản cuối chậm hơn, dùng khi đã chọn ảnh ưng ý.", flux: "Mô hình thử nghiệm, chỉ để so sánh chất lượng và tốc độ." } },
    fields: [
      { id: "ar", label: "Tỉ lệ", type: "select", options: ["Theo ảnh gốc", "1:1", "4:3", "3:4", "16:9", "9:16"], default: "Theo ảnh gốc", chip: true },
      { id: "res", label: "Độ phân giải", type: "select", options: ["1024", "1536"], default: "1024", chip: true },
      { id: "steps", label: "Số bước (bản cuối)", type: "select", options: ["20", "30", "40"], default: "40", adv: true },
      { id: "seed", label: "Seed", type: "number", placeholder: "Tự chọn", adv: true },
      { id: "rmbg", label: "Tách nền ảnh gốc trước khi tạo", type: "check", default: true, adv: true }
    ],
    build: function (c) {
      var sd = S.seed(c.val("seed")); if (sd.error) return { error: sd.error };
      var body = { mode: "production", images: c.ids, aspect_ratio: S.ratio(c.val("ar")), resolution: +c.val("res"), steps: +c.val("steps"), quality: c.quality, remove_bg: !!c.val("rmbg") };
      if (c.prompt) body.prompt = c.prompt;
      if (sd.seed !== undefined) body.seed = sd.seed;
      return { body: body };
    },
    actions: function (St, t, item, act) {
      if (!item || !item.case || !t.job) return;
      t.picked = t.picked || {};
      var on = !!t.picked[item.case];
      act(on ? "Bỏ chọn ảnh này" : "Chọn làm bản cuối", "grid", function () { if (on) delete t.picked[item.case]; else t.picked[item.case] = true; St.renderCanvas(); });
      var n = Object.keys(t.picked).length;
      if (n) act("Làm lại bản cuối cho " + n + " ảnh (tốn " + n + " lượt)", "refresh", function () { rerender(t); }, true);
    }
  });
})();
