// OFM taktik tahtasi (Faz 13G) -- st.components.v2 bileseni, harici kutuphane / ag istegi YOK.
//
// Bilesen YALNIZCA NIYET yollar: setTriggerValue("intent", {n, rev, action, ...}). Kadroyu Python dogrular ve
// uygular (tactics_board.apply_intent + CareerManager.set_lineup); tahta her zaman sunucudan gelen veriyle yeniden
// cizilir (data.rev her niyetten sonra artar). Metinler yalnizca textContent / setAttribute ile yazilir: innerHTML,
// eval ya da veriden kurulan olay dizesi yoktur.
//
// Etkilesim:
//   fare      : surukle-birak (6 px esik), cift tik -> profil, sag tik -> menu
//   dokunmatik: dokun-sec + hedefe dokun, basili tut (~0,2 sn) + surukle, uzun bas + birak -> menu, cift dokun -> profil
//   klavye    : Enter/Bosluk sec, oklar hedef, Enter birak, Esc iptal, Shift+F10 / Menu tusu -> menu

const DRAG_PX = 6;
const EDGE_PX = 70;           // alt kenar: otomatik kaydirma bolgesi
const EDGE_TOP_PX = 120;      // ust kenar (Streamlit ust cubugu ~60 px ortuyor)
const TOUCH_SCROLL_PX = 10;
const LIFT_MS = 220;
const MENU_MS = 450;
const DOUBLE_MS = 350;
const BUSY_MS = 9000;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function label(s, key) {
  const labels = (s.data && s.data.labels) || {};
  return labels[key] || key;
}

function fmt(template, name) {
  return String(template).split("{name}").join(name);
}

function allPlayers(s) {
  const d = s.data || {};
  const out = [];
  (d.slots || []).forEach((slot) => { if (slot.player) out.push({ p: slot.player, where: "slot", slot: slot.slot }); });
  (d.bench || []).forEach((p) => out.push({ p, where: "bench" }));
  (d.reserves || []).forEach((p) => out.push({ p, where: "reserves" }));
  return out;
}

function findPlayer(s, pid) {
  return allPlayers(s).find((entry) => entry.p.id === pid) || null;
}

// ------------------------------------------------------------------ cizim

function buildToken(p, mode, slotRole) {
  const tok = el("div", `tb-token ${mode === "row" ? "row" : "pitch"}`);
  tok.dataset.pid = String(p.id);
  tok.dataset.target = `p:${p.id}`;
  tok.tabIndex = 0;
  tok.setAttribute("role", "button");
  if (p.out) tok.classList.add("out");
  if (p.offpos) tok.classList.add("offpos");
  if (p.pos === "GK") tok.classList.add("gk");

  const ring = el("div", `tb-ring band-${p.band || "good"}`);
  ring.style.setProperty("--c", String(Math.max(0, Math.min(100, Number(p.cond) || 0))));
  const disc = el("div", "tb-disc");
  disc.appendChild(el("span", "tb-ini", p.ini));
  ring.appendChild(disc);
  tok.appendChild(ring);

  if (p.badges && p.badges.length) {
    const badges = el("div", "tb-badges");
    p.badges.forEach((b) => badges.appendChild(el("span", `tb-b b-${b}`, b)));
    tok.appendChild(badges);
  }
  if (p.out) tok.appendChild(el("span", "tb-x", "✚"));

  const text = el("div", "tb-text");
  text.appendChild(el("div", "tb-name", mode === "row" ? p.name : p.short));
  const meta = el("div", "tb-meta");
  meta.appendChild(el("span", "tb-pos", mode === "row" ? p.pos : (p.offpos ? `${p.pos}→${slotRole}` : p.pos)));
  meta.appendChild(el("span", "tb-stars", p.stars));
  if (mode === "row") meta.appendChild(el("span", "tb-cond", `%${p.cond}`));
  text.appendChild(meta);
  tok.appendChild(text);

  const parts = [p.name, p.pos, `${p.cond}% kondisyon`];
  if (p.stars) parts.push(`Mevcut yetenek: ${p.stars}`);
  if (p.offpos) parts.push(`mevki dışı (${p.pos} → ${slotRole})`);
  if (p.out) parts.push(`oynayamaz: ${p.out}`);
  (p.badges || []).forEach((b) => parts.push({ C: "kaptan", P: "penaltıcı", F: "serbest vuruşçu", K: "kornerci" }[b] || b));
  tok.setAttribute("aria-label", parts.join(", "));
  tok.title = parts.join(" · ");
  return tok;
}

