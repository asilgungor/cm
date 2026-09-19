// OFM canli mac 2D sahasi (Faz 14T) -- st.components.v2 bileseni, harici kutuphane / ag istegi YOK.
//
// Python (match_anim.frame_script) her gosterilen kare icin kucuk bir BETIK yollar: oyuncular, baslangic / bitis
// konumlari, hareket anahtar kareleri, top ucuslari ve efektler (ms). Bu dosya betigi tarayicida
// requestAnimationFrame ile ~60 fps oynatir; Streamlit yeniden calismalari kareleri SURMEZ, yalnizca yeni betik getirir.
// Bilesen yeniden calismalar arasinda ayni kok ogede yasar (durum root.__ofmPitch): yeni betik o anki pozdan devam eder.
//
// Top ucus profili: 0 yay (4h·u·(1-u)), 1 yukselen (h·u: ustten giden sut), 2 yeniden yerlestirme (oyun disi top
// solup yeni yerinde belirir; kale vurusu, korner / frikik / penalti noktasi, santra).
// Kipler (data.m): "play" surekli oynat, "pause" dondur (yeni karede son poz), "hold" (otomatik duraklama) yeni kareyi
// oynatip son pozda donar, "jump" son poza atla (Anında / geri sarma).
// data.k hiz carpani (Yavaş 1,4 / Normal 1 / Hızlı 0,5): betik zamani = gecen sure / k.
// Metinler yalnizca textContent / setAttribute ile yazilir: innerHTML, eval ya da veriden kurulan kod yoktur.
// prefers-reduced-motion: her kare dogrudan son pozunda cizilir.

const NS = "http://www.w3.org/2000/svg";
const L = 105, W = 68, CX = 52.5, CY = 34;
const POST_LO = 30.34, POST_HI = 37.66, NET = 2.0;
const ENERGY = ["#9e9e9e", "#43a047", "#fdd835", "#e53935"];
const ENERGY_TEXT = ["", "kondisyon iyi", "kondisyon orta", "kondisyon düşük"];
const IDLE_AMP = 0.38;        // olaylar arasi "nefes" hareketi (m)
const FADE_MS = 320;

function el(tag, attrs, parent) {
  const node = document.createElementNS(NS, tag);
  if (attrs) for (const k in attrs) node.setAttribute(k, String(attrs[k]));
  if (parent) parent.appendChild(node);
  return node;
}

function hel(tag, cls, parent, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (parent) parent.appendChild(node);
  return node;
}

function ease(u) { return u * u * (3 - 2 * u); }
function f1(v) { return Math.round(v * 100) / 100; }

// ------------------------------------------------------------------ saha iskeleti

