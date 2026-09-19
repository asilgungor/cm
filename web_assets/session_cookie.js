// OFM kalici oturum cerezi (14H) -- st.components.v2 bileseni, gorunmez; harici kutuphane / ag istegi YOK.
//
// Python (web_common.session_cookie_sync) bekleyen TEK islemi yollar:
//   {op: "set",   name, token, max_age, nonce}  -> belirteci cereze yazar (max_age null: tarayici oturumu cerezi)
//   {op: "clear", name, nonce}                  -> cerezi siler
//   {op: "none"}                                -> hicbir sey yapmaz
// Her nonce bu sayfada EN FAZLA bir kez uygulanir ve bir kez onaylanir: setTriggerValue("done", {n, ok}).
// Cerez: Path=/, SameSite=Strict, https'te Secure (ad "__Host-" onekli: Domain'siz, kardes alt alan adi ezemez).
// Ad ve deger siki bicim denetiminden gecer (baska cerez ya da nitelik enjekte edilemez). Belirtec adrese,
// localStorage'a ya da konsola ASLA yazilmaz.

const NAME_RE = /^(__Host-)?ofm_sid_[0-9]{1,5}$/;
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const MAX_AGE_LIMIT = 60 * 60 * 24 * 31;

function secure() {
  return window.location.protocol === "https:";
}

function attributes(maxAge) {
  let attrs = "; Path=/; SameSite=Strict";
  if (maxAge !== null) attrs += "; Max-Age=" + String(maxAge);
  if (secure()) attrs += "; Secure";
  return attrs;
}

function current(name) {
  const prefix = name + "=";
  const parts = String(document.cookie || "").split(";");
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i].trim();
    if (part.indexOf(prefix) === 0) return part.slice(prefix.length);
  }
  return null;
}

function apply(op) {
  if (!op || typeof op.name !== "string" || !NAME_RE.test(op.name)) return false;
  if (op.name.indexOf("__Host-") === 0 && !secure()) return false;   // __Host- yalnizca https'te gecerli
  if (op.op === "set") {
    if (typeof op.token !== "string" || !TOKEN_RE.test(op.token)) return false;
    let maxAge = null;
    if (typeof op.max_age === "number" && isFinite(op.max_age)) {
      maxAge = Math.max(0, Math.min(MAX_AGE_LIMIT, Math.floor(op.max_age)));
    }
    document.cookie = op.name + "=" + op.token + attributes(maxAge);
    return current(op.name) === op.token;
  }
  if (op.op === "clear") {
    document.cookie = op.name + "=" + attributes(0);
    return current(op.name) === null || current(op.name) === "";
  }
  return false;
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const host = parentElement || {};
  const state = host.__ofmSessionCookie || (host.__ofmSessionCookie = { done: {} });
  const op = data || {};
  const nonce = typeof op.nonce === "string" ? op.nonce : null;
  if (!nonce || (op.op !== "set" && op.op !== "clear") || state.done[nonce]) return undefined;
  state.done[nonce] = true;
  let ok = false;
  try {
    ok = apply(op);
  } catch (e) {
    ok = false;
  }
  setTimeout(function () { setTriggerValue("done", { n: nonce, ok: ok }); }, 0);
  return undefined;
}