function buildPitch(s) {
  const pitch = el("div", "tb-pitch");
  pitch.setAttribute("role", "group");
  pitch.setAttribute("aria-label", `${label(s, "aria_board")} ${s.data.formation || ""}`);
  ["half", "circle", "spot", "box-top", "box-bot", "goal-top", "goal-bot", "arc-top", "arc-bot"].forEach((m) => {
    pitch.appendChild(el("div", `tb-mark ${m}`));
  });
  const tag = el("div", "tb-formation", s.data.formation || "");
  pitch.appendChild(tag);
  (s.data.slots || []).forEach((slot) => {
    const node = el("div", "tb-slot");
    node.dataset.slot = String(slot.slot);
    node.style.left = `${Number(slot.x) || 0}%`;
    node.style.top = `${Number(slot.y) || 0}%`;
    if (slot.player) {
      node.appendChild(buildToken(slot.player, "pitch", slot.role));
    } else {
      node.dataset.target = `s:${slot.slot}`;
      node.tabIndex = 0;
      node.classList.add("empty");
      node.setAttribute("role", "button");
      node.setAttribute("aria-label", `${slot.role} ${label(s, "empty")}`);
      const hole = el("div", "tb-hole");
      hole.appendChild(el("span", "tb-hole-role", slot.role));
      node.appendChild(hole);
      node.appendChild(el("div", "tb-name", label(s, "empty")));
    }
    pitch.appendChild(node);
  });
  return pitch;
}

function buildZone(s, zone, players) {
  const box = el("section", `tb-zone tb-${zone}`);
  box.dataset.zone = zone;
  box.dataset.target = `z:${zone}`;
  box.tabIndex = 0;
  const title = zone === "bench" ? label(s, "bench") : label(s, "reserves");
  const count = zone === "bench" ? `${players.length}/${s.data.max_bench || 7}` : String(players.length);
  box.setAttribute("aria-label", `${title} ${count}`);
  const head = el("header", "tb-zone-head");
  head.appendChild(el("span", "tb-zone-title", title));
  const badge = el("span", "tb-count", count);
  if (zone === "bench" && players.length > (s.data.max_bench || 7)) badge.classList.add("over");
  head.appendChild(badge);
  box.appendChild(head);
  const list = el("div", "tb-list");
  players.forEach((p) => list.appendChild(buildToken(p, "row")));
  box.appendChild(list);
  box.appendChild(el("div", "tb-drop-hint", zone === "bench" ? label(s, "drop_bench") : label(s, "drop_reserves")));
  return box;
}

function render(root, s) {
  const active = root.getRootNode().activeElement;
  const focusKey = active && root.contains(active) && active.dataset ? active.dataset.target : s.focusKey;
  const view = root.querySelector(".tb-view");
  view.replaceChildren();
  root.classList.toggle("tb-locked", !!s.data.locked);
  root.classList.remove("tb-busy");
  clearTimeout(s.busyTimer);

  const wrap = el("div", "tb-wrap");
  wrap.appendChild(buildPitch(s));
  const side = el("div", "tb-side");
  side.appendChild(buildZone(s, "bench", s.data.bench || []));
  side.appendChild(buildZone(s, "reserves", s.data.reserves || []));
  wrap.appendChild(side);
  view.appendChild(wrap);
  view.appendChild(el("p", "tb-hint", s.data.locked ? label(s, "locked") : label(s, "hint")));

  if (s.pick !== null && !findPlayer(s, s.pick)) s.pick = null;
  markPick(root, s);
  if (focusKey) {
    const again = root.querySelector(`[data-target="${CSS.escape(focusKey)}"]`);
    if (again) again.focus({ preventScroll: true });
  }
}