function markings(g) {
  el("rect", { x: -4, y: -4, width: L + 8, height: W + 8, fill: "#1f5f24" }, g);
  el("rect", { x: 0, y: 0, width: L, height: W, fill: "#2e7d32" }, g);
  for (let i = 0; i < 10; i += 2) el("rect", { x: i * 10.5, y: 0, width: 10.5, height: W, fill: "#338a38" }, g);
  const lines = el("g", { fill: "none", stroke: "#ffffff", "stroke-opacity": 0.85, "stroke-width": 0.3 }, g);
  const pa = (W - 40.32) / 2, ga = (W - 18.32) / 2, arc = Math.sqrt(9.15 * 9.15 - 5.5 * 5.5);
  el("rect", { x: 0, y: 0, width: L, height: W }, lines);
  el("line", { x1: CX, y1: 0, x2: CX, y2: W }, lines);
  el("circle", { cx: CX, cy: CY, r: 9.15 }, lines);
  el("rect", { x: 0, y: pa, width: 16.5, height: 40.32 }, lines);
  el("rect", { x: L - 16.5, y: pa, width: 16.5, height: 40.32 }, lines);
  el("rect", { x: 0, y: ga, width: 5.5, height: 18.32 }, lines);
  el("rect", { x: L - 5.5, y: ga, width: 5.5, height: 18.32 }, lines);
  el("path", { d: `M16.5,${CY - arc}A9.15,9.15 0 0 1 16.5,${CY + arc}` }, lines);
  el("path", { d: `M${L - 16.5},${CY - arc}A9.15,9.15 0 0 0 ${L - 16.5},${CY + arc}` }, lines);
  el("path", { d: `M0,1A1,1 0 0 0 1,0M${L - 1},0A1,1 0 0 0 ${L},1M${L},${W - 1}A1,1 0 0 0 ${L - 1},${W}M1,${W}A1,1 0 0 0 0,${W - 1}` }, lines);
  const spots = el("g", { fill: "#ffffff", "fill-opacity": 0.9 }, g);
  el("circle", { cx: CX, cy: CY, r: 0.45 }, spots);
  el("circle", { cx: 11, cy: CY, r: 0.35 }, spots);
  el("circle", { cx: L - 11, cy: CY, r: 0.35 }, spots);
  // kaleler: ag deseni (gol dalgasi bu gruba uygulanir)
  const goals = [];
  for (const x of [-NET, L]) {
    const goal = el("g", { class: "mp-goal" }, g);
    el("rect", { x, y: POST_LO, width: NET, height: POST_HI - POST_LO, fill: "#ffffff", "fill-opacity": 0.16,
      stroke: "#ffffff", "stroke-width": 0.3 }, goal);
    const net = el("g", { class: "mp-net", stroke: "#ffffff", "stroke-opacity": 0.45, "stroke-width": 0.12 }, goal);
    for (let y = POST_LO + 0.9; y < POST_HI; y += 0.9) el("line", { x1: x, y1: y, x2: x + NET, y2: y }, net);
    el("line", { x1: x + NET / 2, y1: POST_LO, x2: x + NET / 2, y2: POST_HI }, net);
    goals.push(goal);
  }
  return goals;
}

function build(root) {
  root.textContent = "";
  const head = hel("div", "mp-head", root);
  const home = hel("div", "mp-team home", head);
  const hChip = hel("span", "mp-chip", home);
  const hName = hel("span", "mp-name", home);
  const hDir = hel("span", "mp-dir", home);
  const mid = hel("div", "mp-mid", head);
  const score = hel("span", "mp-score", mid);
  const clock = hel("span", "mp-clock", mid);
  const away = hel("div", "mp-team away", head);
  const aDir = hel("span", "mp-dir", away);
  const aName = hel("span", "mp-name", away);
  const aChip = hel("span", "mp-chip", away);
  const box = hel("div", "mp-box", root);
  const svg = el("svg", { class: "mp-svg", viewBox: "-3.5 -3.5 112 76", role: "img", "aria-label": "2D saha" }, box);
  const goals = markings(el("g", { class: "mp-pitch" }, svg));
  const under = el("g", { class: "mp-under" }, svg);
  const shadow = el("ellipse", { class: "mp-shadow", rx: 0.75, ry: 0.45, fill: "#000000", "fill-opacity": 0.35 }, under);
  const players = el("g", { class: "mp-players" }, svg);
  const ballG = el("g", { class: "mp-ball" }, svg);
  const ball = el("circle", { r: 0.8, fill: "#ffffff", stroke: "#111111", "stroke-width": 0.2 }, ballG);
  const over = el("g", { class: "mp-over" }, svg);
  const cap = hel("div", "mp-cap", root);
  const capText = hel("span", "mp-cap-t", cap);
  hel("span", "mp-note", cap, "Konumlar temsilî · her hareket motorun gerçek olayına ve oyuncusuna bağlı");
  return { head, hChip, hName, hDir, aChip, aName, aDir, score, clock, svg, goals, under, shadow, players,
    ballG, ball, over, capText, box };
}

// ------------------------------------------------------------------ oyuncu ogeleri

