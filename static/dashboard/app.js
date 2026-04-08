(function () {
  let cachedAccountsRows = [];
  let cachedScheduledRows = [];

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
      if (
        response.status === 402 &&
        data &&
        typeof data === "object" &&
        data.code === "subscription_expired" &&
        data.redirect_url
      ) {
        window.location.href = String(data.redirect_url);
        throw new Error("Subscription expired.");
      }
      if (typeof data === "string") {
        throw new Error(formatUiErrorMessage(data, response.status));
      }
      throw new Error(formatUiErrorMessage(data, response.status));
    }
    return data;
  }

  function formatUiErrorMessage(value, statusCode = null) {
    if (value && typeof value === "object") {
      const details = value.details ? String(value.details) : "";
      const error = value.error ? String(value.error) : "";
      return sanitizeUiError(details || error || "Request failed.", statusCode);
    }
    return sanitizeUiError(value, statusCode);
  }

  function sanitizeUiError(value, statusCode = null) {
    const text = String(value || "").trim();
    if (!text) return "Request failed.";
    const compact = text.replace(/\s+/g, " ").trim();
    const lowered = compact.toLowerCase();
    if (statusCode === 413) {
      return "Uploaded media is larger than the current server upload limit. Increase VPS nginx client_max_body_size or upload a smaller file.";
    }
    if (statusCode === 502 || statusCode === 504) {
      return "Upstream service is temporarily unavailable. Retry once.";
    }
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

  function isHtmlLikeResponse(value) {
    const lowered = String(value || "").toLowerCase();
    return lowered.includes("<!doctype html") || lowered.includes("<html") || lowered.includes("</html>");
  }

  function isTransientFetchError(error) {
    const text = String((error && error.message) || error || "").trim().toLowerCase();
    if (!text) return false;
    return (
      text.includes("aborted") ||
      text.includes("aborterror") ||
      text.includes("failed to fetch") ||
      text.includes("networkerror") ||
      text.includes("load failed") ||
      text.includes("upstream service returned an unreadable html error page")
    );
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
    const head = `<tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr>`;
    const body = rows
      .map((row) => `<tr>${headers.map((h) => `<td>${escapeHtml(row[h] ?? "")}</td>`).join("")}</tr>`)
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
    const explicitImage = sanitizeUrl(row.profile_picture_url);
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
          <img class="avatar-img" src="${escapeHtml(graphUrl)}" alt="${escapeHtml(name)}" loading="lazy"
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

    container.innerHTML = `<table class="accounts-table">${head}${body}</table>`;
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

  function sanitizeUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return "";
      }
      return parsed.href;
    } catch (_err) {
      return "";
    }
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
        <th>status_note</th>
        <th>page_name</th>
        <th>actions</th>
      </tr>
    `;

    const body = rows
      .map((row) => {
        const canRetry = row.status === "failed";
        const normalizedError = String(row.error_message || "");
        const isRetrying = row.status === "pending" && /auto-retry in/i.test(normalizedError);
        const statusLabel = isRetrying ? "retrying" : String(row.status || "");
        const statusClass = `queue-status queue-status-${String(statusLabel || "unknown")
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")}`;
        let errorDisplay = normalizedError;
        if (isRetrying) {
          const retryWindowMatch = normalizedError.match(/auto-retry in\s+\d+s/i);
          const retryWindow = retryWindowMatch ? retryWindowMatch[0].replace(/^auto/i, "Auto") : "Auto-retry scheduled";
          errorDisplay = `Meta is pacing requests. ${retryWindow}.`;
        }
        const errorCellTitle = normalizedError || errorDisplay;
        const prettyStatusLabel = statusLabel ? statusLabel.charAt(0).toUpperCase() + statusLabel.slice(1) : "Unknown";
        return `
          <tr>
            <td>${escapeHtml(row.id)}</td>
            <td>${escapeHtml(row.platform)}</td>
            <td class="scheduled-message-cell" title="${escapeHtml(row.message)}">
              <span class="scheduled-message-clamp">${escapeHtml(row.message)}</span>
            </td>
            <td>${escapeHtml(row.media_url)}</td>
            <td>${escapeHtml(row.scheduled_for)}</td>
            <td>${escapeHtml(row.due_in)}</td>
            <td><span class="${statusClass}">${escapeHtml(prettyStatusLabel)}</span></td>
            <td title="${escapeHtml(errorCellTitle)}">${escapeHtml(errorDisplay)}</td>
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
    if (row && row.platform) return String(row.platform).toLowerCase();
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
    const accountsViewMeta = document.getElementById("accountsViewMeta");
    const refreshCatalog = options.refreshCatalog === true;
    const refreshAccounts = options.refreshAccounts === true;
    if (!table) return;
    let rows = [];
    try {
      // Primary table should load fast and independently.
      const accountsEndpoint = refreshAccounts ? "/api/accounts/?refresh=1" : "/api/accounts/";
      rows = await fetchJSON(accountsEndpoint);
      if (!Array.isArray(rows)) {
        throw new Error(formatUiErrorMessage(rows));
      }
      cachedAccountsRows = rows;
      renderAccountsFromCache();
    } catch (err) {
      const fallbackMessage = refreshAccounts
        ? "Refresh was interrupted. Showing last loaded accounts."
        : "Accounts list is temporarily unavailable. Retry once.";
      const safeMessage = isTransientFetchError(err) ? fallbackMessage : sanitizeUiError(err && err.message);
      if (cachedAccountsRows.length) {
        rows = cachedAccountsRows;
        renderAccountsFromCache();
        if (accountsViewMeta) {
          accountsViewMeta.textContent = `${accountsViewMeta.textContent} | ${safeMessage}`;
        }
      } else {
        table.innerHTML = `<p>${escapeHtml(safeMessage)}</p>`;
        rows = [];
      }
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
      if (catalogTable && !catalogTable.innerHTML.trim()) catalogTable.innerHTML = "<p>Catalog unavailable right now.</p>";
      if (catalogStatus && !catalogStatus.textContent.trim()) catalogStatus.textContent = "";
    }
  }

  async function loadScheduledPosts() {
    const table = document.getElementById("scheduledTable");
    const scheduledStatusFilter = document.getElementById("scheduledStatusFilter");
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
      cachedScheduledRows = rowsWithLocalTime;
      const selectedStatus = scheduledStatusFilter ? String(scheduledStatusFilter.value || "all") : "all";
      const filteredRows = cachedScheduledRows.filter((row) => {
        const normalizedError = String(row.error_message || "").toLowerCase();
        const retrying = row.status === "pending" && normalizedError.includes("auto-retry in");
        if (selectedStatus === "all") return true;
        if (selectedStatus === "retrying") return retrying;
        return String(row.status || "").toLowerCase() === selectedStatus;
      });
      renderScheduledTable(table, filteredRows);
    } catch (err) {
      table.innerHTML = `<p>${escapeHtml(err.message)}</p>`;
    }
  }

  async function loadPublishHealthStatus() {
    const schedulerTarget = document.getElementById("publishHealthStatus");
    const homeTarget = document.getElementById("publishHealthSummary");
    if (!schedulerTarget && !homeTarget) return;
    try {
      const data = await fetchJSON("/api/posts/publish-health-status/");
      const parts = [
        `${Number(data.retrying_count || 0)} retrying`,
        `${Number(data.processing_count || 0)} processing`,
        `${Number(data.due_pending_count || 0)} due pending`,
        `${Number(data.published_last_6h || 0)} published in last 6h`,
      ];
      const latestRetry = String(data.latest_retry_scheduled_for || "").trim();
      const retrySuffix = latestRetry ? ` Next retry: ${toIndianDateTime(latestRetry) || latestRetry}.` : "";
      const summary = `${parts.join(" | ")}.${retrySuffix}`;
      if (schedulerTarget) schedulerTarget.textContent = summary;
      if (homeTarget) homeTarget.textContent = summary;
    } catch (err) {
      const message = `Publish health unavailable: ${err.message}`;
      if (schedulerTarget) schedulerTarget.textContent = message;
      if (homeTarget) homeTarget.textContent = message;
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
  const forceRefreshProgressWrap = document.getElementById("forceRefreshProgressWrap");
  const forceRefreshProgressFill = document.getElementById("forceRefreshProgressFill");
  const forceRefreshProgressText = document.getElementById("forceRefreshProgressText");
  const FORCE_REFRESH_STATUS_POLL_MS = 7000;
  const FORCE_REFRESH_STATUS_POLL_MAX_MS = 30000;
  let forceRefreshPollTimer = null;
  let forceRefreshPollInFlight = false;
  let forceRefreshPollFailureCount = 0;
  let lastAutoReconciledRunId = null;
  let lastCompletedForceRefreshRunId = null;
  const forceRefreshLabel = "Force Refresh All Profiles";
  if (refreshAccountsBtn) {
    const runWithRefreshAccountsLoading = withButtonLoading(refreshAccountsBtn, "Refresh List", "Refreshing...");
    refreshAccountsBtn.addEventListener("click", () =>
      runWithRefreshAccountsLoading(() => loadAccounts({ refreshCatalog: true, refreshAccounts: true }))
    );
    loadAccounts();
  }
  if (forceRefreshAllBtn) {
    const clearForceRefreshPolling = () => {
      if (forceRefreshPollTimer) {
        window.clearTimeout(forceRefreshPollTimer);
        forceRefreshPollTimer = null;
      }
    };
    const scheduleForceRefreshPoll = (delayMs = FORCE_REFRESH_STATUS_POLL_MS) => {
      clearForceRefreshPolling();
      forceRefreshPollTimer = window.setTimeout(loadForceRefreshStatus, delayMs);
    };

    const renderForceRefreshProgress = (state) => {
      const running = Boolean(state && state.has_active_run);
      const percent = Math.max(0, Math.min(100, Number((state && state.progress_percent) || 0)));
      if (running) {
        forceRefreshAllBtn.disabled = true;
        forceRefreshAllBtn.textContent = "Force Refresh Running...";
        if (forceRefreshProgressWrap) forceRefreshProgressWrap.hidden = false;
        if (forceRefreshProgressFill) forceRefreshProgressFill.style.width = `${percent}%`;
        if (forceRefreshProgressText) {
          const done = Number(state.processed_count || 0);
          const total = Number(state.total_accounts || 0);
          forceRefreshProgressText.textContent = `${percent}% completed (${done}/${total || "-"})`;
        }
        return;
      }
      forceRefreshAllBtn.disabled = false;
      forceRefreshAllBtn.textContent = forceRefreshLabel;
      if (forceRefreshProgressWrap) forceRefreshProgressWrap.hidden = true;
      if (forceRefreshProgressFill) forceRefreshProgressFill.style.width = "0%";
      if (forceRefreshProgressText) forceRefreshProgressText.textContent = "";
    };

    const loadForceRefreshStatus = async () => {
      if (forceRefreshPollInFlight) return;
      forceRefreshPollInFlight = true;
      try {
        const status = await fetchJSON("/api/insights/force-refresh-all/status/");
        forceRefreshPollFailureCount = 0;
        renderForceRefreshProgress(status);
        const running = Boolean(status && status.has_active_run);
        if (running) {
          scheduleForceRefreshPoll(FORCE_REFRESH_STATUS_POLL_MS);
        }
        if (!running) {
          clearForceRefreshPolling();
          if (status && status.auto_reconciled && status.run_id !== lastAutoReconciledRunId) {
            lastAutoReconciledRunId = status.run_id;
            showAppToast("Previous stuck force refresh was auto-recovered and finalized.", "success");
          }
          if (
            status &&
            status.run_id &&
            status.status &&
            status.status !== "idle" &&
            status.run_id !== lastCompletedForceRefreshRunId
          ) {
            lastCompletedForceRefreshRunId = status.run_id;
            loadAccounts({ refreshAccounts: true });
          }
          if (status && status.status && status.status !== "idle" && accountsBulkRefreshStatus) {
            const finalPct = Number(status.progress_percent || 0);
            if (status.status === "completed_with_errors") {
              accountsBulkRefreshStatus.textContent = `Force refresh completed with some errors. Progress: ${finalPct}%.`;
            } else if (status.status === "completed") {
              accountsBulkRefreshStatus.textContent = `Force refresh completed successfully. Progress: ${finalPct}%.`;
            }
          }
        }
      } catch (err) {
        forceRefreshPollFailureCount += 1;
        const retryMs = Math.min(
          FORCE_REFRESH_STATUS_POLL_MAX_MS,
          FORCE_REFRESH_STATUS_POLL_MS * Math.max(1, forceRefreshPollFailureCount)
        );
        if (accountsBulkRefreshStatus) {
          const msg = sanitizeUiError(err && err.message ? err.message : "Force refresh status check failed.");
          const retrySec = Math.ceil(retryMs / 1000);
          accountsBulkRefreshStatus.textContent = `${msg} Retrying status check in ${retrySec}s.`;
        }
        scheduleForceRefreshPoll(retryMs);
      } finally {
        forceRefreshPollInFlight = false;
      }
    };

    forceRefreshAllBtn.addEventListener("click", async () => {
      if (forceRefreshAllBtn.disabled) return;
      const confirmed = window.confirm(
        "Are you sure? It can take significant time to collect all data from Meta and depends on your connected profiles (FB Pages + Insta profiles)."
      );
      if (!confirmed) return;
      forceRefreshAllBtn.disabled = true;
      forceRefreshAllBtn.textContent = "Queuing Force Refresh...";
      try {
        const postForceRefresh = async (payload = {}) => {
          const response = await fetch("/api/insights/force-refresh-all/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify(payload),
          });
          const contentType = response.headers.get("content-type") || "";
          const data = contentType.includes("application/json") ? await response.json() : {};
          return { response, data };
        };

        let { response, data } = await postForceRefresh({});
        if (!response.ok && response.status === 409 && data && data.can_override) {
          const overrideMessage = sanitizeUiError(
            data.details || "Instagram publishing queue is busy. Do you want to continue force refresh anyway?"
          );
          const proceed = window.confirm(`${overrideMessage}\n\nContinue force refresh anyway?`);
          if (proceed) {
            ({ response, data } = await postForceRefresh({ ignore_ig_guard: true }));
          }
        }

        if (!response.ok) {
          const errorMessage = sanitizeUiError((data && (data.details || data.error)) || "Force refresh request failed.");
          if (accountsBulkRefreshStatus) accountsBulkRefreshStatus.textContent = errorMessage;
          showAppToast(errorMessage, "error");
          renderForceRefreshProgress(data);
          if (data && data.has_active_run) {
            forceRefreshPollFailureCount = 0;
            scheduleForceRefreshPoll(FORCE_REFRESH_STATUS_POLL_MS);
          }
          return;
        }

        const queuedAt = toIndianDateTime(data.queued_at) || "-";
        const message = `${data.message || "Force refresh request queued."} Queued at: ${queuedAt}`;
        if (accountsBulkRefreshStatus) accountsBulkRefreshStatus.textContent = message;
        showAppToast(message, "success");
        renderForceRefreshProgress(data);
        forceRefreshPollFailureCount = 0;
        scheduleForceRefreshPoll(FORCE_REFRESH_STATUS_POLL_MS);
      } catch (err) {
        const message = sanitizeUiError(err && err.message ? err.message : "Force refresh request failed.");
        if (accountsBulkRefreshStatus) accountsBulkRefreshStatus.textContent = message;
        showAppToast(message, "error");
        clearForceRefreshPolling();
        forceRefreshAllBtn.disabled = false;
        forceRefreshAllBtn.textContent = forceRefreshLabel;
      }
    });

    loadForceRefreshStatus();
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
    refreshScheduledBtn.addEventListener("click", () => runWithRefreshScheduleLoading(async () => {
      await loadScheduledPosts();
      await loadPublishHealthStatus();
    }));
    loadScheduledPosts();
  }
  const scheduledTable = document.getElementById("scheduledTable");
  const scheduledStatusFilter = document.getElementById("scheduledStatusFilter");
  if (scheduledTable) {
    if (scheduledStatusFilter) {
      scheduledStatusFilter.addEventListener("change", () => loadScheduledPosts());
    }
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
        await loadPublishHealthStatus();
      } catch (err) {
        window.alert(`Retry failed: ${err.message}`);
      }
    });
  }
  if (document.getElementById("publishHealthStatus") || document.getElementById("publishHealthSummary")) {
    loadPublishHealthStatus();
  }

  const scheduleForm = document.getElementById("scheduleForm");
  const schedulerAssistStatus = document.getElementById("schedulerAssistStatus");
  const schedulerBestTimeAssist = document.getElementById("schedulerBestTimeAssist");
  const schedulerCadenceAssist = document.getElementById("schedulerCadenceAssist");
  const schedulerCaptionAssist = document.getElementById("schedulerCaptionAssist");
  if (scheduleForm) {
    const scheduleParams = new URLSearchParams(window.location.search);
    const prefillAccountId = scheduleParams.get("account_id");
    const prefillPlatform = scheduleParams.get("platform");
    const accountIdInput = scheduleForm.querySelector("[name='account_id']");
    const platformInput = scheduleForm.querySelector("[name='platform']");
    const pageNameInput = document.getElementById("scheduleAccountPageName");
    const schedulerAccountNameMap = new Map();
    let schedulerAccountMapLoaded = false;
    let schedulerAssistTimer = null;

    const assistContent = document.getElementById("schedulerAssistContent");

    const _platformLabel = (platform) =>
      platform === "facebook" ? "Facebook" : platform === "instagram" ? "Instagram" : platform;
    const _platformCls = (platform) => (platform === "instagram" ? " ig" : "");

    const _buildCadencePanel = (platforms, platformKeys) => {
      let html = `<div class="assist-panel-header"><span class="assist-panel-icon">\u{1F4CA}</span><h3 class="assist-title">Posting Cadence</h3></div>`;
      platformKeys.forEach((p) => {
        const row = platforms[p] || {};
        const label = escapeHtml(_platformLabel(p));
        html += `<div class="assist-platform-block">
          <div class="assist-platform-label${_platformCls(p)}">${label}</div>
          <div class="assist-metric-row">
            <span class="assist-metric"><span class="assist-metric-value">${escapeHtml(String(row.posts_last_7d ?? 0))}</span> posts in 7 days</span>
            <span class="assist-metric"><strong>${escapeHtml(String(row.avg_posts_per_day_7d ?? 0))}</strong> avg/day</span>
          </div>
          <div class="assist-recommendation">\u{2192} <em>${escapeHtml(row.recommended_cadence || "Start posting regularly")}</em></div>
        </div>`;
      });
      return html;
    };

    const _buildBestTimePanel = (platforms, platformKeys) => {
      let html = `<div class="assist-panel-header"><span class="assist-panel-icon">\u{23F0}</span><h3 class="assist-title">Best Time to Post</h3></div>`;
      platformKeys.forEach((p) => {
        const row = platforms[p] || {};
        const label = escapeHtml(_platformLabel(p));
        const nextWindow = escapeHtml(row.next_best_window || "Not enough data");
        const bestFormat = row.best_format && row.best_format.format ? escapeHtml(row.best_format.format) : null;
        const nextTopic = row.next_topic ? escapeHtml(row.next_topic) : null;
        const slots = Array.isArray(row.best_time_slots) ? row.best_time_slots.slice(0, 3) : [];
        html += `<div class="assist-platform-block">
          <div class="assist-platform-label${_platformCls(p)}">${label}</div>
          <div class="assist-metric-row">
            <span class="assist-metric">Next best window: <strong>${nextWindow}</strong></span>
          </div>`;
        if (slots.length) {
          html += `<div class="assist-slots">`;
          slots.forEach((s, i) => {
            const chipCls = i === 0 ? "assist-slot-chip top" : "assist-slot-chip";
            html += `<span class="${chipCls}">${escapeHtml(s.label)} \u2022 ${escapeHtml(String(s.sample_posts))} posts</span>`;
          });
          html += `</div>`;
        }
        if (bestFormat || nextTopic) {
          html += `<div class="assist-recommendation">`;
          if (bestFormat) html += `Best format: <em>${bestFormat}</em>`;
          if (bestFormat && nextTopic) html += ` \u00B7 `;
          if (nextTopic) html += `Topic: <em>${nextTopic}</em>`;
          html += `</div>`;
        }
        html += `</div>`;
      });
      return html;
    };

    const _buildCaptionPanel = (platforms, platformKeys) => {
      let html = `<div class="assist-panel-header"><span class="assist-panel-icon">\u{270D}\u{FE0F}</span><h3 class="assist-title">A/B Caption Strategy</h3></div>`;
      platformKeys.forEach((p) => {
        const row = platforms[p] || {};
        const label = escapeHtml(_platformLabel(p));
        const ab = row.caption_ab_test || {};
        html += `<div class="assist-platform-block">
          <div class="assist-platform-label${_platformCls(p)}">${label}</div>
          <div class="assist-recommendation"><em>${escapeHtml(ab.primary_test || "Short vs Medium captions")}</em></div>
          <div class="assist-metric" style="margin-top:4px">${escapeHtml(ab.reasoning || "Run A/B test with different caption lengths.")}</div>
        </div>`;
      });
      return html;
    };

    const renderSchedulerAssist = (data) => {
      const platforms = data && data.platforms && typeof data.platforms === "object" ? data.platforms : {};
      const platformKeys = Object.keys(platforms);
      if (!platformKeys.length) {
        if (schedulerAssistStatus) {
          schedulerAssistStatus.textContent = "Not enough post history yet for this profile.";
          schedulerAssistStatus.classList.remove("loading");
        }
        if (assistContent) assistContent.classList.add("hidden");
        if (schedulerCadenceAssist) schedulerCadenceAssist.innerHTML = "";
        if (schedulerBestTimeAssist) schedulerBestTimeAssist.innerHTML = "";
        if (schedulerCaptionAssist) schedulerCaptionAssist.innerHTML = "";
        return;
      }
      if (schedulerAssistStatus) {
        schedulerAssistStatus.innerHTML = `\u{2705} Strategy loaded for <strong>${escapeHtml(data.page_name || "selected account")}</strong>`;
        schedulerAssistStatus.classList.remove("loading");
      }
      if (assistContent) assistContent.classList.remove("hidden");
      if (schedulerCadenceAssist) schedulerCadenceAssist.innerHTML = _buildCadencePanel(platforms, platformKeys);
      if (schedulerBestTimeAssist) schedulerBestTimeAssist.innerHTML = _buildBestTimePanel(platforms, platformKeys);
      if (schedulerCaptionAssist) schedulerCaptionAssist.innerHTML = _buildCaptionPanel(platforms, platformKeys);
    };

    const loadSchedulerAssist = async (accountId) => {
      if (!accountId) {
        if (schedulerAssistStatus) {
          schedulerAssistStatus.textContent = "Enter Account ID to load profile-wise best posting guidance.";
          schedulerAssistStatus.classList.remove("loading");
        }
        if (assistContent) assistContent.classList.add("hidden");
        if (schedulerCadenceAssist) schedulerCadenceAssist.innerHTML = "";
        if (schedulerBestTimeAssist) schedulerBestTimeAssist.innerHTML = "";
        if (schedulerCaptionAssist) schedulerCaptionAssist.innerHTML = "";
        return;
      }
      if (schedulerAssistStatus) {
        schedulerAssistStatus.textContent = "Analyzing posting history\u2026";
        schedulerAssistStatus.classList.add("loading");
      }
      if (assistContent) assistContent.classList.add("hidden");
      try {
        const data = await fetchJSON(`/api/insights/scheduler-assist/${accountId}/`);
        renderSchedulerAssist(data);
      } catch (err) {
        if (schedulerAssistStatus) {
          schedulerAssistStatus.textContent = `Assist unavailable: ${err.message}`;
          schedulerAssistStatus.classList.remove("loading");
        }
        if (assistContent) assistContent.classList.add("hidden");
        if (schedulerCadenceAssist) schedulerCadenceAssist.innerHTML = "";
        if (schedulerBestTimeAssist) schedulerBestTimeAssist.innerHTML = "";
        if (schedulerCaptionAssist) schedulerCaptionAssist.innerHTML = "";
      }
    };

    const setSchedulerPageName = (name, accountId) => {
      if (!pageNameInput) return;
      const safeName = String(name || "").trim();
      if (!accountId) {
        pageNameInput.value = "";
        pageNameInput.placeholder = "Type/select Account ID to view page name";
        return;
      }
      if (safeName) {
        pageNameInput.value = safeName;
        return;
      }
      pageNameInput.value = "";
      pageNameInput.placeholder = "Account ID found nahi hua. Connected Accounts me check karein.";
    };

    const loadSchedulerAccountMap = async () => {
      if (schedulerAccountMapLoaded) return;
      try {
        const rows = await fetchJSON("/api/accounts/");
        schedulerAccountNameMap.clear();
        if (Array.isArray(rows)) {
          const mergedRows = mergeAccountRows(rows);
          mergedRows.forEach((row) => {
            const accountId = Number(row.account_id);
            if (!accountId || Number.isNaN(accountId)) return;
            const combinedName = cleanProfileName(row.profile_name);
            if (combinedName) {
              schedulerAccountNameMap.set(accountId, combinedName);
            }
          });

          // Fallback for any non-merged / direct account id lookups.
          rows.forEach((row) => {
            const accountId = Number(row.id);
            if (!accountId || Number.isNaN(accountId)) return;
            if (!schedulerAccountNameMap.has(accountId)) {
              schedulerAccountNameMap.set(accountId, cleanProfileName(row.page_name));
            }
          });
        }
        schedulerAccountMapLoaded = true;
      } catch (err) {
        schedulerAccountMapLoaded = false;
      }
    };

    const refreshSchedulerPageName = async () => {
      if (!accountIdInput) return;
      const accountId = Number(accountIdInput.value);
      if (!accountId || Number.isNaN(accountId)) {
        setSchedulerPageName("", null);
        await loadSchedulerAssist(null);
        return;
      }
      if (!schedulerAccountMapLoaded) {
        await loadSchedulerAccountMap();
      }
      setSchedulerPageName(schedulerAccountNameMap.get(accountId) || "", accountId);
      await loadSchedulerAssist(accountId);
    };

    if (accountIdInput && prefillAccountId) accountIdInput.value = prefillAccountId;
    if (platformInput && prefillPlatform) platformInput.value = prefillPlatform;
    if (accountIdInput) {
      accountIdInput.addEventListener("input", () => {
        if (schedulerAssistTimer) window.clearTimeout(schedulerAssistTimer);
        schedulerAssistTimer = window.setTimeout(() => {
          refreshSchedulerPageName();
        }, 220);
      });
      accountIdInput.addEventListener("change", () => {
        refreshSchedulerPageName();
      });
    }
    refreshSchedulerPageName();

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
        scheduleForm.reset();
        setSchedulerPageName("", null);
        await loadSchedulerAssist(null);
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
  const profileForm = document.getElementById("profileForm");
  const saveProfileBtn = document.getElementById("saveProfileBtn");
  const profileResult = document.getElementById("profileResult");
  const profileEmail = document.getElementById("profileEmail");
  const profileFirstName = document.getElementById("profileFirstName");
  const profileLastName = document.getElementById("profileLastName");
  const profilePictureUrl = document.getElementById("profilePictureUrl");
  const profilePlan = document.getElementById("profilePlan");
  const profilePlanStatus = document.getElementById("profilePlanStatus");
  const profilePlanExpiry = document.getElementById("profilePlanExpiry");
  const profileAvatarPreview = document.getElementById("profileAvatarPreview");
  const profileAvatarFallback = document.getElementById("profileAvatarFallback");
  const profileNamePreview = document.getElementById("profileNamePreview");
  const profileEmailPreview = document.getElementById("profileEmailPreview");
  const profilePlanPreview = document.getElementById("profilePlanPreview");
  const profileStatusPreview = document.getElementById("profileStatusPreview");
  const profileExpiryPreview = document.getElementById("profileExpiryPreview");
  const subscriptionShell = document.getElementById("subscriptionShell");
  const subscriptionResult = document.getElementById("subscriptionResult");

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

  function profileInitials(firstName, lastName, email) {
    const first = String(firstName || "").trim();
    const last = String(lastName || "").trim();
    if (first || last) {
      return `${first.slice(0, 1)}${last.slice(0, 1)}`.toUpperCase() || "NA";
    }
    const mail = String(email || "").trim();
    return (mail.slice(0, 2) || "NA").toUpperCase();
  }

  const profileMemberSince = document.getElementById("profileMemberSince");
  const statConnectedAccounts = document.getElementById("statConnectedAccounts");
  const statAccountBreakdown = document.getElementById("statAccountBreakdown");
  const statPublished = document.getElementById("statPublished");
  const statTotalScheduled = document.getElementById("statTotalScheduled");
  const statPending = document.getElementById("statPending");
  const statFailed = document.getElementById("statFailed");
  const statDaysLeft = document.getElementById("statDaysLeft");
  const statMemberSince = document.getElementById("statMemberSince");

  function setProfilePreview(data) {
    if (!data) return;
    const firstName = String(data.first_name || "").trim();
    const lastName = String(data.last_name || "").trim();
    const email = String(data.email || "").trim();
    const avatarUrl = sanitizeUrl(data.profile_picture_url);
    const fullName = `${firstName} ${lastName}`.trim() || "Your Name";

    if (profileNamePreview) profileNamePreview.textContent = fullName;
    if (profileEmailPreview) profileEmailPreview.textContent = email || "-";
    if (profilePlanPreview) profilePlanPreview.textContent = String(data.subscription_plan || "Trial");
    if (profileStatusPreview) {
      const statusText = String(data.subscription_status || "active").toLowerCase() === "expired" ? "Expired" : "Active";
      profileStatusPreview.textContent = statusText;
      profileStatusPreview.classList.toggle("is-expired", statusText === "Expired");
    }
    if (profileExpiryPreview) {
      const rawDate = String(data.subscription_expires_on || "").trim();
      const formattedDate = rawDate ? formatScheduleDateTime(`${rawDate}T00:00:00`) : "-";
      profileExpiryPreview.textContent = `Plan expiry: ${formattedDate || "-"}`;
    }
    if (profileMemberSince) {
      profileMemberSince.textContent = `Member since: ${data.member_since || "-"}`;
    }

    const initials = profileInitials(firstName, lastName, email);
    if (profileAvatarFallback) profileAvatarFallback.textContent = initials;
    if (profileAvatarPreview) {
      if (avatarUrl) {
        profileAvatarPreview.src = avatarUrl;
        profileAvatarPreview.hidden = false;
        if (profileAvatarFallback) profileAvatarFallback.hidden = true;
      } else {
        profileAvatarPreview.hidden = true;
        if (profileAvatarFallback) profileAvatarFallback.hidden = false;
      }
    }

    // Stats tiles
    const stats = data.stats || {};
    if (statConnectedAccounts) statConnectedAccounts.textContent = data.connected_accounts ?? "-";
    if (statAccountBreakdown) {
      statAccountBreakdown.textContent = `FB: ${data.fb_accounts ?? 0} | IG: ${data.ig_accounts ?? 0}`;
    }
    if (statPublished) statPublished.textContent = stats.published ?? "-";
    if (statTotalScheduled) statTotalScheduled.textContent = `${stats.total_scheduled ?? 0} total scheduled`;
    if (statPending) statPending.textContent = stats.pending ?? "-";
    if (statFailed) statFailed.textContent = stats.failed ?? "-";
    if (statDaysLeft) statDaysLeft.textContent = data.days_left ?? "-";
    if (statMemberSince) statMemberSince.textContent = data.member_since || "-";
  }

  function applyProfileFormData(data) {
    if (!data) return;
    if (profileEmail) profileEmail.value = String(data.email || "");
    if (profileFirstName) profileFirstName.value = String(data.first_name || "");
    if (profileLastName) profileLastName.value = String(data.last_name || "");
    if (profilePictureUrl) profilePictureUrl.value = String(data.profile_picture_url || "");
    if (profilePlan) profilePlan.value = String(data.subscription_plan || "Trial");
    if (profilePlanStatus) profilePlanStatus.value = String(data.subscription_status || "active");
    if (profilePlanExpiry) profilePlanExpiry.value = String(data.subscription_expires_on || "");
    setProfilePreview(data);
  }

  async function loadProfileData() {
    if (!profileForm) return;
    try {
      const data = await fetchJSON("/dashboard/profile-data/");
      applyProfileFormData(data);
      if (profileResult) profileResult.textContent = "";
    } catch (err) {
      if (profileResult) profileResult.textContent = `Error: ${err.message}`;
    }
  }

  if (profileForm && saveProfileBtn) {
    const runWithProfileLoading = withButtonLoading(saveProfileBtn, "Save Profile", "Saving...");
    if (profileAvatarPreview && profileAvatarFallback) {
      profileAvatarPreview.addEventListener("error", () => {
        profileAvatarPreview.hidden = true;
        profileAvatarFallback.hidden = false;
      });
    }

    [profileFirstName, profileLastName]
      .filter(Boolean)
      .forEach((element) => {
        element.addEventListener("input", () => {
          setProfilePreview({
            first_name: profileFirstName?.value || "",
            last_name: profileLastName?.value || "",
            email: profileEmail?.value || "",
            profile_picture_url: profilePictureUrl?.value || "",
            subscription_plan: profilePlan?.value || "",
            subscription_status: profilePlanStatus?.value || "active",
            subscription_expires_on: profilePlanExpiry?.value || "",
          });
        });
      });

    profileForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(profileForm);
      const payload = {
        first_name: String(formData.get("first_name") || "").trim(),
        last_name: String(formData.get("last_name") || "").trim(),
      };
      try {
        const data = await runWithProfileLoading(() =>
          fetchJSON("/dashboard/profile-data/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify(payload),
          })
        );
        applyProfileFormData(data);
        if (profileResult) profileResult.textContent = String(data.message || "Profile updated.");
        showAppToast(String(data.message || "Profile updated successfully."), "success");
      } catch (err) {
        if (profileResult) profileResult.textContent = `Error: ${err.message}`;
        showAppToast(`Profile update failed: ${err.message}`, "error");
      }
    });

    loadProfileData();
  }

  if (subscriptionShell) {
    const razorpayKey = String(subscriptionShell.dataset.razorpayKey || "").trim();
    const currency = String(subscriptionShell.dataset.currency || "INR").trim().toUpperCase();
    const initialPlan = String(subscriptionShell.dataset.currentPlan || "").trim().toLowerCase();
    const initialStatus = String(subscriptionShell.dataset.currentStatus || "").trim().toLowerCase();
    const isLocked = String(subscriptionShell.dataset.isLocked || "").trim().toLowerCase() === "true";
    const payButtons = Array.from(subscriptionShell.querySelectorAll(".subscription-pay-btn"));

    const updateSubscriptionButtons = (plan, status) => {
      const normalizedPlan = String(plan || "").trim().toLowerCase();
      const normalizedStatus = String(status || "").trim().toLowerCase();
      const isActive = normalizedStatus === "active";
      payButtons.forEach((button) => {
        const buttonPlan = String(button.dataset.plan || "").trim().toLowerCase();
        if (!buttonPlan) return;

        let shouldDisable = false;
        if (isLocked) {
          shouldDisable = false;
        } else if (isActive && normalizedPlan === "monthly" && buttonPlan === "monthly") {
          shouldDisable = true;
        } else if (isActive && normalizedPlan === "yearly") {
          shouldDisable = true;
        }

        button.disabled = shouldDisable;
        button.classList.toggle("is-disabled", shouldDisable);
        if (buttonPlan === "yearly") {
          const yearlyActive = isActive && normalizedPlan === "yearly";
          button.classList.toggle("is-yearly-highlight", !yearlyActive);
        }
        if (shouldDisable) {
          button.textContent = "Active Plan";
        } else if (buttonPlan === "monthly") {
          button.textContent = "Pay Monthly";
        } else if (buttonPlan === "yearly") {
          button.textContent = "Pay Yearly";
        }
      });
    };

    const setSubscriptionMessage = (message, isError = false) => {
      if (!subscriptionResult) return;
      subscriptionResult.textContent = String(message || "");
      subscriptionResult.classList.toggle("is-error", Boolean(isError));
      subscriptionResult.classList.toggle("is-success", !isError && Boolean(message));
    };

    const openRazorpayCheckout = async (plan, triggerButton) => {
      if (!razorpayKey) {
        const msg = "Razorpay not configured. Please set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env.";
        setSubscriptionMessage(msg, true);
        showAppToast(msg, "error");
        return;
      }
      if (typeof window.Razorpay === "undefined") {
        const msg = "Razorpay checkout SDK failed to load. Refresh and try again.";
        setSubscriptionMessage(msg, true);
        showAppToast(msg, "error");
        return;
      }

      const defaultText = triggerButton ? triggerButton.textContent : "Pay Now";
      if (triggerButton) {
        triggerButton.disabled = true;
        triggerButton.textContent = "Creating Order...";
      }

      try {
        const orderData = await fetchJSON("/dashboard/subscription/create-order/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          body: JSON.stringify({ plan }),
        });

        if (!orderData || !orderData.order_id) {
          throw new Error("Invalid order response from Razorpay.");
        }

        const checkout = new window.Razorpay({
          key: String(orderData.razorpay_key_id || razorpayKey),
          amount: Number(orderData.amount || 0),
          currency: String(orderData.currency || currency),
          name: "Postzyo",
          description: String(orderData.plan_title || "Subscription"),
          order_id: String(orderData.order_id),
          prefill: orderData.prefill || {},
          notes: {
            plan: String(orderData.plan || plan),
            price_label: String(orderData.price_label || ""),
          },
          theme: {
            color: "#1a4d68",
          },
          handler: async (response) => {
            try {
              const verify = await fetchJSON("/dashboard/subscription/verify-payment/", {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify(response || {}),
              });
              const msg = String(verify.message || "Payment successful and verified.");
              setSubscriptionMessage(msg, false);
              showAppToast(msg, "success");
              if (verify.subscription) {
                updateSubscriptionButtons(verify.subscription.subscription_plan, verify.subscription.subscription_status);
              }
              window.setTimeout(() => {
                window.location.href = "/dashboard/subscription/";
              }, 900);
            } catch (err) {
              const msg = `Payment captured but verification failed: ${err.message}`;
              setSubscriptionMessage(msg, true);
              showAppToast(msg, "error");
            }
          },
          modal: {
            ondismiss: () => {
              setSubscriptionMessage("Payment popup closed. You can retry anytime.", false);
            },
          },
        });

        checkout.open();
      } catch (err) {
        const msg = `Unable to start payment: ${err.message}`;
        setSubscriptionMessage(msg, true);
        showAppToast(msg, "error");
      } finally {
        if (triggerButton) {
          triggerButton.disabled = false;
          triggerButton.textContent = defaultText;
        }
      }
    };

    payButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const plan = String(button.dataset.plan || "").trim().toLowerCase();
        if (!plan) return;
        openRazorpayCheckout(plan, button);
      });
    });

    updateSubscriptionButtons(initialPlan, initialStatus);
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
  const insightEarlyMonitor = document.getElementById("insightEarlyMonitor");
  const insightDistributionAlerts = document.getElementById("insightDistributionAlerts");
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
      { metric: "Followers", facebook: fbSummary.total_followers, instagram: igSummary.total_followers, window: "Current", group: "profile" },
      { metric: "Following", facebook: fbSummary.total_following, instagram: igSummary.total_following, window: "Current", group: "profile" },
      { metric: "Media / Posts", facebook: fbSummary.total_post_share, instagram: metricFromInsights(igInsights, ["media_count"]), window: "Current", group: "profile" },
      { metric: "Reach", facebook: metricFromInsights(fbInsights, ["page_reach"]), instagram: metricFromInsights(igInsights, ["reach"]), window: "Last 7 days", group: "engagement" },
      { metric: "Profile Views", facebook: metricFromInsights(fbInsights, ["page_views_total"]), instagram: metricFromInsights(igInsights, ["profile_views"]), window: "Last 7 days", group: "engagement" },
      { metric: "Accounts Engaged", facebook: metricFromInsights(fbInsights, ["page_engaged_users"]), instagram: metricFromInsights(igInsights, ["accounts_engaged"]), window: "Last 7 days", group: "engagement" },
      { metric: "Interactions", facebook: [fbRecentLikes, fbRecentComments, fbRecentShares].some((v) => v !== null) ? (fbRecentLikes || 0) + (fbRecentComments || 0) + (fbRecentShares || 0) : null, instagram: metricFromInsights(igInsights, ["total_interactions"]), window: "Last 7 days", group: "engagement" },
      { metric: "Views", facebook: metricFromInsights(fbInsights, ["page_impressions"]) ?? fbRecentViews, instagram: metricFromInsights(igInsights, ["views"]), window: "Last 7 days", group: "performance" },
      { metric: "Likes", facebook: fbRecentLikes, instagram: metricFromInsights(igInsights, ["likes"]), window: "Last 7 days", group: "performance" },
      { metric: "Comments", facebook: fbRecentComments, instagram: metricFromInsights(igInsights, ["comments"]), window: "Last 7 days", group: "performance" },
      { metric: "Shares", facebook: fbRecentShares, instagram: metricFromInsights(igInsights, ["shares"]) ?? igRecentShares, window: "Last 7 days", group: "performance" },
      { metric: "Saves", facebook: null, instagram: metricFromInsights(igInsights, ["saves"]) ?? igRecentSaves, window: "Last 7 days", group: "performance" },
      { metric: "New Followers", facebook: metricFromInsights(fbInsights, ["followers_count"]), instagram: metricFromInsights(igInsights, ["follower_count", "followers_count"]), window: "Last 7 days", group: "growth" },
      { metric: "New Follows", facebook: metricFromInsights(fbInsights, ["page_follows"]), instagram: metricFromInsights(igInsights, ["follows_count"]), window: "Current", group: "growth" },
    ];
  }

  function _cmpFmt(val) {
    if (val === null || val === undefined || val === "N/A" || val === "") return null;
    const n = Number(val);
    if (Number.isNaN(n)) return null;
    return n;
  }

  function _cmpDisplay(val) {
    const n = _cmpFmt(val);
    if (n === null) return '<span class="cmp-na">-</span>';
    if (n >= 1000000) return `<strong>${(n / 1000000).toFixed(1)}M</strong>`;
    if (n >= 1000) return `<strong>${(n / 1000).toFixed(1)}K</strong>`;
    return `<strong>${n.toLocaleString()}</strong>`;
  }

  function _cmpWinnerClass(fbVal, igVal) {
    const fb = _cmpFmt(fbVal);
    const ig = _cmpFmt(igVal);
    if (fb === null && ig === null) return { fbCls: "", igCls: "" };
    if (fb === null) return { fbCls: "", igCls: "cmp-winner" };
    if (ig === null) return { fbCls: "cmp-winner", igCls: "" };
    if (fb > ig) return { fbCls: "cmp-winner", igCls: "" };
    if (ig > fb) return { fbCls: "", igCls: "cmp-winner" };
    return { fbCls: "", igCls: "" };
  }

  function _cmpBar(fbVal, igVal) {
    const fb = _cmpFmt(fbVal) || 0;
    const ig = _cmpFmt(igVal) || 0;
    const max = Math.max(fb, ig, 1);
    const fbPct = Math.round((fb / max) * 100);
    const igPct = Math.round((ig / max) * 100);
    return `<div class="cmp-bar-track">
      <div class="cmp-bar cmp-bar-fb" style="width:${fbPct}%"></div>
      <div class="cmp-bar cmp-bar-ig" style="width:${igPct}%"></div>
    </div>`;
  }

  const _groupLabels = {
    profile: "\u{1F464} Profile Overview",
    engagement: "\u{1F4CA} Engagement (7 days)",
    performance: "\u{1F525} Post Performance (7 days)",
    growth: "\u{1F4C8} Growth",
  };

  function renderComparisonTable(container, rows, data) {
    if (!container) return;
    if (!rows || !rows.length) {
      container.innerHTML = "<p class='cmp-empty'>No comparison data available. Fetch insights first.</p>";
      return;
    }

    // Group rows
    const grouped = {};
    const groupOrder = ["profile", "engagement", "performance", "growth"];
    rows.forEach((row) => {
      const g = row.group || "other";
      if (!grouped[g]) grouped[g] = [];
      grouped[g].push(row);
    });

    let html = `<div class="cmp-header-row">
      <div class="cmp-header-metric">Metric</div>
      <div class="cmp-header-fb"><img class="cmp-platform-icon" src="/static/dashboard/brand/meta-logo.jpg" alt="FB"> Facebook</div>
      <div class="cmp-header-vs">vs</div>
      <div class="cmp-header-ig"><img class="cmp-platform-icon" src="/static/dashboard/brand/instagram-logo.webp" alt="IG"> Instagram</div>
      <div class="cmp-header-bar">Comparison</div>
    </div>`;

    groupOrder.forEach((groupKey) => {
      const groupRows = grouped[groupKey];
      if (!groupRows || !groupRows.length) return;
      const label = _groupLabels[groupKey] || groupKey;
      html += `<div class="cmp-group-header">${label}</div>`;
      groupRows.forEach((row) => {
        const { fbCls, igCls } = _cmpWinnerClass(row.facebook, row.instagram);
        const windowBadge = row.window ? `<span class="cmp-window">${escapeHtml(row.window)}</span>` : "";
        html += `<div class="cmp-row">
          <div class="cmp-cell-metric">${escapeHtml(row.metric)}${windowBadge}</div>
          <div class="cmp-cell-value ${fbCls}">${_cmpDisplay(row.facebook)}</div>
          <div class="cmp-cell-vs"></div>
          <div class="cmp-cell-value ${igCls}">${_cmpDisplay(row.instagram)}</div>
          <div class="cmp-cell-bar">${_cmpBar(row.facebook, row.instagram)}</div>
        </div>`;
      });
    });

    container.innerHTML = `<div class="cmp-table">${html}</div>`;
  }

  function isVideoUrl(url) {
    if (!url) return false;
    const clean = String(url).split("?")[0].toLowerCase();
    return clean.endsWith(".mp4") || clean.endsWith(".mov") || clean.endsWith(".webm") || clean.endsWith(".m4v");
  }

  function mediaPreviewHtml(url) {
    const safeUrl = sanitizeUrl(url);
    if (!safeUrl) return "<span class='media-empty'>No media</span>";
    if (isVideoUrl(safeUrl)) {
      return `<video class="media-preview" src="${escapeHtml(safeUrl)}" controls preload="metadata"></video>`;
    }
    return `<img class="media-preview" src="${escapeHtml(safeUrl)}" alt="post-media" loading="lazy" />`;
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
      const title = escapeHtml(err || "Metric unavailable");
      return `<span title="${title}">-</span>`;
    }

    const body = rows
      .map(
        (row) => `
          <tr>
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

  function renderCompactList(container, rows, emptyText) {
    if (!container) return;
    if (!Array.isArray(rows) || !rows.length) {
      container.innerHTML = `<p class="meta-note">${escapeHtml(emptyText)}</p>`;
      return;
    }
    const body = rows
      .map((row) => {
        const title = escapeHtml(String(row.title || "-"));
        const line = escapeHtml(String(row.line || ""));
        const tone = escapeHtml(String(row.tone || "neutral"));
        return `<li class="compact-list-item ${tone}"><strong>${title}</strong><span>${line}</span></li>`;
      })
      .join("");
    container.innerHTML = `<ul class="compact-list">${body}</ul>`;
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
    renderComparisonTable(insightMetricsTable, comparisonRows, data);

    const earlyRows = (Array.isArray(data.early_engagement_monitor) ? data.early_engagement_monitor : []).map((row) => ({
      title: `${String(row.platform || "").toUpperCase()} ${row.id}`,
      line: `${row.hours_since_publish ?? "-"}h | views: ${row.views ?? "-"} | likes: ${row.likes ?? "-"} | comments: ${
        row.comments ?? "-"
      } | status: ${row.status || "-"}`,
      tone: row.status === "weak" ? "bad" : row.status === "strong" ? "good" : "neutral",
    }));
    renderCompactList(insightEarlyMonitor, earlyRows, "No posts in first 6-hour watch window.");

    const alertRows = (Array.isArray(data.low_distribution_alerts) ? data.low_distribution_alerts : []).map((row) => ({
      title: `${String(row.platform || "").toUpperCase()} ${row.id}`,
      line: `views ${row.views ?? "-"} vs baseline ${row.baseline_views ?? "-"} | ${row.recommendation || ""}`,
      tone: "bad",
    }));
    renderCompactList(insightDistributionAlerts, alertRows, "No low-distribution alerts in recent window.");
  }

  function renderAiListCard(title, rows, tone, icon) {
    const items = Array.isArray(rows) ? rows.filter((item) => String(item || "").trim()) : [];
    const iconHtml = icon ? `<span class="ai-section-icon">${icon}</span>` : "";
    if (!items.length) {
      return `
        <article class="ai-report-card ${tone}">
          <h3>${iconHtml}${escapeHtml(title)}</h3>
          <p class="ai-empty">No specific items available.</p>
        </article>
      `;
    }
    return `
      <article class="ai-report-card ${tone}">
        <h3>${iconHtml}${escapeHtml(title)}</h3>
        <ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </article>
    `;
  }

  function renderAiContentIdeas(ideas) {
    const items = Array.isArray(ideas) ? ideas.filter((item) => String(item || "").trim()) : [];
    if (!items.length) {
      return `
        <article class="ai-report-card">
          <h3><span class="ai-section-icon">\u{1F4A1}</span>Content Ideas</h3>
          <p class="ai-empty">No content ideas generated.</p>
        </article>
      `;
    }
    return `
      <article class="ai-report-card">
        <h3><span class="ai-section-icon">\u{1F4A1}</span>Content Ideas</h3>
        <div class="ai-ideas-grid">${items.map((item) => `<div class="ai-idea-chip">${escapeHtml(item)}</div>`).join("")}</div>
      </article>
    `;
  }

  function renderAiPlanTable(title, rows, icon) {
    const safeRows = Array.isArray(rows) ? rows : [];
    const iconHtml = icon ? `<span class="ai-section-icon">${icon}</span>` : "";
    if (!safeRows.length) {
      return `
        <article class="ai-report-card">
          <h3>${iconHtml}${escapeHtml(title)}</h3>
          <p class="ai-empty">No detailed plan generated.</p>
        </article>
      `;
    }
    return `
      <article class="ai-report-card">
        <h3>${iconHtml}${escapeHtml(title)}</h3>
        <div class="table-wrap table-wrap-strong">
          <table class="ai-table">
            <tr>
              <th>Action</th>
              <th>Why</th>
              <th>Expected Impact</th>
              <th>Timeline</th>
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
          <h3><span class="ai-section-icon">\u{1F4C8}</span>7-Day KPI Growth Plan</h3>
          <p class="ai-empty">No KPI target table generated.</p>
        </article>
      `;
    }
    return `
      <article class="ai-report-card">
        <h3><span class="ai-section-icon">\u{1F4C8}</span>7-Day KPI Growth Plan</h3>
        <div class="table-wrap table-wrap-strong">
          <table class="ai-table">
            <tr>
              <th>Metric</th>
              <th>Current</th>
              <th>Target (7d)</th>
              <th>How to Achieve</th>
            </tr>
            ${safeRows
              .map(
                (row) => `
                  <tr>
                    <td><strong>${escapeHtml(row.metric)}</strong></td>
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

  function _fmtNum(val) {
    if (val === null || val === undefined || val === "-") return "-";
    const n = Number(val);
    if (Number.isNaN(n)) return String(val);
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "K";
    return String(n);
  }

  function renderAiInsights(data) {
    if (!aiInsightResult || !aiInsightMeta) return;
    const analysis = data && data.analysis ? data.analysis : {};
    const cadence = (data && data.source_overview && data.source_overview.posting_cadence) || {};
    const perf = (data && data.source_overview && data.source_overview.performance_last_7d) || {};
    const pageName = data.page_name || "Unknown Profile";
    const initial = pageName.charAt(0).toUpperCase();
    const platformLabel = (data.platform || "").replace("+", " + ");

    aiInsightMeta.textContent = `Snapshot: ${toIndianDateTime(data.fetched_at) || "-"} | Generated: ${
      toIndianDateTime(data.generated_at) || "-"
    } | ${data.cached ? "Cached" : "Fresh data"}`;

    const profileBanner = `
      <div class="ai-profile-banner">
        <div class="ai-profile-avatar">${escapeHtml(initial)}</div>
        <div class="ai-profile-info">
          <p class="ai-profile-name">${escapeHtml(pageName)}</p>
          <p class="ai-profile-platform">${escapeHtml(platformLabel || "Multi-platform")} ${data.combined ? " (Combined FB + IG)" : ""}</p>
        </div>
        <div class="ai-profile-badges">
          <span class="ai-badge ai-badge-model">${escapeHtml(data.model || "AI")}</span>
          <span class="ai-badge ai-badge-time">${escapeHtml(toIndianDateTime(data.generated_at) || "Just now")}</span>
        </div>
      </div>`;

    const statsBar = `
      <div class="ai-stats-bar">
        <div class="ai-stat-tile"><div class="ai-stat-value">${_fmtNum(perf.views)}</div><div class="ai-stat-label">Views (7d)</div></div>
        <div class="ai-stat-tile"><div class="ai-stat-value">${_fmtNum(perf.likes)}</div><div class="ai-stat-label">Likes (7d)</div></div>
        <div class="ai-stat-tile"><div class="ai-stat-value">${_fmtNum(perf.comments)}</div><div class="ai-stat-label">Comments (7d)</div></div>
        <div class="ai-stat-tile"><div class="ai-stat-value">${_fmtNum(perf.shares)}</div><div class="ai-stat-label">Shares (7d)</div></div>
        <div class="ai-stat-tile"><div class="ai-stat-value">${_fmtNum(perf.saves)}</div><div class="ai-stat-label">Saves (7d)</div></div>
      </div>`;

    const cadenceBar = `
      <div class="ai-cadence-bar">
        <span class="ai-cadence-chip"><strong>${cadence.posts_last_24h ?? "-"}</strong> posts today</span>
        <span class="ai-cadence-chip"><strong>${cadence.posts_last_7d ?? "-"}</strong> posts (7d)</span>
        <span class="ai-cadence-chip"><strong>${cadence.posts_last_30d ?? "-"}</strong> posts (30d)</span>
        <span class="ai-cadence-chip">FB <strong>${cadence.facebook_avg_posts_per_day_last_7d ?? "-"}</strong>/day</span>
        <span class="ai-cadence-chip">IG <strong>${cadence.instagram_avg_posts_per_day_last_7d ?? "-"}</strong>/day</span>
      </div>`;

    const execSummary = `
      <div class="ai-report-header">
        <h3><span class="ai-section-icon">\u{1F4CB}</span>Executive Summary</h3>
        <p>${escapeHtml(analysis.executive_summary || "No summary generated.")}</p>
      </div>`;

    const strategy = analysis.posting_strategy || {};
    const strategyCard = `
      <div class="ai-strategy-card">
        <h3><span class="ai-section-icon">\u{1F3AF}</span>Posting Strategy</h3>
        <div class="ai-strategy-row">
          <div class="ai-strategy-label">Current Approach</div>
          <div class="ai-strategy-value">${escapeHtml(strategy.current_posting || "Not specified")}</div>
        </div>
        <div class="ai-strategy-row">
          <div class="ai-strategy-label">Recommended Change</div>
          <div class="ai-strategy-value">${escapeHtml(strategy.recommended_posting || "Not specified")}</div>
        </div>
        <div class="ai-strategy-row">
          <div class="ai-strategy-label">Data-Backed Reasoning</div>
          <div class="ai-strategy-value">${escapeHtml(strategy.reasoning || "Not specified")}</div>
        </div>
      </div>`;

    const nbp = analysis.next_best_post || {};
    const nextPostCard = `
      <div class="ai-next-post-card">
        <h3><span class="ai-section-icon">\u{1F680}</span>Your Next Best Post</h3>
        <div class="ai-next-post-grid">
          <div class="ai-next-post-item">
            <div class="ai-strategy-label">Best Time Window</div>
            <div class="ai-strategy-value">${escapeHtml(nbp.best_time_window || "Not specified")}</div>
          </div>
          <div class="ai-next-post-item">
            <div class="ai-strategy-label">Recommended Format</div>
            <div class="ai-strategy-value">${escapeHtml(nbp.recommended_format || "Not specified")}</div>
          </div>
          <div class="ai-next-post-item">
            <div class="ai-strategy-label">Recommended Topic</div>
            <div class="ai-strategy-value">${escapeHtml(nbp.recommended_topic || "Not specified")}</div>
          </div>
          <div class="ai-next-post-item">
            <div class="ai-strategy-label">Why Now</div>
            <div class="ai-strategy-value">${escapeHtml(nbp.why_now || "Not specified")}</div>
          </div>
        </div>
      </div>`;

    aiInsightResult.innerHTML = `
      ${profileBanner}
      ${statsBar}
      ${cadenceBar}
      ${execSummary}
      <div class="ai-report-grid-4">
        ${renderAiListCard("Strengths", analysis.pros || [], "tone-good", "\u{2705}")}
        ${renderAiListCard("Weaknesses", analysis.cons || [], "tone-bad", "\u{26A0}\u{FE0F}")}
        ${renderAiListCard("Risks", analysis.risks || [], "tone-warn", "\u{1F6A8}")}
        ${renderAiListCard("Opportunities", analysis.opportunities || [], "tone-blue", "\u{1F4A0}")}
      </div>
      <div class="ai-report-grid">
        ${renderAiListCard("What Worked", analysis.what_worked || [], "tone-good", "\u{1F44D}")}
        ${renderAiListCard("What Flopped", analysis.what_flopped || [], "tone-bad", "\u{1F44E}")}
      </div>
      ${strategyCard}
      ${nextPostCard}
      ${renderAiPlanTable("7-Day Action Plan", analysis.action_plan_7d || [], "\u{1F4C5}")}
      ${renderAiKpiTable(analysis.kpi_growth_plan || [])}
      ${renderAiContentIdeas(analysis.content_ideas || [])}
      ${renderAiListCard("Top Growth Recommendations", analysis.best_recommendations_for_growth || [], "tone-good", "\u{1F31F}")}
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
