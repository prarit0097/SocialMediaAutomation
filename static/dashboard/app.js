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
      renderTable(table, rows);
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
  const insightOutput = document.getElementById("insightsOutput");
  const insightAccountId = document.getElementById("insightAccountId");

  async function loadInsights(forceRefresh) {
    if (!insightOutput || !insightAccountId) return;
    const accountId = Number(insightAccountId.value);
    if (!accountId) {
      insightOutput.textContent = "Enter valid account id";
      return;
    }

    const suffix = forceRefresh ? "?refresh=1" : "";
    try {
      const data = await fetchJSON(`/api/insights/${accountId}/${suffix}`);
      insightOutput.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      insightOutput.textContent = err.message;
    }
  }

  if (fetchInsightsBtn) {
    fetchInsightsBtn.addEventListener("click", () => loadInsights(false));
  }
  if (refreshInsightsBtn) {
    refreshInsightsBtn.addEventListener("click", () => loadInsights(true));
  }
})();