function makePlayer(ui, row, colors) {
  const [pid, side, name, gk] = row;
  const g = el("g", { class: "mp-p" + (gk ? " gk" : ""), "data-pid": pid, "data-side": side ? "away" : "home" },
    ui.players);
  const hl = el("circle", { class: "mp-hl", r: 3.2, fill: "none", stroke: "#ffd60a", "stroke-width": 0.55 }, g);
  const ring = el("circle", { class: "mp-ring", r: 2.35, fill: "none", "stroke-width": 0.45 }, g);
  const body = el("g", { class: "mp-body" }, g);
  const dot = el("circle", { r: 1.6, "stroke-width": gk ? 0.5 : 0.35 }, body);
  const label = el("text", { class: "mp-lbl", y: 4.3, "text-anchor": "middle" }, g);
  label.textContent = name;
  const badges = el("g", { class: "mp-badges" }, g);
  const title = el("title", null, g);
  const p = { pid, g, hl, ring, body, dot, label, badges, title, side, gk: !!gk, x: CX, y: CY, bx: CX, by: CY,
    phase: (pid * 0.6180339) % 1, alive: true, fade: 1, op: 1, leaving: false, pose: "", lblSlot: 0, lblHidden: false,
    on: false };
  paint(p, row, colors);
  return p;
}

function paint(p, row, colors) {
  const [, side, name, gk, energy, yellow, status] = row;
  const kit = colors[side] || ["#e53935", "#ffffff", "#8e1c19"];
  p.dot.setAttribute("fill", gk ? kit[2] : kit[0]);
  p.dot.setAttribute("stroke", gk ? "#ffffff" : kit[1]);
  p.ring.setAttribute("stroke", ENERGY[energy] || ENERGY[0]);
  p.ring.setAttribute("stroke-opacity", energy ? 0.95 : 0.35);
  if (p.label.textContent !== name) p.label.textContent = name;
  p.title.textContent = name + (ENERGY_TEXT[energy] ? " · " + ENERGY_TEXT[energy] : "");
  p.badges.textContent = "";
  if (yellow > 0) {
    el("rect", { x: 1.5, y: -3.5, width: 1.1, height: 1.5, rx: 0.15, fill: yellow >= 2 ? "#e53935" : "#fdd835",
      stroke: "#1a1a1a", "stroke-width": 0.12 }, p.badges);
  }
  p.leaving = status === 2;
  p.alive = true;
}

// ------------------------------------------------------------------ betik hazirligi

