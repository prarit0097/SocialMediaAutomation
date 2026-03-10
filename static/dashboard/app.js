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

  async function loadAccounts() {
    const table = document.getElementById("accountsTable");
    if (!table) return;
    try {
      const rows = await fetchJSON("/api/accounts/");
      renderTable(table, rows);
    } catch (err) {
      table.innerHTML = `<p>${err.message}</p>`;
    }
  }

  async function loadScheduledPosts() {
    const table = document.getElementById("scheduledTable");
    if (!table) return;
    try {
      const rows = await fetchJSON("/api/posts/scheduled/");
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
        };
      });
      renderTable(table, rowsWithLocalTime);
    } catch (err) {
      table.innerHTML = `<p>${err.message}</p>`;
    }
  }

  const connectBtn = document.getElementById("connectMetaBtn");
  if (connectBtn) {
    connectBtn.addEventListener("click", async () => {
      const data = await fetchJSON("/auth/meta/start");
      window.location.href = data.auth_url;
    });
  }

  const refreshAccountsBtn = document.getElementById("refreshAccountsBtn");
  if (refreshAccountsBtn) {
    refreshAccountsBtn.addEventListener("click", loadAccounts);
    loadAccounts();
  }

  const refreshScheduledBtn = document.getElementById("refreshScheduledBtn");
  if (refreshScheduledBtn) {
    refreshScheduledBtn.addEventListener("click", loadScheduledPosts);
    loadScheduledPosts();
  }

  const scheduleForm = document.getElementById("scheduleForm");
  if (scheduleForm) {
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
        const data = await fetchJSON("/api/posts/schedule/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          body: JSON.stringify(payload),
        });
        resultEl.textContent = `Scheduled: #${data.id}`;
        loadScheduledPosts();
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

  function renderInsights(data) {
    if (!data) return;
    if (insightError) insightError.textContent = "";

    const summary = data.summary || {};
    setInsightValue(totalFollowers, summary.total_followers);
    setInsightValue(totalFollowing, summary.total_following);
    setInsightValue(totalPostShare, summary.total_post_share);

    if (insightMeta) {
      const fetchedAt = toIndianDateTime(data.fetched_at);
      insightMeta.textContent = `Platform: ${data.platform || "-"} | Snapshot: ${data.snapshot_id || "-"} | Fetched: ${
        fetchedAt || "-"
      } | Cached: ${data.cached ? "Yes" : "No"}`;
    }

    const publishedPosts = (data.published_posts || []).map((row) => ({
      ...row,
      scheduled_for: toIndianDateTime(row.scheduled_for),
      published_at: toIndianDateTime(row.published_at),
    }));
    renderTable(insightPostsTable, publishedPosts);

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
    fetchInsightsBtn.addEventListener("click", () => loadInsights(false));
  }
  if (refreshInsightsBtn) {
    refreshInsightsBtn.addEventListener("click", () => loadInsights(true));
  }
})();
