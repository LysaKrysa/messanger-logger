"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let csrf = "", active = false, ready = false, frameBusy = false, finished = false, sending = Promise.resolve();
  let invitation = location.hash.slice(1);
  const showError = text => { $("setup-error").textContent = text; $("setup-error").hidden = false; };
  const hideError = () => { $("setup-error").hidden = true; };
  addEventListener("hashchange", () => {
    const next = location.hash.slice(1);
    if (!next) return;
    invitation = next; csrf = ""; active = false; finished = false; ready = false;
    hideError(); $("claim-form").reset();
    $("welcome").hidden = false; $("facebook").hidden = true; $("telegram").hidden = true;
    $("remote-screen").hidden = true; $("remote-placeholder").hidden = false;
    $("step-welcome").classList.add("current"); $("step-facebook").classList.remove("current"); $("step-telegram").classList.remove("current");
  });
  async function api(action, body, anonymous = false) {
    const response = await fetch("/setup/api/" + action, {method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store", headers: {"Content-Type": "application/json", ...(anonymous ? {} : {"X-Setup-CSRF": csrf})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401) { active = false; finished = true; $("remote-screen").hidden = true; $("remote-typing").value = ""; }
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
      active = false; $("remote-screen").removeAttribute("src"); $("remote-typing").value = "";
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
  async function frame() {
    if (!active || finished || frameBusy || document.hidden) return;
    frameBusy = true;
    try {
      const data = await api("frame", {});
      if (!active || finished) return;
      const bytes = Uint8Array.from(atob(data.jpeg), value => value.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], {type: "image/jpeg"}));
      const previous = $("remote-screen").src;
      $("remote-screen").width = data.width; $("remote-screen").height = data.height;
      $("remote-screen").onload = () => { if (previous.startsWith("blob:")) URL.revokeObjectURL(previous); };
      $("remote-screen").src = url; $("remote-screen").hidden = false; $("remote-placeholder").hidden = true;
      $("remote-site").textContent = data.site; ready = data.ready;
      $("facebook-ready").textContent = ready ? "A signed-in session was detected. Confirm below when you have finished Facebook's checks." : "Sign in to Facebook first. Then confirm the connection here.";
      $("finish-facebook").disabled = !ready || !$("approve-session").checked;
    } catch (error) { showError(error.message); active = false; $("remote-placeholder").hidden = false; $("remote-screen").hidden = true; }
    finally { frameBusy = false; }
  }
  $("start-browser").addEventListener("click", async () => {
    hideError(); $("start-browser").disabled = true; $("start-browser").textContent = "Opening your browser…";
    try { await api("start", {mobile: matchMedia("(max-width: 760px)").matches}); active = true; await frame(); } catch (error) { showError(error.message); }
    finally { $("start-browser").disabled = false; $("start-browser").textContent = "Open Facebook ↗"; }
  });
  function input(data) {
    if (!active || finished) return;
    sending = sending.then(() => api("input", data)).catch(error => showError(error.message));
  }
  $("remote-screen").addEventListener("click", event => {
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
  $("remote-screen").addEventListener("wheel", event => { if (active) { event.preventDefault(); input({action: "scroll", delta: event.deltaY}); } }, {passive: false});
  $("remote-enter").addEventListener("click", () => input({action: "key", key: "Enter"}));
  $("reload-browser").addEventListener("click", () => input({action: "reload"}));
  $("approve-session").addEventListener("change", () => { $("finish-facebook").disabled = !ready || !$("approve-session").checked; });
  $("finish-facebook").addEventListener("click", async () => {
    hideError(); $("finish-facebook").disabled = true;
    try { await sending; progress(await api("finish", {approve: $("approve-session").checked})); }
    catch (error) { showError(error.message); $("finish-facebook").disabled = !ready || !$("approve-session").checked; }
  });
  $("cancel-setup").addEventListener("click", async () => {
    try { await api("cancel", {}); active = false; finished = true; $("facebook").hidden = true; showError("Setup cancelled. This invitation can no longer be used."); }
    catch (error) { showError(error.message); }
  });
  setInterval(frame, 1200);
  setInterval(() => { if (csrf && (finished || !active)) status(); }, 6000);
  if (!invitation) status();
})();