function prepare(st, s, continuous) {
  const ui = st.ui;
  const n = s.pl.length;
  const seen = new Set();
  const actors = [];
  for (let i = 0; i < n; i++) {
    const row = s.pl[i];
    const pid = row[0];
    seen.add(pid);
    let p = st.players.get(pid);
    const fresh = !p;
    if (fresh) {
      p = makePlayer(ui, row, st.colors);
      st.players.set(pid, p);
    } else {
      paint(p, row, st.colors);
    }
    let x0 = s.p0[2 * i], y0 = s.p0[2 * i + 1];
    if (continuous && !fresh && p.fade > 0.5) { x0 = p.bx; y0 = p.by; }
    if (fresh) p.fade = 0;
    p.bx = x0; p.by = y0;
    const segs = [];
    actors.push({ p, x0, y0, segs, row });
  }
  for (const m of s.mv) {
    const a = actors[m[0]];
    if (!a) continue;
    const prev = a.segs.length ? a.segs[a.segs.length - 1] : null;
    a.segs.push({ t0: m[1], t1: m[2], x0: prev ? prev.x1 : a.x0, y0: prev ? prev.y1 : a.y0, x1: m[3], y1: m[4],
      cx: m.length > 5 ? m[5] : null, cy: m.length > 6 ? m[6] : null });
  }
  // sahnede olup yeni betikte olmayanlar: solarak cikar
  for (const [pid, p] of st.players) {
    if (!seen.has(pid)) p.alive = false;
  }
  st.actors = actors;
  // vurgular
  const hl = new Set(s.hl || []);
  actors.forEach((a, i) => {
    a.p.on = hl.has(i);
    a.p.g.classList.toggle("on", a.p.on);
    a.p.label.classList.toggle("on", a.p.on);
  });
  // top: surekli oynatmada o anki yerinden; sahibi Python'un varsaydigindan farkliysa kisa bir kayma
  let bx = s.b0[0], by = s.b0[1], holder = s.b0[2];
  const flights = [];
  if (continuous && st.ballReady) {
    bx = st.ball.x; by = st.ball.y;
    const curIdx = st.ball.holderPid !== null ? actors.findIndex((a) => a.p.pid === st.ball.holderPid) : -1;
    if (holder >= 0 && curIdx !== holder) {
      flights.push({ t0: 0, t1: 120, x: null, y: null, h: 0.2, holder, prof: 0 });
      holder = curIdx;
    } else if (holder < 0) {
      holder = -1;
    }
  }
  for (const b of s.bl) flights.push({ t0: b[0], t1: b[1], x: b[2], y: b[3], h: b[4], holder: b[5], prof: b[6] });
  st.ball0 = { x: bx, y: by, holder };
  st.flights = flights;
  // ucus baslangiclarini onceden hesapla (sahibin o anki konumu ya da bir onceki ucusun sonu)
  let cur = { x: bx, y: by, holder };
  for (const f of flights) {
    const from = cur.holder >= 0 ? posAt(actors[cur.holder], f.t0) : { x: cur.x, y: cur.y };
    f.sx = from.x; f.sy = from.y;
    if (f.x === null) { const to = posAt(actors[f.holder], f.t1); f.x = to.x; f.y = to.y; }
    cur = { x: f.x, y: f.y, holder: f.holder };
  }
  st.fx = (s.fx || []).map((f) => ({ t0: f[0], dur: f[1], code: f[2], i: f[3], x: f[4], y: f[5], node: null }));
  clearFx(st);
  st.T = Math.max(1, s.T || 1);
  st.dir = s.dir;
  // basliklar
  ui.score.textContent = `${s.sc[0]} - ${s.sc[1]}` + (s.pen ? ` (pen. ${s.pen[0]}-${s.pen[1]})` : "");
  ui.clock.textContent = `${s.min} · ${s.ph}`;
  ui.hDir.textContent = s.dir ? "→" : "←";
  ui.aDir.textContent = s.dir ? "←" : "→";
  ui.capText.textContent = s.cap || "";
  ui.svg.setAttribute("aria-label",
    `${st.names[0]} ${s.sc[0]}-${s.sc[1]} ${st.names[1]}, ${s.min}: ${s.cap || ""}`);
}

function posAt(a, t) {
  if (!a) return { x: CX, y: CY };
  let x = a.x0, y = a.y0;
  for (const s of a.segs) {
    if (t <= s.t0) return { x, y };
    if (t >= s.t1) { x = s.x1; y = s.y1; continue; }
    const u = ease((t - s.t0) / (s.t1 - s.t0));
    if (s.cx === null) return { x: s.x0 + (s.x1 - s.x0) * u, y: s.y0 + (s.y1 - s.y0) * u };
    const v = 1 - u;
    return { x: v * v * s.x0 + 2 * v * u * s.cx + u * u * s.x1, y: v * v * s.y0 + 2 * v * u * s.cy + u * u * s.y1 };
  }
  return { x, y };
}

function ballAt(st, t) {
  let holder = st.ball0.holder, x = st.ball0.x, y = st.ball0.y;
  for (const f of st.flights) {
    if (t < f.t0) break;
    if (t >= f.t1) { holder = f.holder; x = f.x; y = f.y; continue; }
    const u = (t - f.t0) / (f.t1 - f.t0);
    if (f.prof === 2) {        // oyun disi: top toplanip yeni yerine konur (ucmaz; solup yerinde belirir)
      return u < 0.5 ? { x: f.sx, y: f.sy, h: 0, holder: -1, op: 1 - 2 * u } : { x: f.x, y: f.y, h: 0, holder: -1, op: 2 * u - 1 };
    }
    const h = f.prof === 1 ? f.h * u : 4 * f.h * u * (1 - u);
    return { x: f.sx + (f.x - f.sx) * u, y: f.sy + (f.y - f.sy) * u, h, holder: -1 };
  }
  if (holder >= 0 && st.actors[holder]) {
    const q = posAt(st.actors[holder], t);
    const right = (st.actors[holder].p.side === 0) === (st.dir === 1);
    return { x: q.x + (right ? 0.95 : -0.95), y: q.y + 0.35, h: 0, holder };
  }
  return { x, y, h: 0, holder: -1 };
}