// ------------------------------------------------------------------ niyet

function emit(root, s, payload) {
  if (root.classList.contains("tb-busy")) return;
  s.nonce += 1;
  const message = Object.assign({ n: s.nonce, rev: Number(s.data.rev) || 0 }, payload);
  root.classList.add("tb-busy");
  clearTimeout(s.busyTimer);
  s.busyTimer = setTimeout(() => root.classList.remove("tb-busy"), BUSY_MS);
  announce(root, label(s, "sent"));
  s.send("intent", message);
}

function drop(root, s, pid, target) {
  if (!target || s.data.locked) return;
  if (target.kind === "player") {
    if (target.pid !== pid) emit(root, s, { action: "swap", player: pid, with: target.pid });
  } else if (target.kind === "slot") {
    emit(root, s, { action: "move", player: pid, to: "slot", slot: target.slot });
  } else if (target.kind === "bench" || target.kind === "reserves") {
    const entry = findPlayer(s, pid);
    if (entry && entry.where === target.kind) return;
    emit(root, s, { action: "move", player: pid, to: target.kind });
  }
}

function targetOf(node, root) {
  if (!node || !root.contains(node)) return null;
  const tok = node.closest(".tb-token");
  if (tok) return { kind: "player", pid: Number(tok.dataset.pid), node: tok };
  const slot = node.closest(".tb-slot");
  if (slot) return { kind: "slot", slot: Number(slot.dataset.slot), node: slot };
  const zone = node.closest("[data-zone]");
  if (zone) return { kind: zone.dataset.zone, node: zone };
  return null;
}

function announce(root, text) {
  const live = root.querySelector(".tb-live");
  if (live) live.textContent = text;
}

// ------------------------------------------------------------------ secim (dokun / klavye)

function markPick(root, s) {
  root.querySelectorAll(".tb-picked").forEach((n) => n.classList.remove("tb-picked"));
  root.classList.toggle("tb-picking", s.pick !== null);
  if (s.pick === null) return;
  const tok = root.querySelector(`.tb-token[data-pid="${s.pick}"]`);
  if (tok) tok.classList.add("tb-picked");
}

function pickUp(root, s, pid) {
  if (s.data.locked) return;
  s.pick = pid;
  markPick(root, s);
  const entry = findPlayer(s, pid);
  announce(root, fmt(label(s, "picked"), entry ? entry.p.name : ""));
}

function cancelPick(root, s, quiet) {
  if (s.pick === null) return;
  s.pick = null;
  markPick(root, s);
  if (!quiet) announce(root, label(s, "cancelled"));
}

// ------------------------------------------------------------------ menu

function closeMenu(root, s) {
  const menu = root.querySelector(".tb-menu");
  if (menu) menu.remove();
  s.menuFor = null;
}

function openMenu(root, s, pid, clientX, clientY) {
  if (s.menuFor === pid && root.querySelector(".tb-menu")) return;
  closeMenu(root, s);
  const entry = findPlayer(s, pid);
  if (!entry) return;
  cancelPick(root, s, true);
  s.menuFor = pid;
  const p = entry.p;
  const menu = el("div", "tb-menu");
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", p.name);
  menu.appendChild(el("div", "tb-menu-title", p.name));
  const has = (b) => (p.badges || []).includes(b);
  const items = [["profile", label(s, "menu_profile"), false]];
  if (!s.data.locked) {
    items.push(["captain", has("C") ? label(s, "is_captain") : label(s, "menu_captain"), has("C")]);
    items.push(["penalty", has("P") ? label(s, "is_penalty") : label(s, "menu_penalty"), has("P")]);
    items.push(["free_kick", has("F") ? label(s, "is_free_kick") : label(s, "menu_free_kick"), has("F")]);
    items.push(["corner", has("K") ? label(s, "is_corner") : label(s, "menu_corner"), has("K")]);
    if (entry.where === "slot") items.push(["bench", label(s, "menu_bench"), false]);
    if (entry.where !== "reserves") items.push(["reserves", label(s, "menu_reserves"), false]);
  }
  items.forEach(([action, text, disabled]) => {
    const item = el("button", "tb-menu-item", text);
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.dataset.action = action;
    if (disabled) {
      item.disabled = true;
      item.setAttribute("aria-disabled", "true");
    }
    menu.appendChild(item);
  });
  root.appendChild(menu);
  const box = root.getBoundingClientRect();
  const width = menu.offsetWidth || 220;
  const height = menu.offsetHeight || 260;
  let left = clientX - box.left;
  let top = clientY - box.top;
  left = Math.max(4, Math.min(left, box.width - width - 4));
  if (top + height > box.height - 4) top = Math.max(4, top - height);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  const first = menu.querySelector(".tb-menu-item:not([disabled])");
  if (first) first.focus({ preventScroll: true });
}

