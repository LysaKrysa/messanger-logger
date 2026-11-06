"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let csrf = "", active = false, ready = false, frameBusy = false, finished = false, viewGeneration = 0, reconnectTimer = null, fallbackTimer = null, statusBusy = false, compatibility = false;
  let invitation = location.hash.slice(1);
  const showError = text => { $("setup-error").textContent = text; $("setup-error").hidden = false; };
  const hideError = () => { $("setup-error").hidden = true; };
  const inputs = new MessengerRemote.InputQueue(events => api("input", {events}), error => showError(error.message));
  function stopView() {
    viewGeneration += 1;
    frameBusy = false; statusBusy = false;
    clearTimeout(reconnectTimer); clearTimeout(fallbackTimer);
    reconnectTimer = null; fallbackTimer = null;
    $("remote-screen").onerror = null;
    const old = $("remote-screen").src;
    $("remote-screen").removeAttribute("src");
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  }
  function resetInput() { inputs.clear(); $("remote-typing").value = ""; }

  addEventListener("hashchange", () => {
    const next = location.hash.slice(1);
    if (!next) return;
    invitation = next; csrf = ""; active = false; finished = false; ready = false; stopView(); resetInput();
    hideError(); $("claim-form").reset();
    $("welcome").hidden = false; $("facebook").hidden = true; $("telegram").hidden = true;
    $("remote-screen").hidden = true; $("remote-placeholder").hidden = false;
    $("step-welcome").classList.add("current"); $("step-facebook").classList.remove("current"); $("step-telegram").classList.remove("current");
  });
  async function api(action, body, anonymous = false) {
    const auth = csrf;
    const response = await fetch("/setup/api/" + action, {method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store", headers: {"Content-Type": "application/json", ...(anonymous ? {} : {"X-Setup-CSRF": auth})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401 && auth === csrf) { active = false; finished = true; stopView(); resetInput(); $("remote-screen").hidden = true; showError(data.error || "This setup session has expired or was disabled. Ask for a new invitation."); }
      throw new Error(data.error || "Setup could not continue. Please try again.");
    }
    return data;
  }
  function progress(data) {
    csrf = data.csrf;
    finished = data.state === "completed";
    $("welcome").hidden = true; $("facebook").hidden = finished; $("telegram").hidden = !finished;
    $("step-welcome").classList.remove("current");
    $("step-facebook").classList.toggle("current", !finished); $("step-telegram").classList.toggle("current", finished);
    if (finished) {
      active = false; stopView(); resetInput();
      $("telegram-invite").hidden = !data.telegram_url;
      if (data.telegram_url && /^https:\/\/t\.me\/[A-Za-z0-9_]+\?start=[A-Za-z0-9_-]+$/.test(data.telegram_url)) $("telegram-invite").href = data.telegram_url;
      $("pairing-status").textContent = ({waiting: "Open Telegram above and press Start to connect your private chat.", pending_approval: "Telegram connected. Your administrator will approve your identity and start the logger.", approved: "Your Telegram destination is approved. Your administrator controls when capture starts.", expired: "The Telegram invitation expired. Ask your administrator for a new Telegram invitation."})[data.pairing];
    }
  }
  async function status() {
    try { const data = await api("status"); if (!invitation) progress(data); } catch (error) { if (!invitation) showError(error.message); }
  }
  $("claim-form").addEventListener("submit", async event => {
    event.preventDefault(); hideError(); $("claim-button").disabled = true;
    try {
      const result = await api("claim", {token: invitation, name: $("guest-name").value, consent: $("guest-consent").checked}, true);
      invitation = ""; history.replaceState(null, "", "/setup"); progress(result);
    } catch (error) { showError(error.message); } finally { $("claim-button").disabled = false; }
  });
  function metadata(data) {
    const screen = $("remote-screen");
    if (screen.width !== data.width || screen.height !== data.height) { screen.width = data.width; screen.height = data.height; }
    $("remote-site").textContent = data.site; ready = data.ready;
    $("facebook-ready").textContent = ready ? "A signed-in session was detected. Confirm below when you have finished Facebook's checks." : "Sign in to Facebook first. Then confirm the connection here.";
    $("finish-facebook").disabled = !ready || !$("approve-session").checked;
  }
  async function viewStatus() {
    if (!active || finished || statusBusy || document.hidden) return;
    statusBusy = true; const generation = viewGeneration;
    try { const data = await api("view", {}); if (active && generation === viewGeneration) metadata(data); }
    catch (error) { if (active && generation === viewGeneration) showError(error.message); }
    finally { if (generation === viewGeneration) statusBusy = false; }
  }
  async function frame() {
    if (!active || finished || frameBusy || document.hidden || !compatibility) return;
    frameBusy = true; const generation = viewGeneration;
    try {
      const data = await api("frame", {});
      const bytes = Uint8Array.from(atob(data.jpeg), value => value.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], {type: "image/jpeg"}));
      const decoded = new Image(); decoded.src = url;
      await decoded.decode();
      if (!active || generation !== viewGeneration) { URL.revokeObjectURL(url); return; }
      const previous = $("remote-screen").src;
      $("remote-screen").src = url; $("remote-screen").hidden = false; $("remote-placeholder").hidden = true;
      if (previous.startsWith("blob:")) URL.revokeObjectURL(previous);
      metadata(data);
    } catch (error) { if (active && generation === viewGeneration) showError(error.message); }
    finally { if (generation === viewGeneration) { frameBusy = false; if (active && compatibility && !document.hidden) fallbackTimer = setTimeout(frame, 250); } }
  }
  function startView(attempt = 0) {
    stopView();
    if (!active || finished || document.hidden) return;
    const generation = viewGeneration;
    if (compatibility) { frame(); return; }
    const screen = $("remote-screen");
    screen.onerror = () => {
      if (!active || document.hidden || generation !== viewGeneration) return;
      if (attempt >= 2) { compatibility = true; startView(); }
      else reconnectTimer = setTimeout(() => startView(attempt + 1), 400 * (attempt + 1));
    };
    screen.src = "/setup/api/stream?view=" + generation;
    screen.hidden = false; $("remote-placeholder").hidden = true;
    viewStatus();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopView();
    else if (active && !finished) startView();
  });
  $("start-browser").addEventListener("click", async () => {
    hideError(); $("start-browser").disabled = true; $("start-browser").textContent = "Opening your browser…";
    try { await api("start", {mobile: matchMedia("(max-width: 760px)").matches}); active = true; compatibility = false; await viewStatus(); startView(); } catch (error) { showError(error.message); }
    finally { $("start-browser").disabled = false; $("start-browser").textContent = "Open Facebook ↗"; }
  });
  function input(data) {
    if (!active || finished) return;
    inputs.push(data, data.action === "click" || data.action === "key" || data.action === "reload");
  }
  let touch = null, suppressClickUntil = 0;
  $("remote-screen").addEventListener("pointerdown", event => {
    if (event.pointerType !== "touch" || !active) return;
    suppressClickUntil = 0;
    touch = {id: event.pointerId, start: event.clientY, last: event.clientY, moved: false};
    event.currentTarget.setPointerCapture(event.pointerId);
  });
  $("remote-screen").addEventListener("pointermove", event => {
    if (!touch || touch.id !== event.pointerId) return;
    if (Math.abs(event.clientY - touch.start) > 6) touch.moved = true;
    if (touch.moved) {
      event.preventDefault();
      const rect = event.currentTarget.getBoundingClientRect();
      input({action: "scroll", delta: (touch.last - event.clientY) * event.currentTarget.height / rect.height});
      touch.last = event.clientY;
    }
  });
  const finishTouch = event => {
    if (!touch || touch.id !== event.pointerId) return;
    if (touch.moved || event.type === "pointercancel") suppressClickUntil = Date.now() + 500;
    touch = null;
  };
  $("remote-screen").addEventListener("pointerup", finishTouch);
  $("remote-screen").addEventListener("pointercancel", finishTouch);
  $("remote-screen").addEventListener("click", event => {
    if (Date.now() < suppressClickUntil) return;
    const rect = event.currentTarget.getBoundingClientRect();
    input({action: "click", x: (event.clientX - rect.left) * event.currentTarget.width / rect.width, y: (event.clientY - rect.top) * event.currentTarget.height / rect.height});
    $("remote-typing").focus({preventScroll: true});
  });
  const keys = new Set(["Backspace", "Delete", "Enter", "Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);
  $("remote-typing").addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") { event.preventDefault(); input({action: "key", key: "Control+A"}); }
    else if (keys.has(event.key)) { event.preventDefault(); input({action: "key", key: event.key === "Tab" && event.shiftKey ? "Shift+Tab" : event.key}); }
  });
  $("remote-typing").addEventListener("input", event => {
    if (event.isComposing) return;
    const text = event.target.value; event.target.value = "";
    if (text) input({action: "text", text});
  });
  $("remote-typing").addEventListener("beforeinput", event => {
    const key = ({deleteContentBackward: "Backspace", deleteContentForward: "Delete", insertLineBreak: "Enter", insertParagraph: "Enter"})[event.inputType];
    if (key) { event.preventDefault(); input({action: "key", key}); }
  });
  $("remote-typing").addEventListener("compositionend", event => { const text = event.target.value; event.target.value = ""; if (text) input({action: "text", text}); });
  $("remote-screen").addEventListener("wheel", event => { if (active) { event.preventDefault(); input({action: "scroll", delta: event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? event.currentTarget.height : 1)}); } }, {passive: false});
  $("remote-enter").addEventListener("click", () => input({action: "key", key: "Enter"}));
  $("reload-browser").addEventListener("click", () => input({action: "reload"}));
  $("approve-session").addEventListener("change", () => { $("finish-facebook").disabled = !ready || !$("approve-session").checked; });
  $("finish-facebook").addEventListener("click", async () => {
    hideError(); $("finish-facebook").disabled = true;
    try { await inputs.drain(); progress(await api("finish", {approve: $("approve-session").checked})); }
    catch (error) { showError(error.message); $("finish-facebook").disabled = !ready || !$("approve-session").checked; }
  });
  $("cancel-setup").addEventListener("click", async () => {
    try { await api("cancel", {}); active = false; finished = true; stopView(); resetInput(); $("facebook").hidden = true; showError("Setup cancelled. This invitation can no longer be used."); }
    catch (error) { showError(error.message); }
  });
  setInterval(viewStatus, 800);
  setInterval(() => { if (csrf && (finished || !active)) status(); }, 6000);
  if (!invitation) status();
})();