// ------------------------------------------------------------------ efektler

function clearFx(st) {
  st.ui.over.textContent = "";
  for (const g of st.ui.goals) g.classList.remove("rip");
  for (const [, p] of st.players) { p.pose = ""; p.body.removeAttribute("transform"); }
}

function fxNode(st, f) {
  const g = el("g", { class: "mp-fx fx-" + f.code }, st.ui.over);
  switch (f.code) {
    case "wh": {
      el("circle", { r: 1.9, fill: "#111111", "fill-opacity": 0.75, stroke: "#ffffff", "stroke-width": 0.2 }, g);
      el("rect", { x: -1.05, y: -0.45, width: 1.5, height: 0.9, rx: 0.35, fill: "#e0e0e0" }, g);
      el("circle", { cx: 0.6, cy: 0.05, r: 0.55, fill: "#e0e0e0" }, g);
      el("circle", { class: "mp-wave", r: 2.4, fill: "none", stroke: "#ffffff", "stroke-width": 0.2 }, g);
      break;
    }
    case "yc": case "rc":
      el("rect", { x: 1.2, y: -5.4, width: 1.8, height: 2.5, rx: 0.2, fill: f.code === "yc" ? "#fdd835" : "#e53935",
        stroke: "#1a1a1a", "stroke-width": 0.15, transform: "rotate(10 2.1 -4.1)" }, g);
      break;
    case "inj":
      el("circle", { cx: 2.2, cy: -3.4, r: 1.35, fill: "#ffffff", stroke: "#b71c1c", "stroke-width": 0.15 }, g);
      el("path", { d: "M1.4,-3.4H3M2.2,-4.2V-2.6", stroke: "#e53935", "stroke-width": 0.5 }, g);
      break;
    case "fl":
      el("line", { x1: 0, y1: 0, x2: 0, y2: -3.2, stroke: "#dddddd", "stroke-width": 0.25 }, g);
      el("path", { d: "M0,-3.2L2.4,-2.5L0,-1.8Z", fill: "#fdd835", stroke: "#e53935", "stroke-width": 0.12 }, g);
      break;
    case "sub_in": case "sub_out": {
      const up = f.code === "sub_in";
      el("circle", { cx: -2.2, cy: -3.3, r: 1.2, fill: up ? "#43a047" : "#e53935", stroke: "#ffffff",
        "stroke-width": 0.15 }, g);
      el("path", { d: up ? "M-2.2,-2.6V-4M-2.75,-3.45L-2.2,-4.05L-1.65,-3.45" : "M-2.2,-4V-2.6M-2.75,-3.15L-2.2,-2.55L-1.65,-3.15",
        fill: "none", stroke: "#ffffff", "stroke-width": 0.3 }, g);
      break;
    }
    case "net": break;
    case "cel":
      el("circle", { class: "mp-burst", r: 3.2, fill: "none", stroke: "#ffd60a", "stroke-width": 0.5 }, g);
      el("circle", { class: "mp-burst b2", r: 3.2, fill: "none", stroke: "#ffffff", "stroke-width": 0.35 }, g);
      break;
    case "duel":
      el("path", { d: "M-1.3,0L1.3,0M0,-1.3L0,1.3M-0.9,-0.9L0.9,0.9M-0.9,0.9L0.9,-0.9", stroke: "#ffffff",
        "stroke-width": 0.25, "stroke-opacity": 0.85 }, g);
      break;
    case "post": case "wall": case "save":
      el("circle", { class: "mp-burst", r: 1.8, fill: "none", stroke: f.code === "save" ? "#90caf9" : "#ffffff",
        "stroke-width": 0.45 }, g);
      break;
    case "hdr":
      el("circle", { class: "mp-burst", r: 2.6, fill: "none", stroke: "#ffffff", "stroke-width": 0.3 }, g);
      break;
    case "tac":
      el("rect", { x: -1.3, y: -1.7, width: 2.6, height: 3.2, rx: 0.3, fill: "#f5f5f5", stroke: "#37474f",
        "stroke-width": 0.2 }, g);
      el("path", { d: "M-0.7,-0.6H0.7M-0.7,0.2H0.7M-0.7,1H0.3", stroke: "#37474f", "stroke-width": 0.22 }, g);
      break;
    default: break;
  }
  return g;
}

