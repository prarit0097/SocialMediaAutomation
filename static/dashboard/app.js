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
    const response = await fetch(url, options);
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
    if (value === "fb_ig") return "<span class='platform-badge both'>FB_IG</span>";
    if (value === "ig" || value === "instagram") return "<span class='platform-badge instagram'>IG</span>";
    return "<span class='platform-badge facebook'>FB</span>";
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

      merged.push({
        profile_name: cleanProfileName(linkedIg?.page_name || row.page_name),
        account_id: Number(row.id),
        platform: linkedIg ? "fb_ig" : "fb",
        page_id: String(row.page_id || ""),
        ig_user_id: linkedIg ? String(linkedIg.page_id || "") : "",
        created_at: createdAt,
        updated_at: updatedAt,
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
        account_id: Number(row.id),
        platform: "ig",
        page_id: "",
        ig_user_id: String(row.page_id || ""),
        created_at: row.created_at,
        updated_at: row.updated_at,
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
      const bag = [row.account_id, row.platform, row.profile_name, row.page_id, row.ig_user_id]
        .map((v) => String(v ?? "").toLowerCase())
        .join(" ");
      return bag.includes(query);
    });
  }

  function updateAccountsViewMeta(filteredRows, totalRows) {
    const meta = document.getElementById("accountsViewMeta");
    if (!meta) return;
    const fbOnly = filteredRows.filter((r) => String(r.platform).toLowerCase() === "fb").length;
    const igOnly = filteredRows.filter((r) => String(r.platform).toLowerCase() === "ig").length;
    const both = filteredRows.filter((r) => String(r.platform).toLowerCase() === "fb_ig").length;
    meta.textContent = `Showing: ${filteredRows.length}/${totalRows} | FB only: ${fbOnly} | IG only: ${igOnly} | FB_IG: ${both}`;
  }

  function renderAccountsTable(container, rows, totalRows) {
    if (!container) return;
    if (!rows.length) {
      container.innerHTML = "<p>No records found.</p>";
      updateAccountsViewMeta([], totalRows || 0);
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
        <th>updated_at</th>
        <th>actions</th>
      </tr>
    `;

    const avatarResolver = buildAvatarResolver(cachedAccountsRows);
    const body = rows
      .map((row, index) => {
        const createdAt = row.created_at ? toIndianDateTime(row.created_at) : "-";
        const updatedAt = row.updated_at ? toIndianDateTime(row.updated_at) : "-";
        const schedulePlatform = row.platform === "fb_ig" ? "both" : row.platform === "ig" ? "instagram" : "facebook";
        const scheduleAccountId = row.platform === "ig" ? row.ig_account_id || row.account_id : row.fb_account_id || row.account_id;
        const schedulerUrl = `/dashboard/scheduler/?account_id=${encodeURIComponent(scheduleAccountId)}&platform=${encodeURIComponent(
          schedulePlatform
        )}`;
        const insightsUrl = `/dashboard/insights/?account_id=${encodeURIComponent(row.insight_account_id || row.account_id)}`;
        return `
          <tr>
            <td>${index + 1}</td>
            <td>${avatarHtml({ ...row, page_name: row.profile_name }, avatarResolver)}</td>
            <td>${escapeHtml(row.account_id)}</td>
            <td>${platformBadge(row.platform)}</td>
            <td>${escapeHtml(row.page_id)}</td>
            <td>${escapeHtml(row.ig_user_id || "")}</td>
            <td>${escapeHtml(createdAt)}</td>
            <td>${escapeHtml(updatedAt)}</td>
            <td>
              <a class="inline-link-btn" href="${schedulerUrl}">Schedule</a>
              <a class="inline-link-btn muted" href="${insightsUrl}">Insights</a>
            </td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `<table>${head}${body}</table>`;
    updateAccountsViewMeta(rows, totalRows || rows.length);
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

  function renderCatalogTable(container, rows, connectedRows) {
    if (!container) return;
    if (!rows.length) {
      container.innerHTML = "<p>No records found.</p>";
      return;
    }

    const avatarResolver = buildAvatarResolver(connectedRows || []);
    const head = `
      <tr>
        <th>#</th>
        <th>profile</th>
        <th>platform</th>
        <th>page_id</th>
        <th>status</th>
        <th>connectability</th>
        <th>reason</th>
        <th>insights</th>
      </tr>
    `;
    const body = rows
      .map((row, index) => {
        const connectability = String(row.connectability || "").toLowerCase();
        const status = String(row.status || "").toLowerCase();
        const pageTokenStatus = String(row.page_token_status || row.connection_status || "").toLowerCase();
        const platform = guessCatalogPlatform(row, connectedRows);
        const insightsAvailable =
          status === "connected" ||
          connectability === "connected" ||
          (connectability === "connectable" && (pageTokenStatus === "connected" || pageTokenStatus === "synced"));
        const reason =
          row.reason ||
          "Meta did not return page access token. Connect this page in Business Integrations and reconnect.";
        const badge = insightsAvailable
          ? "<span class='status-badge ok'>Available</span>"
          : `<span class='status-badge warn' title='${escapeHtml(reason)}'>Unavailable</span>`;

        return `
          <tr>
            <td>${index + 1}</td>
            <td>${avatarHtml(
              { page_id: row.page_id, ig_user_id: row.ig_user_id, page_name: row.page_name || "(name unavailable)", platform },
              avatarResolver
            )}</td>
            <td>${platformBadge(platform)}</td>
            <td>${escapeHtml(row.page_id)}</td>
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
    renderAccountsTable(table, filtered, mergedRows.length);
  }

  async function loadAccounts(options = {}) {
    const table = document.getElementById("accountsTable");
    const syncStatus = document.getElementById("accountSyncStatus");
    const catalogTable = document.getElementById("metaCatalogTable");
    const catalogStatus = document.getElementById("metaCatalogStatus");
    const refreshCatalog = options.refreshCatalog === true;
    if (!table) return;
    let rows = [];
    try {
      // Primary table should load fast and independently.
      rows = await fetchJSON("/api/accounts/");
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
      if (catalogTable) {
        renderCatalogTable(catalogTable, catalog.rows || [], rows);
      }
      if (catalogStatus) {
        const connected = catalog.connected_pages ?? rows.filter((r) => r.platform === "facebook").length;
        const total = catalog.total_pages ?? connected;
        const connectable = (catalog.rows || []).filter((r) => r.connectability === "connectable").length;
        const notConnectable = (catalog.rows || []).filter((r) => r.connectability === "not_connectable").length;
        catalogStatus.textContent = `Catalog Total: ${total} | Connected in App: ${connected} | Catalog-only: ${
          Math.max(0, total - connected)
        } | Connectable: ${connectable} | Not Connectable: ${notConnectable}`;
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
  if (refreshAccountsBtn) {
    const runWithRefreshAccountsLoading = withButtonLoading(refreshAccountsBtn, "Refresh List", "Refreshing...");
    refreshAccountsBtn.addEventListener("click", () => runWithRefreshAccountsLoading(() => loadAccounts()));
    loadAccounts();
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
        await loadScheduledPosts();
      } catch (err) {
        resultEl.textContent = `Error: ${err.message}`;
      }
    });
  }

  const fetchInsightsBtn = document.getElementById("fetchInsightsBtn");
  const refreshInsightsBtn = document.getElementById("refreshInsightsBtn");
  const insightAccountId = document.getElementById("insightAccountId");
  const insightError = document.getElementById("insightError");
  const publicUrlStatus = document.getElementById("publicUrlStatus");
  const totalFollowers = document.getElementById("totalFollowers");
  const totalFollowing = document.getElementById("totalFollowing");
  const totalPostShare = document.getElementById("totalPostShare");
  const insightMeta = document.getElementById("insightMeta");
  const insightPageHero = document.getElementById("insightPageHero");
  const insightPageName = document.getElementById("insightPageName");
  const insightPostsTable = document.getElementById("insightPostsTable");
  const insightMetricsTable = document.getElementById("insightMetricsTable");

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

  function setInsightValue(element, value) {
    if (!element) return;
    element.textContent = value === null || value === undefined || value === "" ? "N/A" : String(value);
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
            <td>${escapeHtml(row.message)}</td>
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
      insightError.textContent = data.warning ? String(data.warning) : "";
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
      if (data.combined && Array.isArray(data.accounts)) {
        const ids = data.accounts.map((row) => `${row.platform}:${row.account_id}`).join(", ");
        insightMeta.textContent = `Accounts: ${ids} | Platform: ${data.platform || "-"} | Snapshot: ${
          data.snapshot_id || "-"
        } | Fetched: ${fetchedAt || "-"} | Cached: ${data.cached ? "Yes" : "No"}`;
      } else {
        insightMeta.textContent = `Account ID: ${data.account_id || "-"} | Platform: ${
          data.platform || "-"
        } | Snapshot: ${data.snapshot_id || "-"} | Fetched: ${fetchedAt || "-"} | Cached: ${data.cached ? "Yes" : "No"}`;
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

    const publishedPosts = (data.published_posts || []).map((row) => ({
      ...row,
      scheduled_for: toIndianDateTime(row.scheduled_for),
      published_at: toIndianDateTime(row.published_at),
    }));
    renderPostsTable(insightPostsTable, publishedPosts);

    function metricDisplayValue(metric) {
      if (metric && metric.total_value && typeof metric.total_value === "object" && metric.total_value.value !== undefined) {
        return metric.total_value.value;
      }
      if (metric && Array.isArray(metric.values) && metric.values.length && metric.values[0] && metric.values[0].value !== undefined) {
        return metric.values[0].value;
      }
      return "";
    }

    const metrics = (data.insights || []).map((metric) => {
      const row = {
        metric: metric.name,
        value: metricDisplayValue(metric),
        title: metric.title || "",
        period: metric.period || "",
      };
      if (metric.platform) row.platform = metric.platform;
      return row;
    });
    renderTable(insightMetricsTable, metrics);
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
      return;
    }

    const suffix = forceRefresh ? "?refresh=1" : "";
    try {
      const data = await fetchJSON(`/api/insights/${accountId}/${suffix}`);
      renderInsights(data);
    } catch (err) {
      if (insightError) insightError.textContent = err.message;
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
})();
