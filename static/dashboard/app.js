(function () {
  let cachedAccountsRows = [];

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) {
      return parts.pop().split(";").shift();
    }
    return "";
  }

  const csrfToken = getCookie("csrftoken");

  async function fetchJSON(url, options = {}) {
    const requestOptions = { ...options };
    if (!requestOptions.method || String(requestOptions.method).toUpperCase() === "GET") {
      requestOptions.cache = requestOptions.cache || "no-store";
    }
    const response = await fetch(url, requestOptions);
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : await response.text();

    if (!response.ok) {
      if (typeof data === "string") {
        throw new Error(formatUiErrorMessage(data));
      }
      throw new Error(formatUiErrorMessage(data));
    }
    return data;
  }

  function formatUiErrorMessage(value) {
    if (value && typeof value === "object") {
      const details = value.details ? String(value.details) : "";
      const error = value.error ? String(value.error) : "";
      return sanitizeUiError(details || error || "Request failed.");
    }
    return sanitizeUiError(value);
  }

  function sanitizeUiError(value) {
    const text = String(value || "").trim();
    if (!text) return "Request failed.";
    const compact = text.replace(/\s+/g, " ").trim();
    const lowered = compact.toLowerCase();
    if (lowered.includes("<!doctype html") || lowered.includes("<html") || lowered.includes("</html>")) {
      if (lowered.includes("err_ngrok_3004")) {
        return "Public media URL is unavailable through ngrok right now. Restart ngrok and refresh again.";
      }
      return "Upstream service returned an unreadable HTML error page.";
    }
    if (lowered.includes("err_ngrok_3004")) {
      return "Public media URL is unavailable through ngrok right now. Restart ngrok and refresh again.";
    }
    return compact.length > 240 ? `${compact.slice(0, 237)}...` : compact;
  }

  function withButtonLoading(button, label, loadingLabel) {
    if (!button) return async (fn) => fn();
    const defaultLabel = label || button.textContent || "";
    return async (fn) => {
      button.disabled = true;
      button.textContent = loadingLabel;
      try {
        return await fn();
      } finally {
        button.disabled = false;
        button.textContent = defaultLabel;
      }
    };
  }

  function formatScheduleDateTime(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw;
    return parsed.toLocaleString("en-IN", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    });
  }

  function showAppToast(message, variant = "success") {
    const text = String(message || "").trim();
    if (!text) return;
    let root = document.getElementById("appToastRoot");
    if (!root) {
      root = document.createElement("div");
      root.id = "appToastRoot";
      document.body.appendChild(root);
    }
    const toast = document.createElement("div");
    toast.className = `app-toast ${variant === "error" ? "is-error" : "is-success"}`;
    toast.textContent = text;
    root.appendChild(toast);
    window.setTimeout(() => {
      toast.classList.add("is-exit");
      window.setTimeout(() => toast.remove(), 260);
    }, 3600);
  }

  function renderTable(container, rows) {
    if (!container) return;
    if (!rows.length) {
      container.innerHTML = "<p>No records found.</p>";
      return;
    }

    const headers = Object.keys(rows[0]);
    const head = `<tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr>`;
    const body = rows
      .map((row) => `<tr>${headers.map((h) => `<td>${row[h] ?? ""}</td>`).join("")}</tr>`)
      .join("");

    container.innerHTML = `<table>${head}${body}</table>`;
  }

  function platformBadge(platform) {
    const value = String(platform || "").toLowerCase();
    if (value === "fb_ig") {
      return (
        "<span class='platform-badge both'>" +
        "<img class='platform-logo' src='/static/dashboard/brand/meta-logo.jpg' alt='Meta logo'>" +
        "<img class='platform-logo' src='/static/dashboard/brand/instagram-logo.webp' alt='Instagram logo'>" +
        "<span>FB_IG</span>" +
        "</span>"
      );
    }
    if (value === "ig" || value === "instagram") {
      return (
        "<span class='platform-badge instagram'>" +
        "<img class='platform-logo' src='/static/dashboard/brand/instagram-logo.webp' alt='Instagram logo'>" +
        "<span>IG</span>" +
        "</span>"
      );
    }
    return (
      "<span class='platform-badge facebook'>" +
      "<img class='platform-logo' src='/static/dashboard/brand/meta-logo.jpg' alt='Meta logo'>" +
      "<span>FB</span>" +
      "</span>"
    );
  }

  function cleanProfileName(value) {
    const raw = String(value || "").trim();
    const cleaned = raw.replace(/\s*\([^)]*\)\s*$/g, "").trim();
    return cleaned || raw || "Account";
  }

  function mergeAccountRows(rows) {
    const safeRows = Array.isArray(rows) ? [...rows] : [];
    safeRows.sort((a, b) => Number(b.id || 0) - Number(a.id || 0));

    const igByPageId = new Map();
    safeRows.forEach((row) => {
      if (String(row.platform || "").toLowerCase() !== "instagram") return;
      const key = String(row.page_id || "");
      if (!key || igByPageId.has(key)) return;
      igByPageId.set(key, row);
    });

    const usedIgIds = new Set();
    const merged = [];

    safeRows.forEach((row) => {
      const platform = String(row.platform || "").toLowerCase();
      if (platform !== "facebook") return;

      const linkedIg = row.ig_user_id ? igByPageId.get(String(row.ig_user_id)) : null;
      if (linkedIg) usedIgIds.add(Number(linkedIg.id));

      const createdAt = linkedIg
        ? (new Date(linkedIg.created_at) < new Date(row.created_at) ? linkedIg.created_at : row.created_at)
        : row.created_at;
      const updatedAt = linkedIg
        ? (new Date(linkedIg.updated_at) > new Date(row.updated_at) ? linkedIg.updated_at : row.updated_at)
        : row.updated_at;
      const rowLastPostAt = row.last_post_at || null;
      const linkedLastPostAt = linkedIg?.last_post_at || null;
      const lastPostAt = [rowLastPostAt, linkedLastPostAt]
        .map((value) => ({ raw: value, parsed: value ? new Date(value) : null }))
        .filter((item) => item.parsed && !Number.isNaN(item.parsed.getTime()))
        .sort((a, b) => b.parsed.getTime() - a.parsed.getTime())[0]?.raw || null;
      const lastPostIsStale = lastPostAt ? isOlderThanHours(lastPostAt, 24) : true;
      const syncIsStale = Boolean(row.is_sync_stale) || Boolean(linkedIg?.is_sync_stale);
      const syncStateReason = linkedIg?.is_sync_stale ? linkedIg.sync_state_reason : row.sync_state_reason;
      const fbName = cleanProfileName(row.page_name);
      const igName = linkedIg ? cleanProfileName(linkedIg.page_name) : "";
      const displayName = linkedIg ? `${fbName} + ${igName}` : fbName;

      merged.push({
        profile_name: displayName,
        fb_page_name: fbName,
        ig_page_name: igName,
        account_id: Number(row.id),
        platform: linkedIg ? "fb_ig" : "fb",
        page_id: String(row.page_id || ""),
        ig_user_id: linkedIg ? String(linkedIg.page_id || "") : "",
        created_at: createdAt,
        updated_at: updatedAt,
        last_post_at: lastPostAt,
        last_post_is_stale: lastPostIsStale,
        is_sync_stale: syncIsStale,
        sync_state_reason: syncStateReason || "",
        fb_account_id: Number(row.id),
        ig_account_id: linkedIg ? Number(linkedIg.id) : null,
        insight_account_id: Number(row.id),
      });
    });

    safeRows.forEach((row) => {
      const platform = String(row.platform || "").toLowerCase();
      if (platform !== "instagram") return;
      if (usedIgIds.has(Number(row.id))) return;

      merged.push({
        profile_name: cleanProfileName(row.page_name),
        fb_page_name: "",
        ig_page_name: cleanProfileName(row.page_name),
        account_id: Number(row.id),
        platform: "ig",
        page_id: "",
        ig_user_id: String(row.page_id || ""),
        created_at: row.created_at,
        updated_at: row.updated_at,
        last_post_at: row.last_post_at || null,
        last_post_is_stale: row.last_post_at ? isOlderThanHours(row.last_post_at, 24) : true,
        is_sync_stale: Boolean(row.is_sync_stale),
        sync_state_reason: row.sync_state_reason || "",
        fb_account_id: null,
        ig_account_id: Number(row.id),
        insight_account_id: Number(row.id),
      });
    });

    function platformRank(platform) {
      const value = String(platform || "").toLowerCase();
      if (value === "fb_ig") return 0;
      return 1;
    }

    merged.sort((a, b) => {
      const rankDiff = platformRank(a.platform) - platformRank(b.platform);
      if (rankDiff !== 0) return rankDiff;
      return Number(b.account_id || 0) - Number(a.account_id || 0);
    });
    return merged;
  }

  function buildAvatarResolver(rows) {
    const facebookPageByIgId = new Map();
    (rows || []).forEach((row) => {
      const platform = String(row.platform || "").toLowerCase();
      if (platform !== "facebook") return;
      const fbPageId = row.page_id ? String(row.page_id) : "";
      const igUserId = row.ig_user_id ? String(row.ig_user_id) : "";
      if (fbPageId && igUserId) {
        facebookPageByIgId.set(igUserId, fbPageId);
      }
    });
    return (row) => {
      const platform = String(row.platform || "").toLowerCase();
      const pageId = row.page_id ? String(row.page_id) : "";
      const igUserId = row.ig_user_id ? String(row.ig_user_id) : "";
      if (platform === "instagram") {
        return facebookPageByIgId.get(pageId) || facebookPageByIgId.get(igUserId) || pageId;
      }
      return pageId;
    };
  }

  function avatarHtml(row, avatarResolver) {
    const pageId = avatarResolver ? avatarResolver(row) : row.page_id ? String(row.page_id) : "";
    const name = row.page_name || row.platform || "Account";
    const initials = escapeHtml(String(name).replace(/\s+/g, " ").trim().slice(0, 2).toUpperCase() || "NA");
    const explicitImage = row.profile_picture_url ? String(row.profile_picture_url) : "";
    if (!pageId) {
      return `
        <span class="profile-cell">
          <span class="avatar-wrap"><span class="avatar-fallback">${initials}</span></span>
          <span class="profile-name">${escapeHtml(name)}</span>
        </span>
      `;
    }
    const graphUrl = explicitImage || `https://graph.facebook.com/${encodeURIComponent(pageId)}/picture?type=normal`;
    return `
      <span class="profile-cell">
        <span class="avatar-wrap">
          <img class="avatar-img" src="${graphUrl}" alt="${escapeHtml(name)}" loading="lazy"
            onerror="this.style.display='none'; this.nextElementSibling.style.display='inline-grid';" />
          <span class="avatar-fallback" style="display:none;">${initials}</span>
        </span>
        <span class="profile-name">${escapeHtml(name)}</span>
      </span>
    `;
  }

  function applyAccountFilters(rows) {
    const platformFilter = document.getElementById("accountsPlatformFilter");
    const searchInput = document.getElementById("accountsSearchInput");
    const filterValue = String(platformFilter?.value || "all").toLowerCase();
    const query = String(searchInput?.value || "").trim().toLowerCase();
    return rows.filter((row) => {
      const platformOk = filterValue === "all" ? true : String(row.platform || "").toLowerCase() === filterValue;
      if (!platformOk) return false;
      if (!query) return true;
      const bag = [
        row.account_id,
        row.fb_account_id,
        row.ig_account_id,
        row.platform,
        row.profile_name,
        row.fb_page_name,
        row.ig_page_name,
        row.page_id,
        row.ig_user_id,
      ]
        .map((v) => String(v ?? "").toLowerCase())
        .join(" ");
      return bag.includes(query);
    });
  }

  function updateAccountsViewMeta(filteredRows, totalRows, rawTotalRows) {
    const meta = document.getElementById("accountsViewMeta");
    if (!meta) return;
    const fbOnly = filteredRows.filter((r) => String(r.platform).toLowerCase() === "fb").length;
    const igOnly = filteredRows.filter((r) => String(r.platform).toLowerCase() === "ig").length;
    const both = filteredRows.filter((r) => String(r.platform).toLowerCase() === "fb_ig").length;
    const rawText = Number.isFinite(Number(rawTotalRows)) ? ` | Active raw rows: ${rawTotalRows}` : "";
    meta.textContent = `Showing merged: ${filteredRows.length}/${totalRows} | FB only: ${fbOnly} | IG only: ${igOnly} | FB_IG: ${both}${rawText}`;
  }

  function renderAccountsTable(container, rows, totalRows, rawTotalRows) {
    if (!container) return;
    if (!rows.length) {
      container.innerHTML = "<p>No records found.</p>";
      updateAccountsViewMeta([], totalRows || 0, rawTotalRows || 0);
      return;
    }

    const head = `
      <tr>
        <th>S.N</th>
        <th>profile_name</th>
        <th>account_id</th>
        <th>platform</th>
        <th>page_id</th>
        <th>ig_user_id</th>
        <th>created_at</th>
        <th>last_post_at</th>
        <th>actions</th>
      </tr>
    `;

    const avatarResolver = buildAvatarResolver(cachedAccountsRows);
    const body = rows
      .map((row, index) => {
        const createdAt = row.created_at ? toIndianDateTime(row.created_at) : "-";
        const updatedAt = row.last_post_at ? toIndianDateTime(row.last_post_at) : "No post found";
        const updatedAtClass = row.last_post_is_stale ? "account-post-time stale" : "account-post-time";
        const schedulePlatform = row.platform === "fb_ig" ? "both" : row.platform === "ig" ? "instagram" : "facebook";
        const scheduleAccountId = row.platform === "ig" ? row.ig_account_id || row.account_id : row.fb_account_id || row.account_id;
        const schedulerUrl = `/dashboard/scheduler/?account_id=${encodeURIComponent(scheduleAccountId)}&platform=${encodeURIComponent(
          schedulePlatform
        )}`;
        const insightsUrl = `/dashboard/insights/?account_id=${encodeURIComponent(row.insight_account_id || row.account_id)}`;
        const aiInsightsUrl = `/dashboard/ai-insights/?account_id=${encodeURIComponent(
          row.insight_account_id || row.account_id
        )}`;
        const scheduleAction = row.is_sync_stale
          ? `<span class="inline-link-btn account-action-btn account-action-primary disabled" title="${escapeHtml(row.sync_state_reason || "Reconnect this profile before scheduling.")}">Reconnect</span>`
          : `<a class="inline-link-btn account-action-btn account-action-primary" href="${schedulerUrl}">Schedule</a>`;
        const syncStateHtml = row.is_sync_stale
          ? `<div class="account-sync-state stale" title="${escapeHtml(row.sync_state_reason || "")}">Stale sync</div>`
          : "";
        return `
          <tr>
            <td>${index + 1}</td>
            <td>${avatarHtml({ ...row, page_name: row.profile_name }, avatarResolver)}</td>
            <td>${escapeHtml(row.account_id)}</td>
            <td>${platformBadge(row.platform)}</td>
            <td>${escapeHtml(row.page_id)}</td>
            <td>${escapeHtml(row.ig_user_id || "")}</td>
            <td>${escapeHtml(createdAt)}</td>
            <td><span class="${updatedAtClass}">${escapeHtml(updatedAt)}</span>${syncStateHtml}</td>
            <td>
              <div class="account-actions-stack">
                ${scheduleAction}
                <div class="account-actions-row">
                  <a class="inline-link-btn account-action-btn account-action-secondary" href="${insightsUrl}">Insights</a>
                  <a class="inline-link-btn account-action-btn account-action-ai" href="${aiInsightsUrl}">AI Insights</a>
                </div>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `<table>${head}${body}</table>`;
    updateAccountsViewMeta(rows, totalRows || rows.length, rawTotalRows || rows.length);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderScheduledTable(container, rows) {
    if (!container) return;
    if (!rows.length) {
      container.innerHTML = "<p>No records found.</p>";
      return;
    }

    const head = `
      <tr>
        <th>id</th>
        <th>platform</th>
        <th>message</th>
        <th>media_url</th>
        <th>scheduled_for</th>
        <th>due_in</th>
        <th>status</th>
        <th>error_message</th>
        <th>page_name</th>
        <th>actions</th>
      </tr>
    `;

    const body = rows
      .map((row) => {
        const canRetry = row.status === "failed";
        return `
          <tr>
            <td>${escapeHtml(row.id)}</td>
            <td>${escapeHtml(row.platform)}</td>
            <td>${escapeHtml(row.message)}</td>
            <td>${escapeHtml(row.media_url)}</td>
            <td>${escapeHtml(row.scheduled_for)}</td>
            <td>${escapeHtml(row.due_in)}</td>
            <td>${escapeHtml(row.status)}</td>
            <td>${escapeHtml(row.error_message)}</td>
            <td>${escapeHtml(row.page_name)}</td>
            <td>${canRetry ? `<button class="btn retry-failed-btn" data-post-id="${row.id}">Retry Failed</button>` : "-"}</td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `<table>${head}${body}</table>`;
  }

  function guessCatalogPlatform(row, connectedRows) {
    const pageId = String(row.page_id || "");
    const matched = (connectedRows || []).find((item) => String(item.page_id || "") === pageId);
    if (matched && matched.platform) return String(matched.platform).toLowerCase();
    if (pageId.startsWith("1784")) return "instagram";
    return "facebook";
  }

  function buildCatalogLinkMaps(connectedRows) {
    const fbToIg = new Map();
    const igToFb = new Map();
    (connectedRows || []).forEach((row) => {
      const platform = String(row.platform || "").toLowerCase();
      if (platform !== "facebook") return;
      const fbPageId = String(row.page_id || "");
      const igUserId = String(row.ig_user_id || "");
      if (!fbPageId || !igUserId) return;
      fbToIg.set(fbPageId, igUserId);
      igToFb.set(igUserId, fbPageId);
    });
    return { fbToIg, igToFb };
  }

  function catalogAvailability(row, connectedRows, platformOverride) {
    const connectability = String(row.connectability || "").toLowerCase();
    const status = String(row.status || "").toLowerCase();
    const pageTokenStatus = String(row.page_token_status || row.connection_status || "").toLowerCase();
    const platform = platformOverride || guessCatalogPlatform(row, connectedRows);
    const available =
      status === "connected" ||
      connectability === "connected" ||
      (connectability === "connectable" && (pageTokenStatus === "connected" || pageTokenStatus === "synced"));
    return {
      platform,
      available,
      reason:
        row.reason ||
        "Meta did not return page access token. Connect this page in Business Integrations and reconnect.",
      status,
      connectability,
    };
  }

  function mergeCatalogRows(rows, connectedRows) {
    const safeRows = Array.isArray(rows) ? [...rows] : [];
    const { fbToIg, igToFb } = buildCatalogLinkMaps(connectedRows);
    const igById = new Map();
    const fbRows = [];
    const otherRows = [];

    safeRows.forEach((row) => {
      const platform = String(row.platform || "").toLowerCase() || guessCatalogPlatform(row, connectedRows);
      if (platform === "facebook") {
        fbRows.push(row);
        return;
      }
      if (platform === "instagram") {
        igById.set(String(row.page_id || ""), row);
        return;
      }
      otherRows.push(row);
    });

    const usedIgIds = new Set();
    const merged = [];

    fbRows.forEach((fbRow) => {
      const fbPageId = String(fbRow.page_id || "");
      const linkedIgId = String(fbRow.ig_user_id || fbToIg.get(fbPageId) || "");
      const igRow = linkedIgId ? igById.get(linkedIgId) : null;
      if (!igRow) {
        const fbInfo = catalogAvailability(fbRow, connectedRows, "facebook");
        merged.push({
          profile_name: cleanProfileName(fbRow.page_name),
          platform: "facebook",
          page_id: fbPageId,
          ig_user_id: linkedIgId,
          status: fbRow.status || "-",
          connectability: fbRow.connectability || "-",
          reason: fbInfo.reason,
          insights_available: fbInfo.available,
          profile_picture_url: fbRow.profile_picture_url || null,
        });
        return;
      }

      usedIgIds.add(String(igRow.page_id || ""));
      const fbInfo = catalogAvailability(fbRow, connectedRows, "facebook");
      const igInfo = catalogAvailability(igRow, connectedRows, "instagram");
      const reason = [fbInfo.reason, igInfo.reason].filter(Boolean).filter((v, i, arr) => arr.indexOf(v) === i).join(" | ");
      const connectability = [fbInfo.connectability, igInfo.connectability].includes("not_connectable")
        ? "not_connectable"
        : [fbInfo.connectability, igInfo.connectability].includes("connectable")
        ? "connectable"
        : "connected";
      const status = [String(fbRow.status || "").toLowerCase(), String(igRow.status || "").toLowerCase()].every(
        (value) => value === "connected"
      )
        ? "connected"
        : "mixed";

      merged.push({
        profile_name: `${cleanProfileName(fbRow.page_name)} + ${cleanProfileName(igRow.page_name)}`,
        platform: "fb_ig",
        page_id: fbPageId,
        ig_user_id: String(igRow.page_id || linkedIgId || ""),
        status,
        connectability,
        reason,
        insights_available: fbInfo.available && igInfo.available,
        profile_picture_url: fbRow.profile_picture_url || igRow.profile_picture_url || null,
      });
    });

    igById.forEach((igRow, igId) => {
      if (usedIgIds.has(igId)) return;
      const igInfo = catalogAvailability(igRow, connectedRows, "instagram");
      merged.push({
        profile_name: cleanProfileName(igRow.page_name),
        platform: "instagram",
        page_id: String(igToFb.get(igId) || ""),
        ig_user_id: igId,
        status: igRow.status || "-",
        connectability: igRow.connectability || "-",
        reason: igInfo.reason,
        insights_available: igInfo.available,
        profile_picture_url: igRow.profile_picture_url || null,
      });
    });

    otherRows.forEach((row) => {
      const info = catalogAvailability(row, connectedRows);
      merged.push({
        profile_name: cleanProfileName(row.page_name),
        platform: info.platform,
        page_id: String(row.page_id || ""),
        ig_user_id: String(row.ig_user_id || ""),
        status: row.status || "-",
        connectability: row.connectability || "-",
        reason: info.reason,
        insights_available: info.available,
        profile_picture_url: row.profile_picture_url || null,
      });
    });

    merged.sort((a, b) => String(a.profile_name || "").localeCompare(String(b.profile_name || "")));
    return merged;
  }

  function renderCatalogTable(container, rows, connectedRows) {
    if (!container) return;
    const mergedRows = mergeCatalogRows(rows, connectedRows);
    if (!mergedRows.length) {
      container.innerHTML = "<p>No records found.</p>";
      return;
    }

    const avatarResolver = buildAvatarResolver(connectedRows || []);
    const head = `
      <tr>
        <th>#</th>
        <th>profile</th>
        <th>platform</th>
        <th>fb_page_id</th>
        <th>ig_user_id</th>
        <th>status</th>
        <th>connectability</th>
        <th>reason</th>
        <th>insights</th>
      </tr>
    `;
    const body = mergedRows
      .map((row, index) => {
        const reason = row.reason || "Meta did not return page access token. Connect and reconnect.";
        const badge = row.insights_available
          ? "<span class='status-badge ok'>Available</span>"
          : `<span class='status-badge warn' title='${escapeHtml(reason)}'>Unavailable</span>`;
        const platform = String(row.platform || "").toLowerCase();

        return `
          <tr>
            <td>${index + 1}</td>
            <td>${avatarHtml(
              { page_id: row.page_id, ig_user_id: row.ig_user_id, page_name: row.profile_name || "(name unavailable)", platform },
              avatarResolver
            )}</td>
            <td>${platformBadge(platform)}</td>
            <td>${escapeHtml(row.page_id)}</td>
            <td>${escapeHtml(row.ig_user_id || "")}</td>
            <td>${escapeHtml(row.status || "-")}</td>
            <td>${escapeHtml(row.connectability || "-")}</td>
            <td>${escapeHtml(reason)}</td>
            <td>${badge}</td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `<table>${head}${body}</table>`;
  }

  function renderAccountsFromCache() {
    const table = document.getElementById("accountsTable");
    if (!table) return;
    const mergedRows = mergeAccountRows(cachedAccountsRows);
    const filtered = applyAccountFilters(mergedRows);
    renderAccountsTable(table, filtered, mergedRows.length, cachedAccountsRows.length);
  }

  async function loadAccounts(options = {}) {
    const table = document.getElementById("accountsTable");
    const syncStatus = document.getElementById("accountSyncStatus");
    const catalogTable = document.getElementById("metaCatalogTable");
    const catalogStatus = document.getElementById("metaCatalogStatus");
    const refreshCatalog = options.refreshCatalog === true;
    const refreshAccounts = options.refreshAccounts === true;
    if (!table) return;
    let rows = [];
    try {
      // Primary table should load fast and independently.
      const accountsEndpoint = refreshAccounts ? "/api/accounts/?refresh=1" : "/api/accounts/";
      rows = await fetchJSON(accountsEndpoint);
      cachedAccountsRows = rows;
      renderAccountsFromCache();
    } catch (err) {
      table.innerHTML = `<p>${err.message}</p>`;
      cachedAccountsRows = [];
      rows = [];
    }

    // Non-critical panels load in background; failures should not blank main table.
    const catalogEndpoint = refreshCatalog ? "/api/accounts/meta-pages/?refresh=1" : "/api/accounts/meta-pages/";
    const [statusResult, catalogResult] = await Promise.allSettled([
      fetchJSON("/api/accounts/sync-status/"),
      fetchJSON(catalogEndpoint),
    ]);

    if (statusResult.status === "fulfilled" && syncStatus) {
      const status = statusResult.value;
      const syncedAt = status.synced_at ? toIndianDateTime(status.synced_at) : "Not synced yet";
      const metaPages = status.meta_pages_synced ?? rows.filter((r) => r.platform === "facebook").length;
      let targetIds = status.token_target_ids_count ?? null;
      if (!targetIds && catalogResult.status === "fulfilled") {
        targetIds = catalogResult.value.total_pages ?? null;
      }
      targetIds = targetIds ?? "Not available";
      const fbTotal = status.facebook_connected_total ?? rows.filter((r) => r.platform === "facebook").length;
      const igTotal = status.instagram_connected_total ?? rows.filter((r) => r.platform === "instagram").length;
      const linkedIgFromFb = rows.filter(
        (r) => String(r.platform || "").toLowerCase() === "facebook" && String(r.ig_user_id || "").trim()
      ).length;
      const warning = status.warning ? ` | Warning: ${status.warning}` : "";
      syncStatus.textContent = `Last Sync: ${syncedAt} | Meta Pages Synced: ${metaPages} | Token Target IDs: ${targetIds} | Connected FB: ${fbTotal} | Connected IG: ${igTotal} | FB linked to IG: ${linkedIgFromFb}${warning}`;
    } else if (syncStatus) {
      syncStatus.textContent = "Sync status unavailable right now.";
    }

    if (catalogResult.status === "fulfilled") {
      const catalog = catalogResult.value;
      const mergedCatalogRows = mergeCatalogRows(catalog.rows || [], rows);
      if (catalogTable) {
        renderCatalogTable(catalogTable, catalog.rows || [], rows);
      }
      if (catalogStatus) {
        const connected = catalog.connected_pages ?? rows.filter((r) => r.platform === "facebook").length;
        const total = catalog.total_pages ?? connected;
        const connectable = (catalog.rows || []).filter((r) => r.connectability === "connectable").length;
        const notConnectable = (catalog.rows || []).filter((r) => r.connectability === "not_connectable").length;
        catalogStatus.textContent = `Catalog Total: ${total} | Merged view rows: ${mergedCatalogRows.length} | Connected in App: ${connected} | Catalog-only: ${Math.max(
          0,
          total - connected
        )} | Connectable: ${connectable} | Not Connectable: ${notConnectable}`;
      }
    } else {
      if (catalogTable) catalogTable.innerHTML = "<p>Catalog unavailable right now.</p>";
      if (catalogStatus) catalogStatus.textContent = "";
    }
  }

  async function loadScheduledPosts() {
    const table = document.getElementById("scheduledTable");
    if (!table) return;
    try {
      const rows = await fetchJSON("/api/posts/scheduled/");
      const now = new Date();

      function formatDueIn(utcValue, status) {
        if (!utcValue) return "-";
        const target = new Date(utcValue);
        if (Number.isNaN(target.getTime())) return "-";

        const diffMs = target.getTime() - now.getTime();
        if (status !== "pending") return "-";
        if (diffMs <= 0) return "Due now";

        const totalMinutes = Math.ceil(diffMs / 60000);
        const hours = Math.floor(totalMinutes / 60);
        const minutes = totalMinutes % 60;
        if (hours <= 0) return `${minutes} min`;
        return `${hours}h ${minutes}m`;
      }

      const rowsWithLocalTime = rows.map((row) => {
        const utcValue = row.scheduled_for;
        const localValue = utcValue
          ? new Date(utcValue).toLocaleString("en-IN", {
              timeZone: "Asia/Kolkata",
              day: "2-digit",
              month: "2-digit",
              year: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
              hour12: true,
            })
          : "";
        return {
          ...row,
          scheduled_for: localValue,
          scheduled_for_utc: utcValue,
          due_in: formatDueIn(utcValue, row.status),
        };
      });
      renderScheduledTable(table, rowsWithLocalTime);
    } catch (err) {
      table.innerHTML = `<p>${err.message}</p>`;
    }
  }

  async function retryFailedPost(postId) {
    await fetchJSON(`/api/posts/${postId}/retry/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({}),
    });
    await loadScheduledPosts();
  }

  const connectBtn = document.getElementById("connectMetaBtn");
  if (connectBtn) {
    const runWithConnectLoading = withButtonLoading(connectBtn, "Connect Facebook + Instagram", "Connecting...");
    connectBtn.addEventListener("click", async () => {
      await runWithConnectLoading(async () => {
        const data = await fetchJSON("/auth/meta/start");
        window.location.href = data.auth_url;
      });
    });
  }

  const refreshAccountsBtn = document.getElementById("refreshAccountsBtn");
  const forceRefreshAllBtn = document.getElementById("forceRefreshAllBtn");
  const accountsBulkRefreshStatus = document.getElementById("accountsBulkRefreshStatus");
  let forceRefreshCooldownTimer = null;
  const forceRefreshLabel = "Force Refresh All Profiles";
  if (refreshAccountsBtn) {
    const runWithRefreshAccountsLoading = withButtonLoading(refreshAccountsBtn, "Refresh List", "Refreshing...");
    refreshAccountsBtn.addEventListener("click", () =>
      runWithRefreshAccountsLoading(() => loadAccounts({ refreshCatalog: true, refreshAccounts: true }))
    );
    loadAccounts();
  }
  if (forceRefreshAllBtn) {
    const startForceRefreshCooldown = (seconds) => {
      const safeSeconds = Number(seconds) > 0 ? Number(seconds) : 90;
      if (forceRefreshCooldownTimer) {
        window.clearInterval(forceRefreshCooldownTimer);
        forceRefreshCooldownTimer = null;
      }
      let remaining = safeSeconds;
      forceRefreshAllBtn.disabled = true;
      forceRefreshAllBtn.textContent = `Wait ${remaining}s`;
      forceRefreshCooldownTimer = window.setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
          window.clearInterval(forceRefreshCooldownTimer);
          forceRefreshCooldownTimer = null;
          forceRefreshAllBtn.disabled = false;
          forceRefreshAllBtn.textContent = forceRefreshLabel;
          return;
        }
        forceRefreshAllBtn.textContent = `Wait ${remaining}s`;
      }, 1000);
    };

    forceRefreshAllBtn.addEventListener("click", async () => {
      if (forceRefreshAllBtn.disabled) return;
      forceRefreshAllBtn.disabled = true;
      forceRefreshAllBtn.textContent = "Queuing Force Refresh...";
      try {
        const response = await fetch("/api/insights/force-refresh-all/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          body: JSON.stringify({}),
        });
        const contentType = response.headers.get("content-type") || "";
        const data = contentType.includes("application/json") ? await response.json() : {};

        if (response.ok) {
          const queuedAt = toIndianDateTime(data.queued_at) || "-";
          const message = `${data.message || "Force refresh request queued."} Queued at: ${queuedAt}`;
          if (accountsBulkRefreshStatus) accountsBulkRefreshStatus.textContent = message;
          showAppToast(message, "success");
          startForceRefreshCooldown(data.cooldown_seconds || 90);
          return;
        }

        const retrySeconds = Number(data.retry_after_seconds || 0);
        const errorMessage = sanitizeUiError((data && (data.details || data.error)) || "Force refresh request failed.");
        if (accountsBulkRefreshStatus) accountsBulkRefreshStatus.textContent = errorMessage;
        showAppToast(errorMessage, "error");
        if (response.status === 429) {
          startForceRefreshCooldown(retrySeconds || 90);
          return;
        }
      } catch (err) {
        const message = sanitizeUiError(err && err.message ? err.message : "Force refresh request failed.");
        if (accountsBulkRefreshStatus) accountsBulkRefreshStatus.textContent = message;
        showAppToast(message, "error");
      }

      forceRefreshAllBtn.disabled = false;
      forceRefreshAllBtn.textContent = forceRefreshLabel;
    });
  }
  const accountsPlatformFilter = document.getElementById("accountsPlatformFilter");
  const accountsSearchInput = document.getElementById("accountsSearchInput");
  if (accountsPlatformFilter) {
    accountsPlatformFilter.addEventListener("change", renderAccountsFromCache);
  }
  if (accountsSearchInput) {
    accountsSearchInput.addEventListener("input", renderAccountsFromCache);
  }
  const checkConnectabilityBtn = document.getElementById("checkConnectabilityBtn");
  if (checkConnectabilityBtn) {
    const runWithConnectabilityLoading = withButtonLoading(
      checkConnectabilityBtn,
      "Check Connectability",
      "Checking..."
    );
    checkConnectabilityBtn.addEventListener("click", () =>
      runWithConnectabilityLoading(() => loadAccounts({ refreshCatalog: true }))
    );
  }

  const refreshScheduledBtn = document.getElementById("refreshScheduledBtn");
  if (refreshScheduledBtn) {
    const runWithRefreshScheduleLoading = withButtonLoading(refreshScheduledBtn, "Refresh", "Refreshing...");
    refreshScheduledBtn.addEventListener("click", () => runWithRefreshScheduleLoading(loadScheduledPosts));
    loadScheduledPosts();
  }
  const scheduledTable = document.getElementById("scheduledTable");
  if (scheduledTable) {
    scheduledTable.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.classList.contains("retry-failed-btn")) {
        return;
      }
      const postId = Number(target.dataset.postId);
      if (!postId) return;
      const ok = window.confirm(`Retry failed post #${postId} now?`);
      if (!ok) return;
      try {
        await retryFailedPost(postId);
      } catch (err) {
        window.alert(`Retry failed: ${err.message}`);
      }
    });
  }

  const scheduleForm = document.getElementById("scheduleForm");
  if (scheduleForm) {
    const scheduleParams = new URLSearchParams(window.location.search);
    const prefillAccountId = scheduleParams.get("account_id");
    const prefillPlatform = scheduleParams.get("platform");
    const accountIdInput = scheduleForm.querySelector("[name='account_id']");
    const platformInput = scheduleForm.querySelector("[name='platform']");
    if (accountIdInput && prefillAccountId) accountIdInput.value = prefillAccountId;
    if (platformInput && prefillPlatform) platformInput.value = prefillPlatform;

    const scheduleSubmitBtn = scheduleForm.querySelector("button[type='submit']");
    const runWithScheduleLoading = withButtonLoading(scheduleSubmitBtn, "Schedule Post", "Scheduling...");
    scheduleForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(scheduleForm);
      const payload = new FormData();
      payload.append("account_id", String(Number(formData.get("account_id"))));
      payload.append("platform", String(formData.get("platform") || ""));
      payload.append("scheduled_for", new Date(formData.get("scheduled_for")).toISOString());

      const message = formData.get("message");
      const mediaUrl = formData.get("media_url");
      const mediaFile = formData.get("media_file");
      if (message) payload.append("message", String(message));
      if (mediaUrl) payload.append("media_url", String(mediaUrl));
      if (mediaFile instanceof File && mediaFile.size > 0) payload.append("media_file", mediaFile);

      const resultEl = document.getElementById("scheduleResult");
      try {
        const data = await runWithScheduleLoading(() =>
          fetchJSON("/api/posts/schedule/", {
            method: "POST",
            headers: {
              "X-CSRFToken": csrfToken,
            },
            body: payload,
          })
        );
        if (Array.isArray(data.posts) && data.posts.length) {
          const summary = data.posts.map((p) => `#${p.id} (${p.platform})`).join(", ");
          resultEl.textContent = `Scheduled: ${summary}`;
        } else {
          resultEl.textContent = `Scheduled: #${data.id}`;
        }
        const scheduledFor = formatScheduleDateTime(formData.get("scheduled_for"));
        if (scheduledFor) {
          showAppToast(`Your post is scheduled for ${scheduledFor}.`, "success");
        } else {
          showAppToast("Your post is scheduled successfully.", "success");
        }
        await loadScheduledPosts();
      } catch (err) {
        resultEl.textContent = `Error: ${err.message}`;
        showAppToast(`Scheduling failed: ${err.message}`, "error");
      }
    });
  }

  const metaAppConfigForm = document.getElementById("metaAppConfigForm");
  const saveMetaAppConfigBtn = document.getElementById("saveMetaAppConfigBtn");
  const metaAppConfigResult = document.getElementById("metaAppConfigResult");
  const metaAppSecretState = document.getElementById("metaAppSecretState");

  if (metaAppConfigForm && saveMetaAppConfigBtn) {
    const runWithMetaConfigLoading = withButtonLoading(
      saveMetaAppConfigBtn,
      "Save Meta Configuration",
      "Saving..."
    );

    metaAppConfigForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(metaAppConfigForm);
      const payload = {
        meta_app_id: String(formData.get("meta_app_id") || "").trim(),
        meta_app_secret: String(formData.get("meta_app_secret") || "").trim(),
        meta_redirect_uri: String(formData.get("meta_redirect_uri") || "").trim(),
      };

      if (metaAppConfigResult) {
        metaAppConfigResult.textContent = "";
        metaAppConfigResult.classList.remove("is-success", "is-error");
      }

      try {
        const data = await runWithMetaConfigLoading(() =>
          fetchJSON("/dashboard/meta-app-config/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify(payload),
          })
        );

        const warningText = data.warning ? ` ${String(data.warning)}` : "";
        if (metaAppConfigResult) {
          metaAppConfigResult.textContent = `${String(data.message || "Meta app configuration saved.")}${warningText}`;
          metaAppConfigResult.classList.add("is-success");
        }
        if (metaAppSecretState) {
          if (data.meta_app_secret_configured && data.meta_app_secret_masked) {
            metaAppSecretState.textContent = `Current secret: ${data.meta_app_secret_masked}`;
          } else {
            metaAppSecretState.textContent = "No secret configured yet.";
          }
        }
        const secretInput = metaAppConfigForm.querySelector("#metaAppSecretInput");
        if (secretInput instanceof HTMLInputElement) {
          secretInput.value = "";
        }
      } catch (err) {
        if (metaAppConfigResult) {
          metaAppConfigResult.textContent = `Error: ${err.message}`;
          metaAppConfigResult.classList.add("is-error");
        }
      }
    });
  }

  const fetchInsightsBtn = document.getElementById("fetchInsightsBtn");
  const refreshInsightsBtn = document.getElementById("refreshInsightsBtn");
  const insightAccountId = document.getElementById("insightAccountId");
  const insightError = document.getElementById("insightError");
  const insightWarning = document.getElementById("insightWarning");
  const publicUrlStatus = document.getElementById("publicUrlStatus");
  const totalFollowers = document.getElementById("totalFollowers");
  const totalFollowing = document.getElementById("totalFollowing");
  const totalPostShare = document.getElementById("totalPostShare");
  const insightMeta = document.getElementById("insightMeta");
  const insightPageHero = document.getElementById("insightPageHero");
  const insightPageName = document.getElementById("insightPageName");
  const insightComparisonTitle = document.getElementById("insightComparisonTitle");
  const insightPostsTable = document.getElementById("insightPostsTable");
  const insightMetricsTable = document.getElementById("insightMetricsTable");
  const aiInsightAccountId = document.getElementById("aiInsightAccountId");
  const aiInsightGoal = document.getElementById("aiInsightGoal");
  const aiInsightForceRefresh = document.getElementById("aiInsightForceRefresh");
  const runAiInsightsBtn = document.getElementById("runAiInsightsBtn");
  const aiInsightError = document.getElementById("aiInsightError");
  const aiInsightMeta = document.getElementById("aiInsightMeta");
  const aiInsightResult = document.getElementById("aiInsightResult");
  const tokenHealthNav = document.getElementById("tokenHealthNav");
  const tokenHealthButton = document.getElementById("tokenHealthButton");
  const tokenHealthDot = document.getElementById("tokenHealthDot");
  const tokenHealthInfoBtn = document.getElementById("tokenHealthInfoBtn");
  const tokenHealthPopover = document.getElementById("tokenHealthPopover");
  const tokenHealthSummary = document.getElementById("tokenHealthSummary");
  const tokenHealthReason = document.getElementById("tokenHealthReason");
  const tokenHealthMeta = document.getElementById("tokenHealthMeta");
  const tokenHealthSteps = document.getElementById("tokenHealthSteps");

  function toIndianDateTime(value) {
    if (!value) return "";
    return new Date(value).toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    });
  }

  function setHealthPopoverOpen(open) {
    if (!tokenHealthPopover || !tokenHealthButton || !tokenHealthInfoBtn) return;
    tokenHealthPopover.hidden = !open;
    tokenHealthButton.setAttribute("aria-expanded", open ? "true" : "false");
    tokenHealthInfoBtn.setAttribute("aria-expanded", open ? "true" : "false");
    tokenHealthButton.classList.toggle("is-open", open);
    tokenHealthInfoBtn.classList.toggle("is-open", open);
  }

  function renderTokenHealth(data) {
    if (!tokenHealthNav || !tokenHealthDot || !tokenHealthSummary || !tokenHealthReason || !tokenHealthMeta || !tokenHealthSteps) {
      return;
    }
    tokenHealthNav.hidden = false;
    tokenHealthDot.classList.remove("is-ok", "is-bad");
    tokenHealthDot.classList.add(data && data.ok ? "is-ok" : "is-bad");

    tokenHealthSummary.textContent = data && data.summary ? String(data.summary) : "Meta token health unavailable.";
    tokenHealthReason.textContent = data && data.reason ? String(data.reason) : "Unable to validate current token state.";

    const checkedAccounts = data && data.checked_accounts !== undefined ? data.checked_accounts : "-";
    const checkedTokens = data && data.checked_tokens !== undefined ? data.checked_tokens : "-";
    const cacheText = data && data.cached ? "Yes" : "No";
    tokenHealthMeta.textContent = `Accounts checked: ${checkedAccounts} | Unique tokens: ${checkedTokens} | Cached: ${cacheText}`;

    const steps = Array.isArray(data && data.next_steps) ? [...data.next_steps] : [];
    const invalidAccounts = Array.isArray(data && data.invalid_accounts) ? data.invalid_accounts : [];
    if (invalidAccounts.length) {
      const preview = invalidAccounts
        .map((row) => `${row.page_name} (${row.platform})`)
        .slice(0, 3)
        .join(", ");
      steps.unshift(`Affected accounts: ${preview}`);
    }

    tokenHealthSteps.innerHTML = steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("");
  }

  async function loadTokenHealth() {
    if (!tokenHealthNav) return;
    try {
      const data = await fetchJSON("/dashboard/token-health-status/");
      renderTokenHealth(data);
    } catch (err) {
      renderTokenHealth({
        ok: false,
        summary: "Meta token health unavailable.",
        reason: err.message,
        checked_accounts: "-",
        checked_tokens: "-",
        cached: false,
        next_steps: ["Refresh the page once.", "If this keeps failing, reconnect the affected accounts from Accounts."],
      });
    }
  }

  function isOlderThanHours(value, hours) {
    if (!value) return true;
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return true;
    return Date.now() - dt.getTime() > hours * 60 * 60 * 1000;
  }

  function parseSortDate(value) {
    if (!value) return null;
    const dt = new Date(value);
    return Number.isNaN(dt.getTime()) ? null : dt;
  }

  function sortPublishedPosts(rows) {
    const safeRows = Array.isArray(rows) ? [...rows] : [];
    safeRows.sort((a, b) => {
      const aDate = parseSortDate(a.published_at_utc || a.published_at || a.scheduled_for_utc || a.scheduled_for);
      const bDate = parseSortDate(b.published_at_utc || b.published_at || b.scheduled_for_utc || b.scheduled_for);
      const aTime = aDate ? aDate.getTime() : 0;
      const bTime = bDate ? bDate.getTime() : 0;
      return bTime - aTime;
    });
    return safeRows;
  }

  function setInsightValue(element, value) {
    if (!element) return;
    element.textContent = value === null || value === undefined || value === "" ? "N/A" : String(value);
  }

  function numericMetricValue(metric) {
    if (!metric) return null;
    if (metric.total_value && typeof metric.total_value === "object" && metric.total_value.value !== undefined) {
      return Number(metric.total_value.value);
    }
    const values = Array.isArray(metric.values) ? metric.values : [];
    if (!values.length) return null;
    const numericValues = values
      .map((item) => (item && item.value !== undefined ? Number(item.value) : NaN))
      .filter((value) => Number.isFinite(value));
    if (!numericValues.length) return null;
    if (String(metric.period || "").toLowerCase() === "day") {
      return numericValues.slice(-7).reduce((sum, value) => sum + value, 0);
    }
    return numericValues[numericValues.length - 1];
  }

  function metricFromInsights(insights, names) {
    const rows = Array.isArray(insights) ? insights : [];
    for (const name of names) {
      const match = rows.find((metric) => metric && metric.name === name);
      if (!match) continue;
      const value = numericMetricValue(match);
      if (value !== null) return value;
    }
    return null;
  }

  function aggregateRecentPostMetric(posts, platform, fieldName) {
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const rows = Array.isArray(posts) ? posts : [];
    let total = 0;
    let found = false;

    rows.forEach((row) => {
      if (String(row.platform || "").toLowerCase() !== platform) return;
      const dt = parseSortDate(row.published_at_utc || row.published_at);
      if (!dt || dt.getTime() < cutoff) return;
      const value = Number(row[fieldName]);
      if (!Number.isFinite(value)) return;
      total += value;
      found = true;
    });

    return found ? total : null;
  }

  function comparisonMetricRows(data) {
    const accounts = Array.isArray(data.accounts) ? data.accounts : [data];
    const fb = accounts.find((row) => row.platform === "facebook") || {};
    const ig = accounts.find((row) => row.platform === "instagram") || {};
    const fbSummary = fb.summary || {};
    const igSummary = ig.summary || {};
    const fbInsights = fb.insights || [];
    const igInsights = ig.insights || [];
    const publishedPosts = Array.isArray(data.published_posts) ? data.published_posts : [];
    const fbRecentViews = aggregateRecentPostMetric(publishedPosts, "facebook", "total_views");
    const fbRecentLikes = aggregateRecentPostMetric(publishedPosts, "facebook", "total_likes");
    const fbRecentComments = aggregateRecentPostMetric(publishedPosts, "facebook", "total_comments");
    const fbRecentShares = aggregateRecentPostMetric(publishedPosts, "facebook", "total_shares");
    const igRecentShares = aggregateRecentPostMetric(publishedPosts, "instagram", "total_shares");
    const igRecentSaves = aggregateRecentPostMetric(publishedPosts, "instagram", "total_saves");

    return [
      { metric: "Total Followers", facebook: fbSummary.total_followers, instagram: igSummary.total_followers, window: "Overall" },
      { metric: "Total Following", facebook: fbSummary.total_following, instagram: igSummary.total_following, window: "Overall" },
      { metric: "Total Post Share", facebook: fbSummary.total_post_share, instagram: igSummary.total_post_share, window: "Overall" },
      { metric: "Total Reach", facebook: metricFromInsights(fbInsights, ["page_reach"]), instagram: metricFromInsights(igInsights, ["reach"]), window: "Last 7 days" },
      { metric: "Total Profile Views", facebook: metricFromInsights(fbInsights, ["page_views_total"]), instagram: metricFromInsights(igInsights, ["profile_views"]), window: "Last 7 days" },
      { metric: "Total Accounts Engaged", facebook: metricFromInsights(fbInsights, ["page_engaged_users"]), instagram: metricFromInsights(igInsights, ["accounts_engaged"]), window: "Last 7 days" },
      { metric: "Total Interactions", facebook: [fbRecentLikes, fbRecentComments, fbRecentShares].some((v) => v !== null) ? (fbRecentLikes || 0) + (fbRecentComments || 0) + (fbRecentShares || 0) : null, instagram: metricFromInsights(igInsights, ["total_interactions"]), window: "Last 7 days" },
      { metric: "Total Likes", facebook: fbRecentLikes, instagram: metricFromInsights(igInsights, ["likes"]), window: "Last 7 days" },
      { metric: "Total Comments", facebook: fbRecentComments, instagram: metricFromInsights(igInsights, ["comments"]), window: "Last 7 days" },
      { metric: "Total Shares", facebook: fbRecentShares, instagram: metricFromInsights(igInsights, ["shares"]) ?? igRecentShares, window: "Last 7 days" },
      { metric: "Total Views", facebook: metricFromInsights(fbInsights, ["page_impressions"]) ?? fbRecentViews, instagram: metricFromInsights(igInsights, ["views"]), window: "Last 7 days" },
      { metric: "Total Saves", facebook: null, instagram: metricFromInsights(igInsights, ["saves"]) ?? igRecentSaves, window: "Last 7 days" },
      { metric: "Total Followers Count", facebook: metricFromInsights(fbInsights, ["followers_count"]), instagram: metricFromInsights(igInsights, ["follower_count", "followers_count"]), window: "Current / 7 days" },
      { metric: "Total Follows Count", facebook: metricFromInsights(fbInsights, ["page_follows"]), instagram: metricFromInsights(igInsights, ["follows_count"]), window: "Current" },
      { metric: "Total Media Count", facebook: fbSummary.total_post_share, instagram: metricFromInsights(igInsights, ["media_count"]), window: "Current" },
    ].map((row) => ({
      metric: row.metric,
      facebook: row.facebook === null || row.facebook === undefined ? "N/A" : row.facebook,
      instagram: row.instagram === null || row.instagram === undefined ? "N/A" : row.instagram,
      window: row.window,
    }));
  }

  function isVideoUrl(url) {
    if (!url) return false;
    const clean = String(url).split("?")[0].toLowerCase();
    return clean.endsWith(".mp4") || clean.endsWith(".mov") || clean.endsWith(".webm") || clean.endsWith(".m4v");
  }

  function mediaPreviewHtml(url) {
    if (!url) return "<span class='media-empty'>No media</span>";
    if (isVideoUrl(url)) {
      return `<video class="media-preview" src="${url}" controls preload="metadata"></video>`;
    }
    return `<img class="media-preview" src="${url}" alt="post-media" loading="lazy" />`;
  }

  function renderPostsTable(container, rows) {
    if (!container) return;
    if (!rows.length) {
      container.innerHTML = "<p>No published posts yet.</p>";
      return;
    }

    const hasPlatform = rows.some((row) => row.platform);
    const hasSourceName = rows.some((row) => row.source_page_name);
    const head = `
      <tr>
        <th>id</th>
        ${hasPlatform ? "<th>platform</th>" : ""}
        ${hasSourceName ? "<th>page</th>" : ""}
        <th>message</th>
        <th>media</th>
        <th>views</th>
        <th>likes</th>
        <th>comments</th>
        <th>reason</th>
        <th>published_at</th>
        <th>scheduled_for</th>
      </tr>
    `;

    function metricCell(value, err) {
      if (value !== null && value !== undefined && value !== "") return String(value);
      const title = err ? String(err).replace(/"/g, "&quot;") : "Metric unavailable";
      return `<span title="${title}">-</span>`;
    }

    const body = rows
      .map(
        (row) => `
          <tr>
            <td>${escapeHtml(row.id)}</td>
            ${hasPlatform ? `<td>${platformBadge(row.platform)}</td>` : ""}
            ${hasSourceName ? `<td>${escapeHtml(row.source_page_name || "-")}</td>` : ""}
            <td class="insight-post-message-cell" title="${escapeHtml(row.message)}">
              <span class="insight-post-message-clamp">${escapeHtml(row.message)}</span>
            </td>
            <td>${mediaPreviewHtml(row.media_url)}</td>
            <td>${metricCell(row.total_views, row.reason)}</td>
            <td>${metricCell(row.total_likes, row.reason)}</td>
            <td>${metricCell(row.total_comments, row.reason)}</td>
            <td>${escapeHtml(row.reason || "-")}</td>
            <td>${escapeHtml(row.published_at)}</td>
            <td>${escapeHtml(row.scheduled_for)}</td>
          </tr>
        `
      )
      .join("");

    container.innerHTML = `<table>${head}${body}</table>`;
  }

  function combinedMetricText(fbValue, igValue) {
    const fb = fbValue === null || fbValue === undefined || fbValue === "" ? "-" : String(fbValue);
    const ig = igValue === null || igValue === undefined || igValue === "" ? "-" : String(igValue);
    return `FB ${fb} | IG ${ig}`;
  }

  function renderInsights(data) {
    if (!data) return;
    if (insightError) {
      insightError.textContent = "";
    }
    if (insightWarning) {
      insightWarning.textContent = data.warning ? String(data.warning) : "";
    }

    const summary = data.summary || {};
    if (data.combined) {
      const fbSummary = summary.facebook || {};
      const igSummary = summary.instagram || {};
      if (totalFollowers) totalFollowers.textContent = combinedMetricText(fbSummary.total_followers, igSummary.total_followers);
      if (totalFollowing) totalFollowing.textContent = combinedMetricText(fbSummary.total_following, igSummary.total_following);
      if (totalPostShare) totalPostShare.textContent = combinedMetricText(fbSummary.total_post_share, igSummary.total_post_share);
    } else {
      setInsightValue(totalFollowers, summary.total_followers);
      setInsightValue(totalFollowing, summary.total_following);
      setInsightValue(totalPostShare, summary.total_post_share);
    }

    if (insightMeta) {
      const fetchedAt = toIndianDateTime(data.fetched_at);
      const stats = data.post_stats_summary || {};
      const statsText = `Post stats - Live: ${stats.live_stats_posts ?? "-"} | Cached fallback: ${
        stats.cached_fallback_posts ?? "-"
      } | Missing: ${stats.missing_stats_posts ?? "-"}`;
      if (data.combined && Array.isArray(data.accounts)) {
        const ids = data.accounts.map((row) => `${row.platform}:${row.account_id}`).join(", ");
        insightMeta.textContent = `Accounts: ${ids} | Platform: ${data.platform || "-"} | Snapshot: ${
          data.snapshot_id || "-"
        } | Fetched: ${fetchedAt || "-"} | Cached: ${data.cached ? "Yes" : "No"} | ${statsText}`;
      } else {
        insightMeta.textContent = `Account ID: ${data.account_id || "-"} | Platform: ${
          data.platform || "-"
        } | Snapshot: ${data.snapshot_id || "-"} | Fetched: ${fetchedAt || "-"} | Cached: ${
          data.cached ? "Yes" : "No"
        } | ${statsText}`;
      }
    }
    if (insightPageHero && insightPageName) {
      if (data.combined && Array.isArray(data.accounts)) {
        const fbAccount = data.accounts.find((row) => row.platform === "facebook");
        const igAccount = data.accounts.find((row) => row.platform === "instagram");
        const fbName = fbAccount ? fbAccount.page_name : "-";
        const igName = igAccount ? igAccount.page_name : "-";
        insightPageName.textContent = `${fbName} + ${igName}`;
      } else {
        insightPageName.textContent = data.page_name || "-";
      }
      insightPageHero.hidden = false;
    }

    const publishedPosts = sortPublishedPosts(
      (data.published_posts || []).map((row) => ({
        ...row,
        scheduled_for_utc: row.scheduled_for || "",
        published_at_utc: row.published_at || "",
        scheduled_for: toIndianDateTime(row.scheduled_for),
        published_at: toIndianDateTime(row.published_at),
      }))
    );
    renderPostsTable(insightPostsTable, publishedPosts);

    if (insightComparisonTitle) {
      if (data.combined && Array.isArray(data.accounts)) {
        const fbAccount = data.accounts.find((row) => row.platform === "facebook");
        const igAccount = data.accounts.find((row) => row.platform === "instagram");
        const fbName = fbAccount ? fbAccount.page_name : "Facebook";
        const igName = igAccount ? igAccount.page_name : "Instagram";
        insightComparisonTitle.textContent = `Overall Insights of ${fbName} + ${igName}`;
      } else {
        insightComparisonTitle.textContent = `Overall Insights of ${data.page_name || "Selected Page"}`;
      }
    }

    const comparisonRows = Array.isArray(data.comparison_rows) && data.comparison_rows.length
      ? data.comparison_rows
      : comparisonMetricRows(data);
    renderTable(insightMetricsTable, comparisonRows);
  }

  function renderAiListCard(title, rows, tone) {
    const items = Array.isArray(rows) ? rows.filter((item) => String(item || "").trim()) : [];
    if (!items.length) {
      return `
        <article class="ai-report-card ${tone}">
          <h3>${escapeHtml(title)}</h3>
          <p class="ai-empty">No specific items available.</p>
        </article>
      `;
    }
    return `
      <article class="ai-report-card ${tone}">
        <h3>${escapeHtml(title)}</h3>
        <ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </article>
    `;
  }

  function renderAiPlanTable(title, rows) {
    const safeRows = Array.isArray(rows) ? rows : [];
    if (!safeRows.length) {
      return `
        <article class="ai-report-card">
          <h3>${escapeHtml(title)}</h3>
          <p class="ai-empty">No detailed plan generated.</p>
        </article>
      `;
    }
    return `
      <article class="ai-report-card">
        <h3>${escapeHtml(title)}</h3>
        <div class="table-wrap table-wrap-strong">
          <table class="ai-table">
            <tr>
              <th>action</th>
              <th>why</th>
              <th>expected_impact</th>
              <th>timeline</th>
            </tr>
            ${safeRows
              .map(
                (row) => `
                  <tr>
                    <td>${escapeHtml(row.action)}</td>
                    <td>${escapeHtml(row.why)}</td>
                    <td>${escapeHtml(row.expected_impact)}</td>
                    <td>${escapeHtml(row.timeline)}</td>
                  </tr>
                `
              )
              .join("")}
          </table>
        </div>
      </article>
    `;
  }

  function renderAiKpiTable(rows) {
    const safeRows = Array.isArray(rows) ? rows : [];
    if (!safeRows.length) {
      return `
        <article class="ai-report-card">
          <h3>7-day KPI growth plan</h3>
          <p class="ai-empty">No KPI target table generated.</p>
        </article>
      `;
    }
    return `
      <article class="ai-report-card">
        <h3>7-day KPI growth plan</h3>
        <div class="table-wrap table-wrap-strong">
          <table class="ai-table">
            <tr>
              <th>metric</th>
              <th>current</th>
              <th>target_7d</th>
              <th>how</th>
            </tr>
            ${safeRows
              .map(
                (row) => `
                  <tr>
                    <td>${escapeHtml(row.metric)}</td>
                    <td>${escapeHtml(row.current)}</td>
                    <td>${escapeHtml(row.target_7d)}</td>
                    <td>${escapeHtml(row.how)}</td>
                  </tr>
                `
              )
              .join("")}
          </table>
        </div>
      </article>
    `;
  }

  function renderAiInsights(data) {
    if (!aiInsightResult || !aiInsightMeta) return;
    const analysis = data && data.analysis ? data.analysis : {};
    const cadence = (data && data.source_overview && data.source_overview.posting_cadence) || {};
    const perf = (data && data.source_overview && data.source_overview.performance_last_7d) || {};

    const cadenceText = [
      `Posts 24h: ${cadence.posts_last_24h ?? "-"}`,
      `Posts 7d: ${cadence.posts_last_7d ?? "-"}`,
      `Posts 30d: ${cadence.posts_last_30d ?? "-"}`,
      `Avg/day (7d): ${cadence.avg_posts_per_day_last_7d ?? "-"}`,
      `FB posts 7d: ${cadence.facebook_posts_last_7d ?? "-"}`,
      `FB avg/day (7d): ${cadence.facebook_avg_posts_per_day_last_7d ?? "-"}`,
      `IG posts 7d: ${cadence.instagram_posts_last_7d ?? "-"}`,
      `IG avg/day (7d): ${cadence.instagram_avg_posts_per_day_last_7d ?? "-"}`,
    ].join(" | ");

    const perfText = [
      `Views: ${perf.views ?? "-"}`,
      `Likes: ${perf.likes ?? "-"}`,
      `Comments: ${perf.comments ?? "-"}`,
      `Shares: ${perf.shares ?? "-"}`,
      `Saves: ${perf.saves ?? "-"}`,
    ].join(" | ");

    aiInsightMeta.textContent = `Profile: ${data.page_name || "-"} | Platform: ${data.platform || "-"} | Model: ${
      data.model || "-"
    } | Snapshot fetched: ${toIndianDateTime(data.fetched_at) || "-"} | AI generated: ${
      toIndianDateTime(data.generated_at) || "-"
    } | Cached: ${data.cached ? "Yes" : "No"}`;

    aiInsightResult.innerHTML = `
      <div class="ai-report-header">
        <h3>Executive summary</h3>
        <p>${escapeHtml(analysis.executive_summary || "No summary generated.")}</p>
      </div>
      <div class="ai-report-meta">
        <span>${escapeHtml(cadenceText)}</span>
        <span>${escapeHtml(perfText)}</span>
      </div>
      <div class="ai-report-grid">
        ${renderAiListCard("Pros", analysis.pros || [], "tone-good")}
        ${renderAiListCard("Cons", analysis.cons || [], "tone-bad")}
        ${renderAiListCard("Risks", analysis.risks || [], "tone-bad")}
        ${renderAiListCard("Opportunities", analysis.opportunities || [], "tone-good")}
      </div>
      <article class="ai-report-card">
        <h3>Posting strategy</h3>
        <p><strong>Current:</strong> ${escapeHtml(
          (analysis.posting_strategy || {}).current_posting || "Not specified"
        )}</p>
        <p><strong>Recommended:</strong> ${escapeHtml(
          (analysis.posting_strategy || {}).recommended_posting || "Not specified"
        )}</p>
        <p><strong>Reasoning:</strong> ${escapeHtml((analysis.posting_strategy || {}).reasoning || "Not specified")}</p>
      </article>
      ${renderAiPlanTable("7-day action plan", analysis.action_plan_7d || [])}
      ${renderAiKpiTable(analysis.kpi_growth_plan || [])}
      ${renderAiListCard("Content ideas", analysis.content_ideas || [], "")}
      ${renderAiListCard("Best recommendation for grow your profile", analysis.best_recommendations_for_growth || [], "tone-good")}
    `;
  }

  async function loadPublicUrlStatus() {
    if (!publicUrlStatus) return;
    try {
      const data = await fetchJSON("/dashboard/public-url-status/");
      const warnings = Array.isArray(data.warnings) ? data.warnings : [];
      const notes = Array.isArray(data.notes) ? data.notes : [];
      const parts = [];
      if (warnings.length) parts.push(`Config warning: ${warnings.join(" | ")}`);
      if (notes.length) parts.push(notes.join(" | "));
      publicUrlStatus.textContent = parts.join(" | ");
    } catch (_err) {
      publicUrlStatus.textContent = "";
    }
  }

  async function loadInsights(forceRefresh) {
    if (!insightAccountId) return;
    const accountId = Number(insightAccountId.value);
    if (!accountId) {
      if (insightError) insightError.textContent = "Enter valid account id";
      if (insightWarning) insightWarning.textContent = "";
      return;
    }

    const suffix = forceRefresh ? "?refresh=1" : "";
    try {
      const data = await fetchJSON(`/api/insights/${accountId}/${suffix}`);
      if (insightError) insightError.textContent = "";
      renderInsights(data);
    } catch (err) {
      if (insightError) insightError.textContent = err.message;
      if (insightWarning) insightWarning.textContent = "";
    }
  }

  if (fetchInsightsBtn) {
    const runWithFetchInsightsLoading = withButtonLoading(fetchInsightsBtn, "Fetch Cached/Latest", "Fetching...");
    fetchInsightsBtn.addEventListener("click", () => runWithFetchInsightsLoading(() => loadInsights(false)));
  }
  if (refreshInsightsBtn) {
    const runWithRefreshInsightsLoading = withButtonLoading(refreshInsightsBtn, "Force Refresh", "Refreshing...");
    refreshInsightsBtn.addEventListener("click", () => runWithRefreshInsightsLoading(() => loadInsights(true)));
  }

  if (insightAccountId && Number(insightAccountId.value)) {
    loadPublicUrlStatus();
    loadInsights(false);
  }

  if (insightAccountId) {
    loadPublicUrlStatus();
    const insightParams = new URLSearchParams(window.location.search);
    const prefillInsightAccountId = insightParams.get("account_id");
    if (prefillInsightAccountId && !insightAccountId.value) {
      insightAccountId.value = prefillInsightAccountId;
      loadInsights(false);
    }
  }

  async function loadAiInsights() {
    if (!aiInsightAccountId) return;
    const accountId = Number(aiInsightAccountId.value);
    if (!accountId) {
      if (aiInsightError) aiInsightError.textContent = "Enter valid account id";
      return;
    }

    const focus = aiInsightGoal ? String(aiInsightGoal.value || "").trim() : "";
    const forceRefresh = !!(aiInsightForceRefresh && aiInsightForceRefresh.checked);
    try {
      const data = await fetchJSON(`/api/ai-insights/${accountId}/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({
          focus,
          force_refresh: forceRefresh,
        }),
      });
      if (aiInsightError) aiInsightError.textContent = "";
      renderAiInsights(data);
    } catch (err) {
      if (aiInsightError) aiInsightError.textContent = err.message;
      if (aiInsightResult) aiInsightResult.innerHTML = "<p class='ai-output-empty'>Unable to generate AI insights.</p>";
      if (aiInsightMeta) aiInsightMeta.textContent = "";
    }
  }

  if (runAiInsightsBtn) {
    const runWithAiLoading = withButtonLoading(runAiInsightsBtn, "Generate AI Insights", "Analyzing...");
    runAiInsightsBtn.addEventListener("click", () => runWithAiLoading(loadAiInsights));
  }

  if (aiInsightAccountId) {
    const params = new URLSearchParams(window.location.search);
    const prefillAccountId = params.get("account_id");
    if (prefillAccountId && !aiInsightAccountId.value) {
      aiInsightAccountId.value = prefillAccountId;
    }
  }

  if (tokenHealthButton && tokenHealthInfoBtn) {
    const toggle = () => setHealthPopoverOpen(tokenHealthPopover ? tokenHealthPopover.hidden : true);
    tokenHealthButton.addEventListener("click", toggle);
    tokenHealthInfoBtn.addEventListener("click", toggle);
    document.addEventListener("click", (event) => {
      if (!tokenHealthNav || !(event.target instanceof Node)) return;
      if (tokenHealthNav.contains(event.target)) return;
      setHealthPopoverOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setHealthPopoverOpen(false);
    });
  }

  loadTokenHealth();
})();