function applyFx(st, t, now) {
  for (const f of st.fx) {
    const active = t >= f.t0 && t < f.t0 + f.dur;
    const persist = (f.code === "yc" || f.code === "rc" || f.code === "inj") && t >= f.t0;
    const show = active || persist;
    const poseOnly = f.code === "dive" || f.code === "fall" || f.code === "net";
    if (show && !f.node && !poseOnly) f.node = fxNode(st, f);
    if (!show && f.node) { f.node.remove(); f.node = null; }
    if (f.code === "net") {
      const goal = st.ui.goals[f.x < CX ? 0 : 1];
      goal.classList.toggle("rip", active);
      continue;
    }
    const a = f.i >= 0 ? st.actors[f.i] : null;
    if (a && (f.code === "dive" || f.code === "fall")) {
      const u = active ? (t - f.t0) / f.dur : 1;
      if (active || (f.code === "fall" && f.dur >= 2000 && t > f.t0)) {
        const k = f.code === "dive" ? Math.sin(Math.min(1, u * 1.6) * Math.PI / 2) : 1;
        const pose = f.code === "dive" ? `scale(${f1(1 + 0.8 * k)} ${f1(1 - 0.35 * k)})`
          : `rotate(70) scale(1.35 0.65)`;
        if (a.p.pose !== pose) { a.p.body.setAttribute("transform", pose); a.p.pose = pose; }
      } else if (a.p.pose) {
        a.p.body.removeAttribute("transform"); a.p.pose = "";
      }
      continue;
    }
    if (!f.node) continue;
    let x = f.x, y = f.y;
    if (a) { x = a.p.x; y = a.p.y; }
    f.node.setAttribute("transform", `translate(${f1(x)} ${f1(y)})`);
    if (active && f.dur > 0) {
      const u = (t - f.t0) / f.dur;
      f.node.style.opacity = String(u > 0.8 ? Math.max(0, (1 - u) * 5) : Math.min(1, u * 6 + 0.2));
    } else if (persist) {
      f.node.style.opacity = "1";
    }
  }
  void now;
}

// ------------------------------------------------------------------ ad etiketleri (kalabalikta cakisma)

// Kalabalik anlarda (korner, frikik, gol sevinci) adlar ust uste binmesin: once olaydaki oyuncularin adi yerlesir,
// digerleri dairenin altina, sigmazsa ustune; o da doluysa gizlenir (nokta yerinde kalir, yalnizca etiket degisir).
const LBL_BELOW = 4.3, LBL_ABOVE = -2.9, LBL_H = 2.3;

function labelAnchor(p) { return p.x < 7 ? "start" : p.x > L - 7 ? "end" : "middle"; }

function labelBox(p, slot) {
  const w = p.label.textContent.length * (p.on ? 1.42 : 1.2) + 0.4;
  const base = p.y + (slot === 0 ? LBL_BELOW : LBL_ABOVE);
  const a = labelAnchor(p);
  const x0 = a === "start" ? p.x - 1.6 : a === "end" ? p.x + 1.6 - w : p.x - w / 2;
  return { x0, x1: x0 + w, y0: base - LBL_H, y1: base + 0.35 };
}

function overlaps(a, b) { return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1; }