function runMenu(root, s, action) {
  const pid = s.menuFor;
  const tok = pid !== null ? root.querySelector(`.tb-token[data-pid="${pid}"]`) : null;
  closeMenu(root, s);
  if (pid === null) return;
  if (tok) s.focusKey = tok.dataset.target;
  if (action === "profile") emit(root, s, { action: "profile", player: pid });
  else if (action === "bench" || action === "reserves") emit(root, s, { action: "move", player: pid, to: action });
  else emit(root, s, { action: "role", player: pid, role: action });
}

// ------------------------------------------------------------------ surukleme

function startGhost(root, d) {
  const src = d.tok.querySelector(".tb-ring");
  const ghost = el("div", "tb-ghost");
  if (src) ghost.appendChild(src.cloneNode(true));
  const name = d.tok.querySelector(".tb-name");
  if (name) ghost.appendChild(el("div", "tb-name", name.textContent));
  root.appendChild(ghost);
  d.ghost = ghost;
  d.tok.classList.add("tb-lifted");
  root.classList.add("tb-dragging");
}

function moveGhost(root, d, x, y) {
  if (!d.ghost) return;
  const box = root.getBoundingClientRect();
  d.ghost.style.left = `${x - box.left}px`;
  d.ghost.style.top = `${y - box.top}px`;
}

function endGhost(root, d) {
  if (d.ghost) d.ghost.remove();
  d.ghost = null;
  if (d.tok) d.tok.classList.remove("tb-lifted");
  root.classList.remove("tb-dragging");
  root.querySelectorAll(".tb-over").forEach((n) => n.classList.remove("tb-over"));
}

function highlight(root, target) {
  root.querySelectorAll(".tb-over").forEach((n) => n.classList.remove("tb-over"));
  if (target && target.node) target.node.classList.add("tb-over");
}

// ------------------------------------------------------------------ olaylar (bir kez baglanir)

function scrollParentOf(root) {
  let node = root.getRootNode().host || root.parentElement;
  while (node && node !== document.body && node !== document.documentElement) {
    const overflow = getComputedStyle(node).overflowY;
    if ((overflow === "auto" || overflow === "scroll") && node.scrollHeight > node.clientHeight + 1) return node;
    node = node.parentElement;
  }
  return document.scrollingElement || document.documentElement;
}

