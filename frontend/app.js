const API_BASE = "http://localhost:4000";

const accountsDiv = document.getElementById("accounts");
const scheduledDiv = document.getElementById("scheduled");
const insightsPre = document.getElementById("insights");
const scheduleResult = document.getElementById("scheduleResult");

function toTable(items) {
  if (!items?.length) {
    return "<p>No data</p>";
  }

  const headers = Object.keys(items[0]);
  const head = `<tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr>`;
  const body = items
    .map((row) => `<tr>${headers.map((h) => `<td>${row[h] ?? ""}</td>`).join("")}</tr>`)
    .join("");

  return `<table>${head}${body}</table>`;
}

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

async function refreshAccounts() {
  const accounts = await getJson("/auth/accounts");
  accountsDiv.innerHTML = toTable(accounts);
}

async function refreshScheduled() {
  const scheduled = await getJson("/posts/scheduled");
  scheduledDiv.innerHTML = toTable(scheduled);
}

document.getElementById("connectBtn").addEventListener("click", async () => {
  const data = await getJson("/auth/meta/start");
  window.open(data.authUrl, "_blank");
});

document.getElementById("refreshAccountsBtn").addEventListener("click", async () => {
  try {
    await refreshAccounts();
  } catch (e) {
    accountsDiv.innerHTML = `<p>${e.message}</p>`;
  }
});

document.getElementById("refreshScheduledBtn").addEventListener("click", async () => {
  try {
    await refreshScheduled();
  } catch (e) {
    scheduledDiv.innerHTML = `<p>${e.message}</p>`;
  }
});

document.getElementById("scheduleForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  const payload = {
    accountId: Number(document.getElementById("accountId").value),
    platform: document.getElementById("platform").value,
    message: document.getElementById("message").value || undefined,
    mediaUrl: document.getElementById("mediaUrl").value || undefined,
    scheduledFor: new Date(document.getElementById("scheduledFor").value).toISOString()
  };

  const res = await fetch(`${API_BASE}/posts/schedule`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const json = await res.json();
  if (!res.ok) {
    scheduleResult.textContent = `Error: ${JSON.stringify(json)}`;
    return;
  }

  scheduleResult.textContent = `Scheduled with ID ${json.id}`;
  await refreshScheduled();
});

document.getElementById("fetchInsightsBtn").addEventListener("click", async () => {
  try {
    const accountId = Number(document.getElementById("insightAccountId").value);
    const data = await getJson(`/insights/${accountId}`);
    insightsPre.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    insightsPre.textContent = e.message;
  }
});

refreshAccounts().catch(() => {
  accountsDiv.innerHTML = "<p>Unable to load accounts yet.</p>";
});
refreshScheduled().catch(() => {
  scheduledDiv.innerHTML = "<p>Unable to load scheduled posts yet.</p>";
});