function layoutLabels(st) {
  const placed = [];
  const order = st.actors.map((a) => a.p).filter((p) => p.alive && p.fade > 0.2);
  order.sort((a, b) => (b.on - a.on) || (a.pid - b.pid));
  const narrow = st.root.classList.contains("mp-narrow");
  for (const p of order) {
    let slot = -1;
    if (!(narrow && !p.on)) {
      for (const cand of [p.lblSlot, 1 - p.lblSlot]) {
        const box = labelBox(p, cand);
        if (!placed.some((q) => overlaps(box, q))) { slot = cand; placed.push(box); break; }
      }
      if (slot < 0 && p.on) { slot = p.lblSlot; placed.push(labelBox(p, slot)); }   // olaydaki oyuncu her zaman
    }
    const hide = slot < 0;
    if (hide !== p.lblHidden) { p.label.classList.toggle("hid", hide); p.lblHidden = hide; }
    const anchor = labelAnchor(p);
    if (anchor !== p.lblAnchor) {
      p.label.setAttribute("text-anchor", anchor);
      p.label.setAttribute("x", anchor === "start" ? -1.6 : anchor === "end" ? 1.6 : 0);
      p.lblAnchor = anchor;
    }
    if (!hide && slot !== p.lblSlot) { p.label.setAttribute("y", slot === 0 ? LBL_BELOW : LBL_ABOVE); p.lblSlot = slot; }
  }
}

// ------------------------------------------------------------------ cizim dongusu

function scriptTime(st, now) {
  if (st.frozen) return st.frozenT;
  return (now - st.start) / st.k;
}

function draw(st, now) {
  const t = Math.min(scriptTime(st, now), st.T);
  const idle = st.frozen ? 0 : 1;
  const wall = now / 1000;
  for (const a of st.actors) {
    const q = posAt(a, t);
    const p = a.p;
    p.bx = q.x; p.by = q.y;
    const w = 2 * Math.PI * (wall / (2.6 + p.phase * 1.4) + p.phase);
    p.x = q.x + idle * IDLE_AMP * Math.sin(w);
    p.y = q.y + idle * IDLE_AMP * 0.8 * Math.cos(w * 0.83 + p.phase * 3);
    p.g.setAttribute("transform", `translate(${f1(p.x)} ${f1(p.y)})`);
  }
  // cikan / sahneden ayrilan oyuncular
  const dt = st.lastNow ? Math.max(0, now - st.lastNow) : 16;
  st.lastNow = now;
  for (const [pid, p] of st.players) {
    const target = !p.alive || (p.leaving && t >= st.T - 1) ? 0 : 1;
    if (p.fade !== target) {
      const step = st.frozen ? 1 : dt / FADE_MS;
      p.fade = target > p.fade ? Math.min(target, p.fade + step) : Math.max(target, p.fade - step);
    }
    const op = f1(p.fade);
    if (p.op !== op) { p.g.style.opacity = String(op); p.op = op; }
    if (!p.alive && p.fade <= 0) { p.g.remove(); st.players.delete(pid); }
  }
  // top
  const b = ballAt(st, t);
  let bx = b.x, by = b.y;
  if (b.holder >= 0) {
    const p = st.actors[b.holder].p;
    bx += p.x - p.bx; by += p.y - p.by;
  }
  st.ball.x = b.x; st.ball.y = b.y;
  st.ball.holderPid = b.holder >= 0 ? st.actors[b.holder].p.pid : null;
  st.ballReady = true;
  const h = Math.max(0, b.h);
  st.ui.shadow.setAttribute("cx", f1(bx)); st.ui.shadow.setAttribute("cy", f1(by + 0.25));
  st.ui.ballG.setAttribute("transform", `translate(${f1(bx + h * 0.12)} ${f1(by - h * 0.45)}) scale(${f1(1 + h * 0.07)})`);
  const op = b.op === undefined ? 1 : Math.max(0, Math.min(1, b.op));
  if (st.ballOp !== op) { st.ui.ballG.style.opacity = String(f1(op)); st.ui.shadow.style.opacity = String(f1(op)); st.ballOp = op; }
  applyFx(st, t, now);
  st.frameNo = (st.frameNo || 0) + 1;
  if (st.frozen || st.frameNo % 3 === 0) layoutLabels(st);
}

function loop(st) {
  const tick = (now) => {
    st.raf = null;
    if (!st.root.isConnected) { st.stopped = true; return; }
    if (st.hold && !st.frozen && scriptTime(st, now) >= st.T) { st.frozen = true; st.frozenT = st.T; }
    // en fazla ~60 fps (120/144 Hz ekranda da); betik bitince (olaylar arasi "nefes") ~30 fps yeter
    const idle = !st.frozen && scriptTime(st, now) > st.T + FADE_MS;
    const gap = now - (st.lastDraw || 0);
    if (st.frozen || gap >= (idle ? 31 : 15.5)) { st.lastDraw = now; draw(st, now); }
    st.root.classList.toggle("mp-frozen", st.frozen);
    if (!st.frozen) st.raf = requestAnimationFrame(tick);
  };
  if (st.raf === null) st.raf = requestAnimationFrame(tick);
}