function bind(root, s, host) {
  const hitTest = (x, y) => (host && typeof host.elementFromPoint === "function"
    ? host.elementFromPoint(x, y) : document.elementFromPoint(x, y));

  // Suruklerken ekranin ust / alt kenarina gelince sayfa kayar (telefonda saha ve kulube alt alta)
  const autoScroll = () => {
    const d = s.drag;
    if (!d || !d.active) {
      s.scrollRaf = null;
      return;
    }
    const top = EDGE_TOP_PX;
    const bottom = window.innerHeight - EDGE_PX;
    let step = 0;
    if (d.lastY < top) step = -Math.ceil((top - d.lastY) / 5);
    else if (d.lastY > bottom) step = Math.ceil((d.lastY - bottom) / 5);
    if (step) {
      if (!d.scroller) d.scroller = scrollParentOf(root);
      d.scroller.scrollTop += step;
      moveGhost(root, d, d.lastX, d.lastY);
      highlight(root, targetOf(hitTest(d.lastX, d.lastY), root));
    }
    s.scrollRaf = requestAnimationFrame(autoScroll);
  };

  const tap = (pid, tok) => {
    const now = Date.now();
    if (s.lastTap && s.lastTap.pid === pid && now - s.lastTap.t < DOUBLE_MS) {
      s.lastTap = null;
      cancelPick(root, s, true);
      s.focusKey = tok.dataset.target;
      emit(root, s, { action: "profile", player: pid });
      return;
    }
    s.lastTap = { pid, t: now };
    if (s.pick !== null && s.pick !== pid) {
      const from = s.pick;
      cancelPick(root, s, true);
      drop(root, s, from, { kind: "player", pid });
      return;
    }
    if (s.pick === pid) cancelPick(root, s);
    else pickUp(root, s, pid);
  };

  root.addEventListener("pointerdown", (e) => {
    if (!e.target.closest(".tb-menu")) closeMenu(root, s);
    const tok = e.target.closest(".tb-token");
    if (!tok) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (e.pointerType === "mouse") {
      e.preventDefault();                        // metin secimi / tarayicinin kendi surukle-birakmasi baslamasin
      tok.focus({ preventScroll: true });
    }
    const d = {
      pid: Number(tok.dataset.pid), tok, id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: Date.now(),
      touch: e.pointerType !== "mouse", active: false, lifted: e.pointerType === "mouse", menu: false, ghost: null,
      timer: null,
    };
    if (d.touch) {
      d.timer = setTimeout(() => {
        if (s.drag === d && !d.active) {
          d.lifted = true;
          tok.classList.add("tb-hold");
        }
      }, LIFT_MS);
    }
    s.drag = d;
    try { tok.setPointerCapture(e.pointerId); } catch (_err) { /* eski tarayici */ }
  });

  root.addEventListener("pointermove", (e) => {
    const d = s.drag;
    if (!d || e.pointerId !== d.id) return;
    const dist = Math.hypot(e.clientX - d.x0, e.clientY - d.y0);
    if (!d.active) {
      if (d.touch && !d.lifted) {
        if (dist <= TOUCH_SCROLL_PX) return;
        const dx = Math.abs(e.clientX - d.x0);
        const dy = Math.abs(e.clientY - d.y0);
        if (dy >= dx) {                          // hizli dikey kaydirma: sayfa kaysin, surukleme yok
          clearTimeout(d.timer);
          d.tok.classList.remove("tb-hold");
          s.drag = null;
          return;
        }
        d.lifted = true;                         // yatay hareket sayfayi kaydirmaz: dogrudan surukle
      }
      if (dist < DRAG_PX || s.data.locked) return;
      d.active = true;
      d.tok.classList.remove("tb-hold");
      cancelPick(root, s, true);
      closeMenu(root, s);
      startGhost(root, d);
      if (!s.scrollRaf) s.scrollRaf = requestAnimationFrame(autoScroll);
    }
    e.preventDefault();
    d.lastX = e.clientX;
    d.lastY = e.clientY;
    moveGhost(root, d, e.clientX, e.clientY);
    highlight(root, targetOf(hitTest(e.clientX, e.clientY), root));
  });

  root.addEventListener("dragstart", (e) => e.preventDefault());   // yerel HTML surukleme hic baslamaz

  // Dokunmatikte surukleme basladiysa sayfa kaymasin (touch-action: pan-y iken tek yol budur)
  root.addEventListener("touchmove", (e) => {
    if (s.drag && (s.drag.active || s.drag.lifted) && e.cancelable) e.preventDefault();
  }, { passive: false });

  root.addEventListener("pointerup", (e) => {
    const d = s.drag;
    if (!d || e.pointerId !== d.id) return;
    clearTimeout(d.timer);
    d.tok.classList.remove("tb-hold");
    s.drag = null;
    if (d.active) {
      const target = targetOf(hitTest(e.clientX, e.clientY), root);
      endGhost(root, d);
      s.focusKey = d.tok.dataset.target;
      drop(root, s, d.pid, target);
      return;
    }
    if (d.menu) return;
    if (d.touch && Date.now() - d.t0 >= MENU_MS) {   // uzun bas + birak: menu (telefonda sag tik yok)
      openMenu(root, s, d.pid, e.clientX, e.clientY);
      return;
    }
    tap(d.pid, d.tok);
  });

  root.addEventListener("pointercancel", () => {
    const d = s.drag;
    if (!d) return;
    clearTimeout(d.timer);
    d.tok.classList.remove("tb-hold");
    endGhost(root, d);
    s.drag = null;
  });

  root.addEventListener("contextmenu", (e) => {
    const tok = e.target.closest(".tb-token");
    if (!tok) return;
    e.preventDefault();
    if (s.drag) {
      if (s.drag.active) return;                 // suruklerken gelen uzun-bas menusu yok sayilir
      s.drag.menu = true;
      clearTimeout(s.drag.timer);
    }
    openMenu(root, s, Number(tok.dataset.pid), e.clientX, e.clientY);
  });

  root.addEventListener("click", (e) => {
    const item = e.target.closest(".tb-menu-item");
    if (item) {
      if (!item.disabled) runMenu(root, s, item.dataset.action);
      return;
    }
    if (e.target.closest(".tb-token") || e.target.closest(".tb-menu")) return;
    if (s.pick === null) return;
    const target = targetOf(e.target, root);
    const from = s.pick;
    cancelPick(root, s, true);
    if (target) drop(root, s, from, target);
  });

  root.addEventListener("keydown", (e) => {
    const menu = root.querySelector(".tb-menu");
    if (menu) {
      const items = Array.from(menu.querySelectorAll(".tb-menu-item:not([disabled])"));
      const at = items.indexOf(root.getRootNode().activeElement);
      if (e.key === "Escape") {
        e.preventDefault();
        const pid = s.menuFor;
        closeMenu(root, s);
        const tok = root.querySelector(`.tb-token[data-pid="${pid}"]`);
        if (tok) tok.focus();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
        if (next) next.focus();
      } else if (e.key === "Tab") {
        closeMenu(root, s);
      }
      return;
    }
    const node = e.target;
    if (!node || !node.dataset) return;
    const tok = node.closest ? node.closest(".tb-token") : null;
    if (e.key === "Escape") {
      cancelPick(root, s);
      return;
    }
    if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
      if (tok) {
        e.preventDefault();
        const r = tok.getBoundingClientRect();
        openMenu(root, s, Number(tok.dataset.pid), r.left + r.width / 2, r.top + r.height / 2);
      }
      return;
    }
    if (e.key === "Enter" || e.key === " ") {
      const target = targetOf(node, root);
      if (!target) return;
      e.preventDefault();
      s.focusKey = node.dataset.target;
      if (s.pick === null) {
        if (tok) pickUp(root, s, Number(tok.dataset.pid));
        return;
      }
      const from = s.pick;
      if (target.kind === "player" && target.pid === from) {
        cancelPick(root, s);
        return;
      }
      cancelPick(root, s, true);
      drop(root, s, from, target);
      return;
    }
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
      const targets = Array.from(root.querySelectorAll("[data-target]"));
      const at = targets.indexOf(node);
      if (at < 0) return;
      e.preventDefault();
      const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : -1;
      const next = targets[(at + step + targets.length) % targets.length];
      if (next) next.focus();
    }
  });
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector(".tb-root");
  if (!root) return undefined;
  let s = root.__ofmBoard;
  if (!s) {
    s = { nonce: 0, pick: null, drag: null, menuFor: null, lastTap: null, focusKey: null, busyTimer: null,
      scrollRaf: null };
    root.__ofmBoard = s;
    bind(root, s, parentElement);
  }
  s.data = data || {};
  s.send = setTriggerValue;
  closeMenu(root, s);
  render(root, s);
  return undefined;
}
