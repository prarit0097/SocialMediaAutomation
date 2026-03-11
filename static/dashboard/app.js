(function () {
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
      throw new Error(typeof data === "string" ? data : JSON.stringify(data));
    }
    return data;
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

  async function loadAccounts() {
    const table = document.getElementById("accountsTable");
    const syncStatus = document.getElementById("accountSyncStatus");
    const catalogTable = document.getElementById("metaCatalogTable");
    const catalogStatus = document.getElementById("metaCatalogStatus");
    if (!table) return;
    try {
      const [rows, status, catalog] = await Promise.all([
        fetchJSON("/api/accounts/"),
        fetchJSON("/api/accounts/sync-status/"),
        fetchJSON("/api/accounts/meta-pages/"),
      ]);
      renderTable(table, rows);
      if (syncStatus) {
        const syncedAt = status.synced_at ? toIndianDateTime(status.synced_at) : "N/A";
        const metaPages = status.meta_pages_synced ?? "N/A";
        const targetIds = status.token_target_ids_count ?? "N/A";
        const fbTotal = status.facebook_connected_total ?? rows.filter((r) => r.platform === "facebook").length;
        const igTotal = status.instagram_connected_total ?? rows.filter((r) => r.platform === "instagram").length;
        const warning = status.warning ? ` | Warning: ${status.warning}` : "";
        syncStatus.textContent = `Last Sync: ${syncedAt} | Meta Pages Synced: ${metaPages} | Token Target IDs: ${targetIds} | Connected FB: ${fbTotal} | Connected IG: ${igTotal}${warning}`;
      }
      if (catalogTable) {
        renderTable(catalogTable, catalog.rows || []);
      }
      if (catalogStatus) {
        const connected = catalog.connected_pages ?? rows.filter((r) => r.platform === "facebook").length;
        const total = catalog.total_pages ?? connected;
        catalogStatus.textContent = `Catalog Total: ${total} | Connected in App: ${connected} | Catalog-only: ${
          Math.max(0, total - connected)
        }`;
      }
    } catch (err) {
      table.innerHTML = `<p>${err.message}</p>`;
      if (syncStatus) syncStatus.textContent = "";
      if (catalogTable) catalogTable.innerHTML = "";
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
    refreshAccountsBtn.addEventListener("click", () => runWithRefreshAccountsLoading(loadAccounts));
    loadAccounts();
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
    const scheduleSubmitBtn = scheduleForm.querySelector("button[type='submit']");
    const runWithScheduleLoading = withButtonLoading(scheduleSubmitBtn, "Schedule Post", "Scheduling...");
    scheduleForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(scheduleForm);

      const payload = {
        account_id: Number(formData.get("account_id")),
        platform: formData.get("platform"),
        message: formData.get("message") || undefined,
        media_url: formData.get("media_url") || undefined,
        scheduled_for: new Date(formData.get("scheduled_for")).toISOString(),
      };

      const resultEl = document.getElementById("scheduleResult");
      try {
        const data = await runWithScheduleLoading(() =>
          fetchJSON("/api/posts/schedule/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify(payload),
          })
        );
        resultEl.textContent = `Scheduled: #${data.id}`;
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

    const head = `
      <tr>
        <th>id</th>
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

  function renderInsights(data) {
    if (!data) return;
    if (insightError) insightError.textContent = "";

    const summary = data.summary || {};
    setInsightValue(totalFollowers, summary.total_followers);
    setInsightValue(totalFollowing, summary.total_following);
    setInsightValue(totalPostShare, summary.total_post_share);

    if (insightMeta) {
      const fetchedAt = toIndianDateTime(data.fetched_at);
      insightMeta.textContent = `Account ID: ${data.account_id || "-"} | Platform: ${
        data.platform || "-"
      } | Snapshot: ${data.snapshot_id || "-"} | Fetched: ${fetchedAt || "-"} | Cached: ${data.cached ? "Yes" : "No"}`;
    }
    if (insightPageHero && insightPageName) {
      insightPageName.textContent = data.page_name || "-";
      insightPageHero.hidden = false;
    }

    const publishedPosts = (data.published_posts || []).map((row) => ({
      ...row,
      scheduled_for: toIndianDateTime(row.scheduled_for),
      published_at: toIndianDateTime(row.published_at),
    }));
    renderPostsTable(insightPostsTable, publishedPosts);

    const metrics = (data.insights || []).map((metric) => ({
      metric: metric.name,
      value: metric.values && metric.values[0] ? metric.values[0].value : "",
      title: metric.title || "",
      period: metric.period || "",
    }));
    renderTable(insightMetricsTable, metrics);
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
})();