function reducedMotion() {
  try { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  catch (e) { return false; }
}

// ------------------------------------------------------------------ giris noktasi

export default function (component) {
  const { data, parentElement } = component;
  const root = parentElement.querySelector(".mp-root");
  if (!root) return undefined;
  let st = root.__ofmPitch;
  if (!st) {
    st = { root, ui: build(root), players: new Map(), actors: [], flights: [], fx: [], ball0: { x: CX, y: CY, holder: -1 },
      ball: { x: CX, y: CY, holderPid: null }, ballReady: false, tok: null, lastF: -2, T: 1, k: 1, start: 0,
      frozen: false, frozenT: 0, raf: null, colors: [], names: ["", ""], dir: 1, lastNow: 0 };
    root.__ofmPitch = st;
    // tanilama / kanit kaydi icin: betigin t (ms) anini dondurulmus cizer (oyunda kullanilmaz)
    st.debugSeek = (t) => { st.frozen = true; st.frozenT = Math.max(0, Math.min(Number(t) || 0, st.T)); draw(st, performance.now());
      st.root.classList.add("mp-frozen"); };
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver((entries) => {
        for (const e of entries) root.classList.toggle("mp-narrow", e.contentRect.width < 560);
      });
      ro.observe(root);
    }
  }
  const d = data || {};
  const s = d.s;
  if (Array.isArray(d.c)) st.colors = d.c;
  if (Array.isArray(d.tn)) st.names = d.tn;
  const ui = st.ui;
  ui.hName.textContent = st.names[0] || "";
  ui.aName.textContent = st.names[1] || "";
  if (st.colors[0]) { ui.hChip.style.background = st.colors[0][0]; ui.hChip.style.borderColor = st.colors[0][1]; }
  if (st.colors[1]) { ui.aChip.style.background = st.colors[1][0]; ui.aChip.style.borderColor = st.colors[1][1]; }
  if (!s || !Array.isArray(s.pl)) return undefined;
  const mode = d.m === "pause" || d.m === "jump" || d.m === "hold" ? d.m : "play";
  st.hold = mode === "hold";
  const k = typeof d.k === "number" && d.k > 0 ? d.k : 1;
  const now = performance.now();
  const tok = String(d.tok || `${s.f}|${s.pf}`);
  if (tok !== st.tok) {
    const continuous = st.tok !== null && st.lastF === s.pf && !s.snap && (mode === "play" || mode === "hold");
    if (continuous && !st.frozen) draw(st, now);           // o anki konumlari yakala
    prepare(st, s, continuous);
    st.tok = tok;
    st.lastF = s.f;
    st.k = k;
    if ((mode === "play" || mode === "hold") && !reducedMotion()) {
      st.start = now; st.frozen = false;
    } else {
      st.start = now - st.T * k - 1;
      st.frozen = mode === "pause";
      st.frozenT = st.T;
      for (const a of st.actors) a.p.fade = 1;
    }
  } else {
    // ayni kare: yalnizca kip / hiz degisti
    if (mode === "pause" && !st.frozen) { st.frozenT = Math.min(scriptTime(st, now), st.T); st.frozen = true; }
    else if (mode !== "pause" && mode !== "hold" && st.frozen) { st.start = now - st.frozenT * k; st.frozen = false; }
    else if (!st.frozen && k !== st.k) { const t = scriptTime(st, now); st.k = k; st.start = now - t * k; }
    if (mode === "jump" && !st.frozen) st.start = now - st.T * k - 1;
    st.k = k;
  }
  draw(st, now);
  st.root.classList.toggle("mp-frozen", st.frozen);
  if (!st.frozen) loop(st);
  return undefined;
}
