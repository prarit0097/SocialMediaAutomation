/* Account picker: searchable dropdown (page name + thumbnail + FB/IG badge).
 * Replaces raw "Account ID" entry. Writes the chosen id into a hidden input
 * (same name/id the existing page JS already reads) and dispatches input+change
 * so downstream wiring (page-name autofill, scheduler assist, fetch buttons) just works.
 *
 * Markup: <div data-account-picker data-target="hiddenInputId"></div>
 * with a sibling/anywhere <input type="hidden" id="hiddenInputId" name="...">.
 */
(function () {
  "use strict";

  var ACCOUNTS_URL = "/api/accounts/";
  var accountsPromise = null;

  var STYLES = [
    ".account-picker{position:relative;width:100%}",
    ".acct-pick-trigger{display:flex;align-items:center;gap:10px;width:100%;min-height:44px;padding:8px 12px;border:1px solid rgba(120,120,140,.35);border-radius:10px;background:var(--surface,#fff);color:inherit;cursor:pointer;text-align:left;font:inherit}",
    ".acct-pick-trigger:hover{border-color:rgba(120,120,140,.6)}",
    ".acct-pick-trigger[aria-expanded='true']{border-color:#6366f1;box-shadow:0 0 0 2px rgba(99,102,241,.18)}",
    ".acct-pick-placeholder{color:#8b8b9a}",
    ".acct-pick-label{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}",
    ".acct-pick-avatar{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;overflow:hidden;flex:0 0 auto;background:#e5e7ef;color:#4b5563;font-size:11px;font-weight:700}",
    ".acct-pick-avatar img{width:100%;height:100%;object-fit:cover;display:block}",
    ".acct-pick-badge{flex:0 0 auto;font-size:10px;font-weight:800;letter-spacing:.04em;padding:2px 7px;border-radius:999px;color:#fff}",
    ".acct-pick-badge--fb{background:#1877f2}",
    ".acct-pick-badge--ig{background:linear-gradient(45deg,#f09433,#dc2743,#bc1888)}",
    ".acct-pick-panel{position:absolute;z-index:50;top:calc(100% + 6px);left:0;right:0;background:var(--surface,#fff);border:1px solid rgba(120,120,140,.3);border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.18);padding:8px;max-height:320px;display:flex;flex-direction:column}",
    ".acct-pick-search{width:100%;padding:8px 10px;border:1px solid rgba(120,120,140,.35);border-radius:8px;margin-bottom:6px;font:inherit;background:var(--surface,#fff);color:inherit}",
    ".acct-pick-list{overflow-y:auto;display:flex;flex-direction:column;gap:2px}",
    ".acct-pick-item{display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;border:0;border-radius:8px;background:transparent;cursor:pointer;text-align:left;font:inherit;color:inherit}",
    ".acct-pick-item:hover{background:rgba(99,102,241,.12)}",
    ".acct-pick-item.is-selected{background:rgba(99,102,241,.18)}",
    ".acct-pick-item-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".acct-pick-empty{padding:12px;text-align:center;color:#8b8b9a;font-size:13px}",
  ].join("");

  function injectStyles() {
    if (document.getElementById("acct-pick-styles")) return;
    var style = document.createElement("style");
    style.id = "acct-pick-styles";
    style.textContent = STYLES;
    document.head.appendChild(style);
  }

  function loadAccounts() {
    if (!accountsPromise) {
      accountsPromise = fetch(ACCOUNTS_URL, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (rows) { return Array.isArray(rows) ? rows : []; })
        .catch(function () { return []; });
    }
    return accountsPromise;
  }

  function platformBadge(platform) {
    return String(platform || "").toLowerCase() === "instagram" ? "IG" : "FB";
  }

  function cleanName(account) {
    var name = String(account.page_name || "").replace(/\s*\(IG\)\s*$/i, "").trim();
    return name || ("Account #" + account.id);
  }

  function initialsFor(name) {
    var parts = String(name || "?").trim().split(/\s+/).slice(0, 2);
    var out = parts.map(function (p) { return p.charAt(0); }).join("");
    return (out || "?").toUpperCase();
  }

  function makeAvatar(account, name) {
    var wrap = document.createElement("span");
    wrap.className = "acct-pick-avatar";
    var url = String(account.profile_picture_url || "").trim();
    if (url) {
      var img = document.createElement("img");
      img.src = url;
      img.alt = "";
      img.loading = "lazy";
      img.addEventListener("error", function () {
        wrap.textContent = initialsFor(name);
        wrap.classList.add("acct-pick-avatar--fallback");
      });
      wrap.appendChild(img);
    } else {
      wrap.textContent = initialsFor(name);
      wrap.classList.add("acct-pick-avatar--fallback");
    }
    return wrap;
  }

  function makeBadge(platform) {
    var badge = document.createElement("span");
    var p = String(platform || "").toLowerCase();
    badge.className = "acct-pick-badge acct-pick-badge--" + (p === "instagram" ? "ig" : "fb");
    badge.textContent = platformBadge(platform);
    return badge;
  }

  function mountPicker(container, accounts) {
    var targetId = container.getAttribute("data-target");
    var hidden = document.getElementById(targetId);
    if (!hidden) return;

    container.classList.add("account-picker");
    container.textContent = "";

    var byId = {};
    accounts.forEach(function (a) { byId[String(a.id)] = a; });

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "acct-pick-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");

    var panel = document.createElement("div");
    panel.className = "acct-pick-panel";
    panel.hidden = true;

    var search = document.createElement("input");
    search.type = "text";
    search.className = "acct-pick-search";
    search.placeholder = "Search page by name…";
    search.setAttribute("autocomplete", "off");

    var list = document.createElement("div");
    list.className = "acct-pick-list";
    list.setAttribute("role", "listbox");

    panel.appendChild(search);
    panel.appendChild(list);
    container.appendChild(trigger);
    container.appendChild(panel);

    function renderTrigger() {
      trigger.textContent = "";
      var current = byId[String(hidden.value || "")];
      if (!current) {
        var ph = document.createElement("span");
        ph.className = "acct-pick-placeholder";
        ph.textContent = accounts.length ? "Select an account…" : "No connected accounts";
        trigger.appendChild(ph);
        return;
      }
      var name = cleanName(current);
      trigger.appendChild(makeAvatar(current, name));
      var label = document.createElement("span");
      label.className = "acct-pick-label";
      label.textContent = name;
      trigger.appendChild(label);
      trigger.appendChild(makeBadge(current.platform));
    }

    function selectAccount(account) {
      hidden.value = account ? String(account.id) : "";
      renderTrigger();
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
      closePanel();
    }

    function renderList(filter) {
      list.textContent = "";
      var q = String(filter || "").trim().toLowerCase();
      var matches = accounts.filter(function (a) {
        if (!q) return true;
        return cleanName(a).toLowerCase().indexOf(q) !== -1 || String(a.id).indexOf(q) !== -1;
      });
      if (!matches.length) {
        var empty = document.createElement("div");
        empty.className = "acct-pick-empty";
        empty.textContent = "No matching accounts";
        list.appendChild(empty);
        return;
      }
      matches.forEach(function (account) {
        var name = cleanName(account);
        var item = document.createElement("button");
        item.type = "button";
        item.className = "acct-pick-item";
        item.setAttribute("role", "option");
        if (String(account.id) === String(hidden.value || "")) {
          item.classList.add("is-selected");
        }
        item.appendChild(makeAvatar(account, name));
        var text = document.createElement("span");
        text.className = "acct-pick-item-name";
        text.textContent = name;
        item.appendChild(text);
        item.appendChild(makeBadge(account.platform));
        item.addEventListener("click", function () { selectAccount(account); });
        list.appendChild(item);
      });
    }

    function openPanel() {
      if (!panel.hidden) return;
      panel.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      search.value = "";
      renderList("");
      window.setTimeout(function () { search.focus(); }, 0);
      document.addEventListener("click", onOutside, true);
    }

    function closePanel() {
      if (panel.hidden) return;
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("click", onOutside, true);
    }

    function onOutside(event) {
      if (!container.contains(event.target)) closePanel();
    }

    trigger.addEventListener("click", function () {
      if (panel.hidden) openPanel(); else closePanel();
    });
    search.addEventListener("input", function () { renderList(search.value); });
    search.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { closePanel(); trigger.focus(); return; }
      if (event.key === "Enter") {
        event.preventDefault();
        var first = list.querySelector(".acct-pick-item");
        if (first) first.click();
      }
    });

    // Initial selection: hidden value, else ?account_id= query param (scheduler prefill).
    if (!hidden.value) {
      try {
        var qp = new URLSearchParams(window.location.search).get("account_id");
        if (qp && byId[String(qp)]) hidden.value = String(qp);
      } catch (e) { /* no-op */ }
    }
    renderTrigger();
  }

  function init() {
    var containers = document.querySelectorAll("[data-account-picker]");
    if (!containers.length) return;
    injectStyles();
    loadAccounts().then(function (accounts) {
      containers.forEach(function (c) { mountPicker(c, accounts); });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
