/* Studio core: everything the five features share. Logic moved over from site/index.html (sign-in, api(),
   job polling and resume, uploads, error texts, history, theme); the screen is a conversation plus a canvas.
   Features register themselves with Studio.register({...}) from feature-*.js; nothing here knows one by name. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var root = document.documentElement;
  var CLERK_PK = "pk_live_Y2xlcmsuY3V0aW4uZGV2JA", CLERK_TEMPLATE = "ai-cutin-dev";
  var ZALO = "https://zalo.me/0907674430";
  var CASE_NAMES = ["Nền trắng, chính diện", "Ảnh hero thương hiệu", "Cận chi tiết", "Flat lay từ trên xuống", "Trong bối cảnh sử dụng", "Có người dùng (tay)", "Concept lợi ích chính", "Ra mắt, chừa chỗ cho chữ"];
  var ICONS = {
    text: "M4 7V5h16v2M12 5v14M9 19h6", merge: "M8 8h11v11H8zM5 15V5h10", edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
    grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z", cut: "M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12",
    image: "M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 16l-5-5-8 8",
    sliders: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4", bolt: "M13 2 4 14h7l-1 8 9-12h-7z", chevron: "m6 9 6 6 6-6",
    download: "M12 4v12M7 11l5 5 5-5M4 20h16", refresh: "M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5",
    clip: "m21 11-8.6 8.6a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.6 8.6a2 2 0 0 1-3-3L15 7", send: "M12 19V5M5 12l7-7 7 7", close: "M6 6l12 12M18 6 6 18", plus: "M12 5v14M5 12h14", check: "M5 12.5l4.5 4.5L19 7.5",
    sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
    moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z", left: "m15 6-6 6 6 6", history: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2", trash: "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14", resize: "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7", msg: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
  };
  function icon(n, size) {
    return '<svg width="' + (size || 18) + '" height="' + (size || 18) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="' + (ICONS[n] || ICONS.image) + '"/></svg>';
  }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function uuid() { return crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2); }
  /* storage: every access can throw (private mode, blocked site data) */
  function load(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } }
  function save(k, v) { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch (e) {} }

  var S = window.Studio = {
    modes: {}, order: [], caps: { gpu_v2: false, qwen: false, flux: false }, signedIn: false, user: "",
    mode: "text", turns: [], sel: null, imgs: { cutout: [], edit: [], compose: [], prod: [] }, vals: {}, nextId: 1, pollToken: 0, busy: false, historyLoaded: false,
    $: $, $$: $$, el: el, icon: icon, uuid: uuid, load: load, save: save, CASE_NAMES: CASE_NAMES, ZALO: ZALO
  };
  S.seed = function (v) {
    v = String(v == null ? "" : v).trim(); if (v === "") return {};
    if (!/^-?\d+$/.test(v)) return { error: ["Seed chưa hợp lệ", "Seed phải là số nguyên, hoặc để trống để chọn ngẫu nhiên."] };
    return { seed: parseInt(v, 10) };
  };
  S.ratio = function (v) { return /^Theo/.test(v) ? "auto" : v; };
  S.register = function (def) { S.modes[def.id] = def; S.order.push(def.id); };
  S.enabled = function (id) { var d = S.modes[id]; return !!d && d.enabled(S.caps); };
  S.qwenOn = function () { return !!(S.caps.gpu_v2 && S.caps.qwen); };
  S.fluxOn = function () { return !!(S.caps.gpu_v2 && S.caps.flux); }; /* FLUX trial: the server says true only for allowlisted keys */
  /* a quality option listed in quality.hideUnless is not offered at all (no "Sắp có") until its check says so */
  function qualityShown(d, v) { var chk = d.quality.hideUnless && d.quality.hideUnless[v]; return !chk || !!S[chk](); }
  function cur() { return S.modes[S.mode]; }

  /* ---------- toast, theme ---------- */
  var tt;
  function toast(m) { var t = $("#toast"); t.textContent = m; t.classList.add("on"); clearTimeout(tt); tt = setTimeout(function () { t.classList.remove("on"); }, 2600); }
  S.toast = toast;
  var tbtn = $("#theme"), savedTheme = load("cutin-theme");
  if (savedTheme === "light" || savedTheme === "dark") root.setAttribute("data-theme", savedTheme);
  function isDark() { var t = root.getAttribute("data-theme"); return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; }
  function syncTheme() { tbtn.innerHTML = icon(isDark() ? "sun" : "moon"); tbtn.setAttribute("aria-label", isDark() ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối"); }
  tbtn.addEventListener("click", function () { var n = isDark() ? "light" : "dark"; root.setAttribute("data-theme", n); save("cutin-theme", n); syncTheme(); });

  /* ---------- sign-in (Clerk shares the cutin.dev session cookie) and api() ---------- */
  function signIn() {
    if (window.Clerk && window.Clerk.loaded) window.Clerk.openSignIn({});
    else toast("Đang tải đăng nhập, thử lại sau vài giây.");
  }
  S.signIn = signIn;
  S.setUser = function (name) {
    S.signedIn = !!name; S.user = name || ""; renderAccount(); loadCaps();
    if (S.signedIn) { loadHistory(); } else { unlockQuota(); S.turns = []; S.sel = null; S.historyLoaded = false; renderAll(); }
  };
  function syncUser() {
    var u = window.Clerk && window.Clerk.user;
    S.setUser(u ? (u.firstName || (u.primaryEmailAddress && u.primaryEmailAddress.emailAddress) || "Tài khoản") : "");
  }
  function initClerk() {
    var s = document.createElement("script"); s.async = true; s.crossOrigin = "anonymous";
    s.setAttribute("data-clerk-publishable-key", CLERK_PK);
    s.src = "https://clerk.cutin.dev/npm/@clerk/clerk-js@5/dist/clerk.browser.js";
    s.onload = function () {
      window.Clerk.load().then(function () { syncUser(); window.Clerk.addListener(syncUser); })
        .catch(function () { toast("Không tải được đăng nhập. Tải lại trang."); });
    };
    s.onerror = function () { toast("Không tải được đăng nhập. Kiểm tra mạng rồi tải lại trang."); };
    document.head.appendChild(s);
  }
  /* ---------- quota lock: the backend only says "out of credits" by answering 429 with Retry-After >= 600 ---------- */
  var QUOTA_KEY = "cutin-quota-until", quotaTimer = null;
  function quotaUntil() { return parseInt(load(QUOTA_KEY) || "0", 10) || 0; }
  S.quotaLocked = function () { return quotaUntil() > Date.now(); };
  function scheduleUnlock() {
    clearTimeout(quotaTimer);
    var u = quotaUntil(); if (!u) return;
    var ms = u - Date.now(); if (ms <= 0) { unlockQuota(); return; }
    quotaTimer = setTimeout(unlockQuota, Math.min(ms, 2147483000));
  }
  function lockQuota(secs) { save(QUOTA_KEY, String(Date.now() + Math.min(secs, 48 * 3600) * 1000)); scheduleUnlock(); renderComposer(); }
  function unlockQuota() { clearTimeout(quotaTimer); save(QUOTA_KEY, ""); renderComposer(); }
  S.lockQuota = lockQuota; S.unlockQuota = unlockQuota;

  function api(path, opt) {
    opt = opt || {};
    var C = window.Clerk;
    var tok = C && C.session ? C.session.getToken({ template: CLERK_TEMPLATE }).catch(function () { return ""; }) : Promise.resolve("");
    return tok.then(function (t) {
      /* no token (signed out, or the public /v1/capabilities call): send no Authorization header at all */
      opt.headers = Object.assign(t ? { Authorization: "Bearer " + t } : {}, opt.headers || {});
      return fetch(path, opt);
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) { return { status: r.status, ok: r.ok, body: b, retry: parseInt(r.headers.get("Retry-After") || "0", 10) || 0 }; });
    }).then(function (res) {
      if (res.status === 429 && res.retry >= 600) lockQuota(res.retry); /* out of credits: lock the composer (browser side only) */
      return res;
    });
  }
  S.api = api;
  function postJson(path, body, idem) {
    var h = { "Content-Type": "application/json" }; if (idem) h["Idempotency-Key"] = uuid();
    return api(path, { method: "POST", headers: h, body: JSON.stringify(body) });
  }
  S.postJson = postJson;
  function describeError(r) {
    var msg = (r.body && r.body.error) || "";
    if (r.status === 401) return ["Phiên đăng nhập đã hết hạn", "Đăng nhập lại để tiếp tục. Nếu vẫn lỗi, nhắn Zalo để được hỗ trợ.", true];
    if (r.status === 400 && /quality "flux" is not available/.test(msg)) return ["Mức FLUX (thử) chưa dùng được", "Tài khoản này chưa được bật mức thử nghiệm. Chọn mức Nhanh hoặc Tinh.", false];
    if (r.status === 400 && /quality "fast" is not available for mode/.test(msg)) return ["Ghép ảnh chỉ có mức Tinh", "Mức Nhanh chưa có cho tính năng này.", false];
    if (r.status === 400 && /original images have expired/.test(msg)) return ["Ảnh gốc đã hết hạn", "Ảnh gốc chỉ lưu 24 giờ nên không làm lại bản cuối được. Hãy tạo bộ mới.", false];
    if (r.status === 400 && /mode "(transparent|edit|compose|production)".*not available yet/.test(msg)) return ["Tính năng này chưa bật", "Sẽ có sau khi nâng cấp máy chủ GPU.", false];
    if (r.status === 400 && /not available yet/.test(msg)) return ["Mức Tinh chưa bật", "Chọn mức Nhanh để tạo ảnh ngay. Mức Tinh sẽ có sau.", false];
    if (r.status === 400 && /Only PNG/.test(msg)) return ["Định dạng ảnh chưa hỗ trợ", "Chọn ảnh PNG, JPEG hoặc WebP.", false];
    if (r.status === 400 && /unknown or expired/.test(msg)) return ["Ảnh đã hết hạn", "Ảnh tải lên chỉ lưu 24 giờ. Chọn lại ảnh rồi thử lại.", false];
    if (r.status === 413 && /Image/.test(msg)) return ["Ảnh lớn hơn 8 MB", "Chọn ảnh nhỏ hơn hoặc nén ảnh trước.", false];
    if (r.status === 429 && /upload/i.test(msg)) return ["Đã hết lượt tải ảnh lên hôm nay", "Thử lại sau, hoặc nhắn Zalo để nâng hạn mức.", false];
    if (r.status === 400) return ["Yêu cầu chưa hợp lệ", msg || "Kiểm tra lại các ô đã nhập.", false];
    if (r.status === 413) return ["Mô tả quá dài", "Rút gọn mô tả rồi thử lại.", false];
    if (r.status === 429) return [r.retry >= 600 ? "Đã hết lượt hôm nay" : "Đang chạy quá nhiều ảnh cùng lúc", r.retry >= 600 ? "Lượt sẽ được làm mới sau 24 giờ. Một bộ 8 ảnh cần 8 lượt. Cần thêm lượt thì nhắn Zalo." : "Đợi vài giây cho ảnh trước xong rồi bấm lại.", false, r.retry >= 600];
    if (r.status === 503) return ["Hệ thống đang đầy", "Thử lại sau " + (r.retry || 30) + " giây.", false];
    return ["Chưa tạo được ảnh", msg || "Có lỗi từ máy chủ (" + r.status + "). Thử lại sau ít phút.", false];
  }
  S.describeError = describeError;

  /* ---------- capabilities ---------- */
  function loadCaps() {
    api("/v1/capabilities").then(function (r) { var c = r.body || {}; S.caps.gpu_v2 = !!c.gpu_v2; S.caps.qwen = !!c.qwen; S.caps.flux = !!c.flux;
      /* signed in: the server says how much of the rolling 24h allowance is used. Lock before a send; a lock set by an old 429 lifts once there is room again */
      if (c.quota && c.quota.limit > 0) { if (c.quota.used >= c.quota.limit) lockQuota(3600); else if (S.quotaLocked()) unlockQuota(); }
      applyCaps(); }).catch(function () { applyCaps(); });
  }
  function applyCaps() {
    if (!S.enabled(S.mode)) S.mode = "text";
    renderAll();
  }

  /* ---------- option values (remembered from the last turn) ---------- */
  var OPTS_KEY = "cutin-studio-opts";
  (function () { try { S.vals = JSON.parse(load(OPTS_KEY)) || {}; } catch (e) { S.vals = {}; } })();
  S.val = function (mode, id) {
    var d = S.modes[mode], v = S.vals[mode] && S.vals[mode][id];
    if (v !== undefined) return v;
    var f = d.fields.filter(function (x) { return x.id === id; })[0];
    if (f) return f.type === "check" ? !!f.default : (f.default !== undefined ? f.default : "");
    return id === "quality" && d.quality ? d.quality.default : "";
  };
  S.setVal = function (mode, id, v) { (S.vals[mode] = S.vals[mode] || {})[id] = v; save(OPTS_KEY, JSON.stringify(S.vals)); };
  function qualityOf(mode) {
    var d = S.modes[mode]; if (!d.quality) return d.fixedQuality;
    var q = S.val(mode, "quality"); if (q === "fine" && d.quality.needsQwen && !S.qwenOn()) q = d.quality.fallback || d.quality.default;
    if (!qualityShown(d, q)) q = d.quality.fallback || d.quality.default; /* e.g. "flux" after sign-out or a key without access */
    return q;
  }
  S.quality = qualityOf;

  /* ---------- results of a turn ---------- */
  function resultsOf(turn) {
    var j = turn.job; if (!j) return [];
    if (j.results && j.results.length) {
      return j.results.filter(function (x) { return x.result_url; }).sort(function (a, b) { return a.case - b.case; })
        .map(function (x) { return { url: x.result_url, label: x.case + ". " + (CASE_NAMES[x.case - 1] || x.name || ""), case: x.case, final: x.final }; });
    }
    return j.result_url ? [{ url: j.result_url, label: turn.prompt || S.modes[turn.mode].label }] : [];
  }
  S.resultsOf = resultsOf;
  function turnById(id) { return S.turns.filter(function (t) { return t.id === id; })[0]; }
  S.turnById = turnById;
  function selected() {
    if (!S.sel) return null;
    var t = turnById(S.sel.turn); if (!t) return null;
    var rs = resultsOf(t);
    return { turn: t, results: rs, item: rs[S.sel.idx] || rs[0] || null };
  }
  S.selected = selected;
  function variants() {
    var out = [];
    S.turns.forEach(function (t) { resultsOf(t).forEach(function (r, i) { out.push({ turn: t, idx: i, item: r }); }); });
    return out.slice(-40);
  }

  /* ---------- mode, chips, attachments, composer ---------- */
  S.setMode = function (id) { if (!S.enabled(id)) { toast("Tính năng này sắp có."); return; } S.mode = id; closePops(false); renderAll(); $("#prompt").focus(); };
  function qualityMulti(d) { return !!(d.quality && d.quality.options.filter(function (o) { return qualityShown(d, o[0]); }).length > 1); } /* one visible level (e.g. Ghép ảnh without FLUX) needs no control */
  function chipSummary(d) {
    var parts = d.fields.filter(function (f) { return f.chip; }).map(function (f) { return S.val(d.id, f.id); }).filter(Boolean);
    if (qualityMulti(d)) { var q = qualityOf(d.id), lab = d.quality.options.filter(function (o) { return o[0] === q; })[0]; parts.push(lab ? lab[1] : q); }
    return parts.join(" · ") || "Tùy chọn";
  }
  S.summaryOf = function (mode, vals, q) {
    var d = S.modes[mode], parts = d.fields.filter(function (f) { return f.chip; }).map(function (f) { return vals && vals[f.id] != null && vals[f.id] !== "" ? vals[f.id] : S.val(mode, f.id); }).filter(Boolean);
    var lab = d.quality && q ? d.quality.options.filter(function (o) { return o[0] === q; })[0] : null; if (lab) parts.push(lab[1]);
    return parts.join(" · ");
  };

  /* ---------- popups: ONE open at a time (mode, opts, quality) plus the account menu ---------- */
  var pop = { name: null }, BUILD = {};
  var appEl = $("#app");
  function setOpen(n) { appEl.dataset.open = n || ""; }
  function closePops(refocus) {
    var name = pop.name; pop.name = null; setOpen("");
    $("#opts").hidden = true; $("#acct-menu").hidden = true; $("#acct").setAttribute("aria-expanded", "false"); $("#histbtn").setAttribute("aria-expanded", "false");
    renderChips();
    if (refocus && name) { var c = name === "acct" ? $("#acct") : name === "hist" ? $("#histbtn") : $('[data-pop="' + name + '"]'); if (c) c.focus(); }
  }
  function openPop(name) {
    if (pop.name === name) { closePops(true); return; }
    closePops(false);
    pop.name = name; setOpen(name);
    var o = $("#opts"); o.innerHTML = ""; BUILD[name](o); o.hidden = false; placePanel(); renderChips();
    var first = $("button:not(:disabled), select", o); if (first) first.focus();
  }
  function refreshPop() { if (pop.name && BUILD[pop.name]) { var o = $("#opts"); o.innerHTML = ""; BUILD[pop.name](o); } renderChips(); }
  function chip(name, ic, text, nm) {
    var open = pop.name === name, c = el("button", "cu-optchip" + (open ? " is-open" : "")); c.type = "button";
    c.setAttribute("aria-haspopup", "dialog"); c.setAttribute("aria-expanded", open ? "true" : "false"); c.dataset.pop = name;
    c.innerHTML = icon(ic, 16) + '<span class="' + (nm ? "nm" : "") + '"></span>' + icon("chevron", 16); c.querySelector("span").textContent = text;
    c.addEventListener("click", function (e) { e.stopPropagation(); openPop(name); });
    return c;
  }
  function renderChips() {
    var d = cur(), box = $("#chips"); box.innerHTML = "";
    box.appendChild(chip("mode", d.icon, d.label, true));
    if (d.fields.length || qualityMulti(d)) box.appendChild(chip("opts", "sliders", chipSummary(d)));
    var fx = box.parentNode; fx.classList.toggle("noscroll", box.scrollWidth <= box.clientWidth + 1);
  }
  function placePanel() {
    /* wide screens: the card sits above the composer; narrow screens: CSS makes it a bottom sheet */
    var dock = $("#dock"), o = $("#opts"), r = appEl.getBoundingClientRect();
    o.style.setProperty("--pop-bottom", Math.max(8, r.bottom - dock.getBoundingClientRect().top + 8) + "px");
  }
  /* every popup is the same card: a title, a 44px close button, a body */
  function sheet(title) {
    var card = el("div", "cu-sheet opts-card"), head = el("div", "cu-sheet-head"), h = el("h3", "", title), x = el("button", "cu-ibtn cu-ibtn-quiet bare");
    x.type = "button"; x.setAttribute("aria-label", "Đóng"); x.innerHTML = icon("close"); x.addEventListener("click", function () { closePops(true); });
    card.setAttribute("role", "dialog"); card.setAttribute("aria-label", title);
    head.appendChild(h); head.appendChild(x); card.appendChild(head);
    var body = el("div", "cu-sheet-body"); card.appendChild(body);
    return { card: card, body: body };
  }
  function fieldEl(d, f) {
    var wrap = f.type === "check" ? el("label", "ck") : el("label", "fld"), v = S.val(d.id, f.id), inp;
    if (f.type === "select") {
      inp = el("select"); f.options.forEach(function (o) { var op = el("option", "", o); op.value = o; if (String(v) === o) op.selected = true; inp.appendChild(op); });
    } else if (f.type === "check") {
      inp = el("input"); inp.type = "checkbox"; inp.checked = !!v;
    } else if (f.type === "text") {
      inp = el("textarea"); inp.value = v || ""; inp.placeholder = f.placeholder || "";
    } else { inp = el("input"); inp.type = "number"; inp.value = v === "" ? "" : v; inp.placeholder = f.placeholder || ""; }
    inp.addEventListener("change", function () { S.setVal(d.id, f.id, f.type === "check" ? inp.checked : inp.value); renderChips(); });
    if (f.type === "check") { wrap.appendChild(inp); wrap.appendChild(el("span", "", f.label)); } else { wrap.appendChild(el("span", "", f.label)); wrap.appendChild(inp); }
    return wrap;
  }
  BUILD.mode = function (o) {
    var s = sheet("Chế độ"), list = el("div", "modelist"); list.setAttribute("role", "group"); list.setAttribute("aria-label", "Chế độ");
    S.order.forEach(function (id) {
      var d = S.modes[id], on = S.enabled(id), b = el("button", "cu-menu-item modeitem" + (id === S.mode ? " is-active" : "")); b.type = "button"; b.disabled = !on;
      b.setAttribute("aria-pressed", id === S.mode ? "true" : "false"); if (id === S.mode) b.setAttribute("aria-current", "page");
      b.innerHTML = icon(d.icon) + '<span class="nm"></span>'; $(".nm", b).textContent = d.label;
      if (!on) b.appendChild(el("span", "cu-mode-soon", "Sắp có"));
      b.addEventListener("click", function () { S.setMode(id); });
      list.appendChild(b);
    });
    s.body.appendChild(list); o.appendChild(s.card);
  };
  function qualityField(d) {
    var q = qualityOf(d.id), seg = el("div", "cu-seg"); seg.setAttribute("role", "group"); seg.setAttribute("aria-label", "Chất lượng");
    d.quality.options.filter(function (op) { return qualityShown(d, op[0]); }).forEach(function (op) {
      var b = el("button", "cu-seg-item" + (op[0] === q ? " is-active" : "")); b.type = "button"; b.setAttribute("aria-pressed", op[0] === q ? "true" : "false");
      var off = op[0] === "fine" && d.quality.needsQwen && !S.qwenOn(); b.textContent = op[1] + (off ? " · Sắp có" : ""); b.disabled = off;
      b.addEventListener("click", function () { S.setVal(d.id, "quality", op[0]); refreshPop(); });
      seg.appendChild(b);
    });
    var f = el("div", "fld"); f.appendChild(el("span", "", "Chất lượng")); f.appendChild(seg);
    if (d.quality.notes) f.appendChild(el("span", "note", d.quality.notes[q] || ""));
    return f;
  }
  BUILD.opts = function (o) {
    var d = cur(), s = sheet("Tùy chọn");
    d.fields.filter(function (f) { return !f.adv; }).forEach(function (f) { s.body.appendChild(fieldEl(d, f)); });
    if (qualityMulti(d)) s.body.appendChild(qualityField(d));
    var adv = d.fields.filter(function (f) { return f.adv; });
    if (adv.length) {
      var det = el("details", "adv"), sm = el("summary", "", "Nâng cao"), ab = el("div", "adv-body");
      adv.forEach(function (f) { ab.appendChild(fieldEl(d, f)); }); det.appendChild(sm); det.appendChild(ab); s.body.appendChild(det);
    }
    o.appendChild(s.card);
  };
  document.addEventListener("click", function (e) { if (!e.target.closest("#opts, #acct-menu, #acct, #side, #histbtn, #view, .cu-optchip")) closePops(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && pop.name) closePops(true); });

  function attached() { return S.imgs[S.mode] || []; }
  function renderAttached() {
    var box = $("#attached"); box.innerHTML = "";
    attached().forEach(function (it, i) {
      var chip = el("span", "cu-chip"), name = el("span", "cu-chip-name", cur().images.max > 1 ? (i + 1) + ". " + it.name : it.name);
      chip.appendChild(name);
      if (cur().images.max > 1 && i > 0) {
        var up = el("button", "cu-chip-x"); up.type = "button"; up.setAttribute("aria-label", "Đưa ảnh " + (i + 1) + " lên trước"); up.innerHTML = icon("left", 16);
        up.addEventListener("click", function () { var a = attached(), t = a[i - 1]; a[i - 1] = a[i]; a[i] = t; renderAttached(); });
        chip.appendChild(up);
      }
      var x = el("button", "cu-chip-x"); x.type = "button"; x.setAttribute("aria-label", "Bỏ ảnh " + (i + 1)); x.textContent = "×";
      x.addEventListener("click", function () { attached().splice(i, 1); renderAttached(); renderComposer(); });
      chip.appendChild(x); box.appendChild(chip);
    });
  }
  S.addFiles = function (files) {
    var d = cur();
    if (d.images.max === 0) { var t = ["edit", "cutout"].filter(S.enabled)[0]; if (!t) { toast("Chọn ảnh chưa dùng được lúc này."); return; } S.mode = t; d = cur(); renderAll(); }
    var list = S.imgs[S.mode], big = false;
    Array.prototype.slice.call(files).forEach(function (f) {
      if (!/^image\/(png|jpeg|webp)$/.test(f.type)) { toast("Chọn ảnh PNG, JPEG hoặc WebP."); return; }
      if (f.size > 8 * 1024 * 1024) { big = true; return; }
      if (list.length >= d.images.max) { if (d.images.max === 1) list.length = 0; else { toast("Đã đủ " + d.images.max + " ảnh."); return; } }
      list.push({ file: f, src: URL.createObjectURL(f), name: f.name });
    });
    if (big) toast("Có ảnh lớn hơn 8 MB, đã bỏ qua.");
    renderAttached(); renderComposer();
  };
  function renderComposer() {
    var d = cur(), on = S.enabled(d.id), locked = S.quotaLocked(), usable = on && !locked, ta = $("#prompt");
    ta.placeholder = locked ? "Hết lượt hôm nay" : (on ? d.placeholder : "Tính năng này sắp có."); ta.disabled = !usable; /* a disabled textarea keeps what was typed */
    $("#composer").classList.toggle("is-disabled", !usable);
    var att = $("#attach"); att.hidden = d.images.max === 0 && !S.enabled("edit") && !S.enabled("cutout"); att.disabled = !usable;
    $("#cost").textContent = locked ? "Hết lượt" : (on ? "Tốn " + d.cost + " lượt" : "");
    var send = $("#send"); send.innerHTML = icon("send", 20); send.disabled = !usable || S.busy;
    autosize();
  }
  function autosize() {
    var ta = $("#prompt"); ta.style.height = "auto";
    var box = Math.ceil(parseFloat(getComputedStyle(ta).height) || 0);
    ta.style.height = Math.max(ta.scrollHeight, box) + "px";
  }
  $("#attach").innerHTML = icon("clip", 20);
  $("#attach").addEventListener("click", function () { $("#file").click(); });
  $("#file").addEventListener("change", function (e) { S.addFiles(e.target.files); e.target.value = ""; });
  $("#prompt").addEventListener("input", autosize);
  $("#prompt").addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
  $("#send").addEventListener("click", submit);

  /* ---------- submit and run ---------- */
  function ctxOf(req) {
    var d = S.modes[req.mode];
    return { prompt: req.prompt, ids: req.ids || req.imgs.map(function (it, i) { return it.imageId || "pending-" + i; }), quality: req.quality,
      val: function (id) { return req.vals[id]; } };
  }
  function snapshot(mode) {
    var d = S.modes[mode], vals = {};
    d.fields.forEach(function (f) { vals[f.id] = S.val(mode, f.id); });
    return { mode: mode, prompt: $("#prompt").value.trim(), imgs: S.imgs[mode] ? S.imgs[mode].slice() : [], vals: vals, quality: qualityOf(mode) };
  }
  function submit() {
    var d = cur();
    if (S.busy) return;
    if (S.quotaLocked()) { toast("Hết lượt hôm nay. Nhắn Zalo để thêm lượt."); return; }
    if (!S.enabled(d.id)) { toast("Tính năng này sắp có."); return; }
    if (!S.signedIn) { toast("Đăng nhập để tạo ảnh."); signIn(); return; }
    var req = snapshot(d.id);
    var err = checkReq(d, req); if (err) { toast(err[0] + ". " + err[1]); return; }
    run(req, true);
  }
  function checkReq(d, req) {
    if (req.imgs.length < d.images.min) return [d.images.min > 1 ? "Cần ít nhất " + d.images.min + " ảnh" : "Chưa có ảnh", d.images.hint];
    if (d.promptRequired && !req.prompt) return [d.promptMissing[0], d.promptMissing[1]];
    var b = d.build(ctxOf(req)); if (b.error) return b.error;
    return null;
  }
  S.run = function (req) { return run(req, false); };
  function ensureImage(item) {
    if (item.imageId) return Promise.resolve({ ok: true, id: item.imageId });
    return api("/v1/uploads", { method: "POST", headers: { "Content-Type": item.file.type || "application/octet-stream" }, body: item.file }).then(function (u) {
      if (!u.ok) return { ok: false, r: u };
      item.imageId = u.body.image_id; return { ok: true, id: item.imageId };
    });
  }
  function userText(d, req) {
    return req.prompt || ({ cutout: "Xóa nền ảnh này", prod: "Tạo bộ 8 ảnh sản phẩm" }[d.id]) || d.label;
  }
  function run(req, clear) {
    var d = S.modes[req.mode];
    if (S.quotaLocked()) { toast("Hết lượt hôm nay. Nhắn Zalo để thêm lượt."); return; }
    var turn = { id: S.nextId++, mode: req.mode, prompt: userText(d, req), atts: req.imgs.map(function (i) { return i.name; }), req: req, status: "busy", pct: 6, text: req.imgs.length ? "Đang tải ảnh lên…" : "Đang gửi yêu cầu…", startedAt: Date.now() };
    S.turns.push(turn); S.sel = { turn: turn.id, idx: 0 }; S.busy = true;
    if (clear) { $("#prompt").value = ""; if (S.imgs[req.mode]) S.imgs[req.mode] = []; }
    closePops(false); renderAll();
    function fail(r) {
      if (r.status === 400 && /unknown or expired/.test((r.body && r.body.error) || "")) req.imgs.forEach(function (it) { if (it.file) delete it.imageId; });
      if (clear && r.status === 429 && r.retry >= 600) { $("#prompt").value = req.prompt === d.label ? "" : req.prompt; if (S.imgs[req.mode]) S.imgs[req.mode] = req.imgs.slice(); } /* keep what the person typed */
      var e = describeError(r); turn.status = "error"; turn.err = e; S.busy = false; save("cutin-last-job", ""); renderAll();
    }
    Promise.all(req.imgs.map(ensureImage)).then(function (res) {
      var bad = res.filter(function (x) { return !x.ok; })[0];
      if (bad) { fail(bad.r); return null; }
      turn.text = "Đang gửi yêu cầu…"; turn.pct = 12; renderTurns(); renderCanvas();
      var b = d.build(ctxOf({ mode: req.mode, prompt: req.prompt, vals: req.vals, quality: req.quality, imgs: req.imgs, ids: res.map(function (x) { return x.id; }) }));
      return postJson("/v1/generate", b.body, true);
    }).then(function (r) {
      if (!r) return;
      if (!r.ok) { fail(r); return; }
      track(turn, r.body.job_id, {});
    }).catch(function () { turn.status = "error"; turn.err = ["Không kết nối được", "Kiểm tra mạng rồi bấm Thử lại.", false]; S.busy = false; renderAll(); });
  }

  /* poll one job (a set of 8 takes minutes); o.parent / o.done are for a re-render of picked cases */
  function track(turn, id, o) {
    var token = ++S.pollToken, fails = 0, set = S.modes[turn.mode].kind === "set", gap = set ? 4000 : 3000, limit = (set ? 45 : 15) * 60, startedAt = o.startedAt || Date.now();
    var target = o.parent || turn;
    function say(text, pct) { target.text = text; target.pct = pct; renderTurns(); renderCanvas(); }
    function end() { S.busy = false; renderAll(); }
    function failed(title, text) { target.status = o.parent ? "done" : "error"; target.err = o.parent ? null : [title, text, false]; if (o.parent) toast(title + ". " + text); save("cutin-last-job", ""); end(); }
    save("cutin-last-job", o.parent ? "" : id);
    function tick() {
      if (token !== S.pollToken) return;
      var secs = Math.max(0, Math.round((Date.now() - startedAt) / 1000)), mins = Math.floor(secs / 60);
      if (secs > limit) { failed("Quá thời gian chờ", "Chưa xong sau " + (set ? 45 : 15) + " phút. Thử tạo lại."); return; }
      api("/v1/jobs/" + id).then(function (r) {
        if (token !== S.pollToken) return;
        if (r.status === 404) { save("cutin-last-job", ""); target.status = target.job ? "done" : "error"; if (!target.job) target.err = ["Không tìm thấy ảnh", "Yêu cầu không còn trên máy chủ. Thử tạo lại.", false]; end(); return; }
        if (!r.ok) { var d = describeError(r); target.status = o.parent ? "done" : "error"; if (!o.parent) target.err = d; else toast(d[0]); save("cutin-last-job", ""); end(); return; }
        fails = 0; var j = r.body;
        if (j.status === "completed") {
          save("cutin-last-job", "");
          if (o.done) { o.done(j); target.status = "done"; target.text = ""; }
          else { turn.job = j; turn.status = "done"; turn.text = ""; S.sel = { turn: turn.id, idx: 0 }; }
          end(); return;
        }
        if (j.status === "failed") { failed("Không tạo được ảnh", (j.error || "Máy chủ gặp lỗi khi tạo ảnh.") + " Bấm Thử lại."); return; }
        var cold = j.status === "queued" && secs > 20 ? (set ? " Máy chủ đang khởi động, lần đầu có thể mất vài phút." : " Máy chủ đang khởi động, lần đầu có thể mất khoảng 90 giây.") : "";
        var what = o.parent ? "Đang làm lại bản cuối" : (j.status === "processing" ? "Đang tạo ảnh" : "Đang xếp hàng");
        say(what + "… " + (mins ? mins + " phút " : "") + (secs % 60) + " giây." + (set ? " Thường mất vài phút." : "") + cold, j.status === "processing" ? 60 : 20);
        setTimeout(tick, gap);
      }).catch(function () {
        if (token !== S.pollToken) return;
        fails++;
        if (fails >= (set ? 6 : 5)) { failed("Mất kết nối", "Ảnh có thể vẫn đang chạy. Tải lại trang để nối lại."); return; }
        setTimeout(tick, gap);
      });
    }
    tick();
  }
  S.track = track;

  /* ---------- history: the key's last jobs become the first turns; a running job resumes ---------- */
  function modeOfJob(job) { return { transparent: "cutout", edit: "edit", compose: "compose", production: "prod", rerender: "prod" }[job.mode] || "text"; }
  function loadHistory() {
    if (S.historyLoaded) return; S.historyLoaded = true;
    api("/v1/jobs?limit=12").then(function (r) {
      if (!S.signedIn) return;
      var done = r.ok && r.body.jobs ? r.body.jobs.filter(function (j) { return j.status === "completed" && j.result_url !== undefined && j.mode !== "rerender" && (j.result_url || (j.results && j.results.length)); }) : [];
      var older = done.slice().reverse().map(function (j) {
        var m = modeOfJob(j), d = S.modes[m];
        return { id: S.nextId++, mode: m, prompt: j.prompt || (d ? d.label : "Ảnh"), atts: [], req: null, status: "done", job: j, fromHistory: true };
      });
      S.turns = older.concat(S.turns);
      if (!S.sel && older.length) S.sel = { turn: older[older.length - 1].id, idx: 0 };
      renderAll(); resume();
    }).catch(function () { S.historyLoaded = false; });
  }
  function resume() {
    var id = load("cutin-last-job"); if (!id || !S.signedIn || S.busy) return;
    api("/v1/jobs/" + id).then(function (r) {
      if (!r.ok) { save("cutin-last-job", ""); return; }
      var j = r.body, m = modeOfJob(j); if (j.mode === "rerender") { save("cutin-last-job", ""); return; }
      var d = S.modes[m], turn = { id: S.nextId++, mode: m, prompt: j.prompt || (d ? d.label : "Ảnh"), atts: [], req: null, status: "busy", pct: 20, text: "Đang nối lại…", startedAt: Date.parse(j.created_at) || Date.now() };
      if (j.status === "completed") { save("cutin-last-job", ""); turn.status = "done"; turn.job = j; S.turns.push(turn); S.sel = { turn: turn.id, idx: 0 }; renderAll(); }
      else if (j.status === "queued" || j.status === "processing") { S.turns.push(turn); S.sel = { turn: turn.id, idx: 0 }; S.busy = true; renderAll(); track(turn, id, { startedAt: turn.startedAt }); }
      else save("cutin-last-job", "");
    }).catch(function () {});
  }

  /* ---------- chain: use a result in another mode ---------- */
  S.chain = function (target, turn, item) {
    if (!S.enabled(target)) { toast("Tính năng này sắp có."); return; }
    var body = item && item.case ? { job_id: turn.job.job_id, case: item.case } : { job_id: turn.job.job_id };
    postJson("/v1/uploads/from-job", body).then(function (r) {
      if (!r.ok) { toast(describeError(r)[0]); return; }
      var it = { imageId: r.body.image_id, src: item.url, name: "Ảnh từ kết quả" };
      var list = S.imgs[target], max = S.modes[target].images.max;
      if (max > 1) { if (list.length >= max) { toast("Đã đủ " + max + " ảnh."); return; } list.push(it); } else S.imgs[target] = [it];
      S.setMode(target); toast("Đã gắn ảnh vào ô soạn.");
    }).catch(function () { toast("Không kết nối được. Thử lại sau."); });
  };

  /* ---------- rendering: conversation ---------- */
  function renderTurns() {
    var box = $("#turns"), wrap = box.parentNode, atBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 80;
    box.innerHTML = "";
    if (!S.turns.length) { box.appendChild(el("p", "hint", S.signedIn ? "Hội thoại của phiên này sẽ hiện ở đây. Mô tả ảnh bạn muốn ở ô soạn bên dưới." : "Đăng nhập để tạo ảnh. Hội thoại và ảnh bạn tạo sẽ hiện ở đây.")); return; }
    S.turns.forEach(function (t) {
      var d = S.modes[t.mode], row = el("div", "turn");
      if (t.atts && t.atts.length) { var a = el("div", "atts"); t.atts.forEach(function (n) { var c = el("span", "cu-chip"); c.appendChild(el("span", "cu-chip-name", n)); a.appendChild(c); }); row.appendChild(a); }
      row.appendChild(el("div", "cu-bubble cu-bubble-user", t.prompt));
      var rep = el("div", "reply");
      if (t.status === "busy" || (t.status === "done" && t.text)) {
        rep.appendChild(el("span", "cu-thinking", t.text || "Đang tạo…"));
        var bar = el("div", "bar"), fill = el("i"); fill.style.width = (t.pct || 10) + "%"; bar.appendChild(fill); rep.appendChild(bar);
      } else if (t.status === "error") {
        var ban = el("div", "cu-banner cu-banner-danger"), tx = el("div", "cu-banner-text"); tx.appendChild(el("strong", "", t.err[0])); tx.appendChild(document.createTextNode(t.err[1])); ban.appendChild(tx); rep.appendChild(ban);
        var acts = el("div", "stage-acts");
        if (t.err[2]) { var si = el("button", "cu-btn cu-btn-primary", "Đăng nhập"); si.type = "button"; si.addEventListener("click", signIn); acts.appendChild(si); }
        if (t.err[3]) { var z = el("a", "cu-btn cu-btn-primary", "Nhắn Zalo để thêm lượt"); z.href = ZALO; z.target = "_blank"; z.rel = "noopener"; acts.appendChild(z); }
        if (t.req) { var re = el("button", "cu-btn cu-btn-ghost", "Thử lại"); re.type = "button"; re.addEventListener("click", function () { if (!S.busy) { S.turns = S.turns.filter(function (x) { return x !== t; }); run(t.req, false); } }); acts.appendChild(re); }
        rep.appendChild(acts);
      } else {
        var rs = resultsOf(t);
        rep.appendChild(el("div", "cu-bubble cu-bubble-assistant", rs.length > 1 ? "Đã tạo " + rs.length + " ảnh." : "Đã tạo ảnh."));
        var mini = el("div", "mini");
        rs.forEach(function (r, i) {
          var b = el("button", S.sel && S.sel.turn === t.id && S.sel.idx === i ? "on" : ""); b.type = "button"; b.setAttribute("aria-label", "Mở " + r.label);
          var im = el("img"); im.src = r.url; im.alt = ""; im.loading = "lazy"; b.appendChild(im);
          b.addEventListener("click", function () { S.sel = { turn: t.id, idx: i }; renderTurns(); renderCanvas(); }); mini.appendChild(b);
        });
        if (!(S.modes[t.mode].renderSet && rs.length > 1)) rep.appendChild(mini); /* a set shown as a grid on the canvas is picked there, not twice */
        var meta = t.job && t.job.width && t.job.height ? t.job.width + "×" + t.job.height : ""; if (meta) rep.appendChild(el("span", "note", meta));
      }
      row.appendChild(rep); box.appendChild(row);
    });
    if (atBottom) wrap.scrollTop = wrap.scrollHeight;
  }

  /* ---------- rendering: canvas ---------- */
  function emptyCanvas() {
    var d = cur(), on = S.enabled(d.id), box = el("div", "empty"), e = el("div", "cu-empty");
    e.appendChild(el("h2", "", on ? d.emptyTitle : "Sắp có"));
    e.appendChild(el("p", "", on ? d.emptyText : "Tính năng này sẽ dùng được khi máy chủ GPU được nâng cấp. Bạn chưa bị tính lượt nào."));
    if (on && d.samples) {
      var acts = el("div", "cu-empty-actions");
      d.samples.forEach(function (s) { var b = el("button", "cu-btn sample", s); b.type = "button"; b.addEventListener("click", function () { $("#prompt").value = s; autosize(); $("#prompt").focus(); }); acts.appendChild(b); });
      e.appendChild(acts);
    }
    if (on && d.kinds) { var ol = el("ol", "list8"); d.kinds.forEach(function (k) { ol.appendChild(el("li", "", k)); }); e.appendChild(ol); }
    if (on && d.images.max > 0) { var pk = el("button", "cu-btn cu-btn-ghost", "Chọn ảnh"); pk.type = "button"; pk.addEventListener("click", function () { $("#file").click(); }); var w = el("div", "cu-empty-actions"); w.appendChild(pk); e.appendChild(w); }
    box.appendChild(e); return box;
  }
  function renderCanvas() {
    var cv = $("#canvas"); cv.innerHTML = "";
    var s = selected(), vs = variants();
    if (!s) { cv.appendChild(emptyCanvas()); return; }
    var t = s.turn, d = S.modes[t.mode], busy = t.status === "busy", stage = el("div", "cu-stage is-fill");
    /* a finished set (Production) shows all its pictures as a grid with a checkbox per tile, instead of one big picture */
    if (d.renderSet && t.status === "done" && t.job && resultsOf(t).length > 1) { cv.appendChild(d.renderSet(S, t)); return; }
    var main = el("figure", "cu-stage-main stage-main" + (t.mode === "cutout" ? " is-checker" : ""));
    if (busy) {
      var b = el("div", "busy"); b.appendChild(el("span", "cu-thinking", t.text || "Đang tạo…")); var bar = el("div", "bar"), fill = el("i"); fill.style.width = (t.pct || 10) + "%"; bar.appendChild(fill); b.appendChild(bar); main.appendChild(b);
    } else if (s.item) {
      var im = el("img"); im.src = s.item.url; im.alt = t.mode === "cutout" ? "Ảnh đã xóa nền" : s.item.label; main.appendChild(im);
      if (s.item.final) main.appendChild(el("span", "tag fin", "Bản cuối"));
    } else {
      main.appendChild(el("span", "", t.status === "error" ? "Chưa có ảnh" : ""));
    }
    stage.appendChild(main);
    if (t.status === "done" && t.text) stage.appendChild(el("span", "meta", t.text));
    /* which quality made this picture, so Nhanh and FLUX (thử) can be compared by eye */
    var qUsed = d.quality && !busy && s.item ? ((t.job && t.job.quality) || (t.req && t.req.quality)) : "", qOpt = qUsed ? d.quality.options.filter(function (o) { return o[0] === qUsed; })[0] : null;
    if (qOpt) stage.appendChild(el("span", "meta", "Mức chất lượng: " + qOpt[1]));
    if (s.item && !busy) {
      var acts = el("div", "cu-stage-actions");
      function act(label, ic, fn, primary) { var a = el("button", "cu-btn " + (primary ? "cu-btn-primary" : "cu-btn-ghost")); a.type = "button"; a.innerHTML = icon(ic) + '<span class="lb"></span>'; $(".lb", a).textContent = label; a.setAttribute("aria-label", label); a.title = label; a.addEventListener("click", fn); acts.appendChild(a); return a; }
      var dl = el("a", "cu-btn cu-btn-ghost"); dl.innerHTML = icon("download") + '<span class="lb">Tải xuống</span>'; dl.setAttribute("aria-label", "Tải xuống"); dl.title = "Tải xuống"; dl.href = s.item.url; dl.download = "cutin-" + String((t.job && t.job.job_id) || "anh").slice(0, 8) + (s.item.case ? "-" + s.item.case : "") + ".png"; acts.appendChild(dl);
      if (S.enabled("edit")) act("Sửa ảnh này", "edit", function () { S.chain("edit", t, s.item); });
      if (S.enabled("cutout") && t.mode !== "cutout") act("Xóa nền", "cut", function () { S.chain("cutout", t, s.item); });
      if (S.enabled("compose")) act("Ghép ảnh", "merge", function () { S.chain("compose", t, s.item); });
      if (t.req && d.id !== "prod") act("Tạo lại", "refresh", function () { if (!S.busy) run(t.req, false); });
      if (d.actions) d.actions(S, t, s.item, act);
      stage.appendChild(acts);
    }
    if (vs.length) {
      var strip = el("div", "cu-stage-strip"); strip.setAttribute("role", "group"); strip.setAttribute("aria-label", "Ảnh trong phiên");
      vs.forEach(function (v) {
        var on = S.sel && S.sel.turn === v.turn.id && S.sel.idx === v.idx;
        var th = el("button", "cu-stage-thumb" + (on ? " is-active" : "") + (v.turn.picked && v.item.case && v.turn.picked[v.item.case] ? " picked" : "")); th.type = "button"; th.setAttribute("aria-pressed", on ? "true" : "false"); th.setAttribute("aria-label", v.item.label);
        var i2 = el("img"); i2.src = v.item.url; i2.alt = ""; i2.loading = "lazy"; th.appendChild(i2);
        th.addEventListener("click", function () { S.sel = { turn: v.turn.id, idx: v.idx }; renderTurns(); renderCanvas(); });
        strip.appendChild(th);
      });
      stage.appendChild(strip);
    }
    cv.appendChild(stage);
  }
  S.renderCanvas = renderCanvas; S.renderTurns = renderTurns;
  (function drop() {
    var cv = $("#canvas");
    cv.addEventListener("dragover", function (e) { e.preventDefault(); cv.classList.add("drag"); });
    cv.addEventListener("dragleave", function () { cv.classList.remove("drag"); });
    cv.addEventListener("drop", function (e) { e.preventDefault(); cv.classList.remove("drag"); if (e.dataTransfer && e.dataTransfer.files.length) S.addFiles(e.dataTransfer.files); });
  })();

  /* ---------- account ---------- */
  function renderAccount() {
    var b = $("#acct"); b.classList.toggle("out", !S.signedIn);
    $("#acct-txt").textContent = S.signedIn ? S.user : "Đăng nhập";
    $("#acct-av").textContent = S.signedIn ? S.user.slice(0, 1).toUpperCase() : "";
  }
  $("#acct").addEventListener("click", function (e) {
    e.stopPropagation();
    if (!S.signedIn) { signIn(); return; }
    if (pop.name === "acct") { closePops(true); return; }
    closePops(false); pop.name = "acct"; $("#acct-menu").hidden = false; this.setAttribute("aria-expanded", "true");
  });
  /* "Phiên mới": clears this page's conversation only. Server history stays (a reload brings the last 12 jobs back); the typed prompt and the mode stay.
     While a job is running the click only explains itself with a toast, so the polling turn is never orphaned. */
  $("#newsession-ic").innerHTML = icon("plus", 18);
  $("#newsession").addEventListener("click", function () {
    if (S.busy) { toast("Đang tạo ảnh, đợi xong nhé."); return; }
    closePops(false);
    S.turns = []; S.sel = null;
    Object.keys(S.imgs).forEach(function (k) { S.imgs[k] = []; });
    renderAll();
  });
  $("#signout").addEventListener("click", function () { closePops(false); unlockQuota(); if (window.Clerk) window.Clerk.signOut().then(function () { toast("Đã đăng xuất."); }); });

  function renderAll() { renderChips(); renderAttached(); renderComposer(); renderTurns(); renderCanvas(); }
  S.renderAll = renderAll;
  S.boot = function () {
    syncTheme(); renderAccount();
    var h = (location.hash || "").slice(1); if (S.modes[h]) S.mode = h;
    scheduleUnlock(); renderAll(); loadCaps(); initClerk();
    window.addEventListener("resize", function () { autosize(); if (!$("#opts").hidden) placePanel(); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(autosize);
  };
  Promise.resolve().then(function () { S.boot(); });
})();
