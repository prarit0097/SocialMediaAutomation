(() => {
  const app = document.getElementById("planningApp");
  if (!app) return;

  const csrfToken = document.querySelector("[name='csrfmiddlewaretoken']")?.value || "";
  const monthLabel = document.getElementById("planningMonthLabel");
  const grid = document.getElementById("planningGrid");
  const prevBtn = document.getElementById("planningPrevMonth");
  const nextBtn = document.getElementById("planningNextMonth");
  const reloadBtn = document.getElementById("planningReloadBtn");
  const createForm = document.getElementById("planningCreateForm");
  const tagForm = document.getElementById("planningTagForm");
  const tagsList = document.getElementById("planningTagsList");
  const aiForm = document.getElementById("planningAiForm");
  const aiStatus = document.getElementById("planningAiStatus");
  const aiResult = document.getElementById("planningAiResult");

  let cursor = new Date();
  cursor.setDate(1);
  let items = [];

  const pad = (n) => String(n).padStart(2, "0");
  const monthKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
  const fmtLocalInput = (iso) => {
    const d = new Date(iso);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.error || body.details || "Request failed");
    }
    return body;
  }

  function statusClass(value) {
    return `status-${String(value || "draft").toLowerCase()}`;
  }

  function renderCalendar() {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    monthLabel.textContent = cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" });

    const firstDay = new Date(year, month, 1);
    const startWeekday = firstDay.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    grid.innerHTML = "";

    for (let i = 0; i < 42; i++) {
      const dateNum = i - startWeekday + 1;
      const cellDate = new Date(year, month, dateNum);
      const inMonth = dateNum >= 1 && dateNum <= daysInMonth;
      const cell = document.createElement("div");
      cell.className = `planning-cell ${inMonth ? "" : "is-muted"}`.trim();
      cell.dataset.date = `${cellDate.getFullYear()}-${pad(cellDate.getMonth() + 1)}-${pad(cellDate.getDate())}`;
      cell.innerHTML = `<div class="planning-cell-date">${cellDate.getDate()}</div><div class="planning-cell-items"></div>`;

      const itemsWrap = cell.querySelector(".planning-cell-items");
      if (inMonth) {
        const cellItems = items.filter((item) => {
          const d = new Date(item.start_at);
          return d.getFullYear() === year && d.getMonth() === month && d.getDate() === cellDate.getDate();
        });

        cellItems.forEach((item) => {
          const badge = document.createElement("button");
          badge.type = "button";
          badge.className = `planning-item ${statusClass(item.status)}`;
          badge.draggable = true;
          badge.dataset.id = String(item.id);
          badge.title = `${item.title}\n${item.platform} | ${item.status}`;
          badge.textContent = `${item.title}`;
          badge.addEventListener("dragstart", (ev) => {
            ev.dataTransfer.setData("text/plain", String(item.id));
          });
          itemsWrap.appendChild(badge);
        });

        cell.addEventListener("dragover", (ev) => ev.preventDefault());
        cell.addEventListener("drop", async (ev) => {
          ev.preventDefault();
          const id = Number(ev.dataTransfer.getData("text/plain"));
          if (!id) return;
          const targetDate = cell.dataset.date;
          const source = items.find((it) => it.id === id);
          if (!source || !targetDate) return;
          const sourceDate = new Date(source.start_at);
          const nextStart = `${targetDate}T${pad(sourceDate.getHours())}:${pad(sourceDate.getMinutes())}:00`;
          try {
            const updated = await fetchJSON(`/api/planning/calendar/${id}/`, {
              method: "PATCH",
              headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken,
              },
              body: JSON.stringify({ start_at: nextStart }),
            });
            items = items.map((it) => (it.id === id ? updated : it));
            renderCalendar();
          } catch (err) {
            alert(`Move failed: ${err.message}`);
          }
        });
      }

      grid.appendChild(cell);
    }
  }

  async function loadItems() {
    const payload = await fetchJSON(`/api/planning/calendar/?month=${monthKey(cursor)}`);
    items = payload.items || [];
    renderCalendar();
  }

  async function loadTags() {
    const payload = await fetchJSON(`/api/planning/tags/`);
    const tags = payload.tags || [];
    tagsList.innerHTML = tags
      .map((tag) => `<span class="planning-tag-pill" style="--tag-color:${tag.color}">#${tag.id} ${tag.name} (${tag.category})</span>`)
      .join("");
  }

  prevBtn?.addEventListener("click", async () => {
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1);
    await loadItems();
  });

  nextBtn?.addEventListener("click", async () => {
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
    await loadItems();
  });

  reloadBtn?.addEventListener("click", async () => {
    await loadItems();
    await loadTags();
  });

  createForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const formData = new FormData(createForm);
    const tagIds = String(formData.get("tag_ids") || "")
      .split(",")
      .map((v) => Number(v.trim()))
      .filter((v) => Number.isFinite(v) && v > 0);

    const body = {
      title: String(formData.get("title") || "").trim(),
      start_at: String(formData.get("start_at") || ""),
      platform: String(formData.get("platform") || "both"),
      status: String(formData.get("status") || "draft"),
      caption: String(formData.get("caption") || ""),
      notes: String(formData.get("notes") || ""),
      tag_ids: tagIds,
    };

    try {
      await fetchJSON(`/api/planning/calendar/create/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(body),
      });
      createForm.reset();
      await loadItems();
    } catch (err) {
      alert(`Create failed: ${err.message}`);
    }
  });

  tagForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const formData = new FormData(tagForm);
    const body = {
      name: String(formData.get("name") || "").trim(),
      category: String(formData.get("category") || "tag"),
      color: String(formData.get("color") || "#1f6feb"),
    };
    try {
      await fetchJSON(`/api/planning/tags/create/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(body),
      });
      tagForm.reset();
      await loadTags();
    } catch (err) {
      alert(`Tag create failed: ${err.message}`);
    }
  });

  aiForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const formData = new FormData(aiForm);
    const body = {
      niche: String(formData.get("niche") || "").trim(),
      goal: String(formData.get("goal") || "").trim(),
      platform: String(formData.get("platform") || "both"),
      duration_days: Number(formData.get("duration_days") || 7),
    };
    const accountId = Number(formData.get("account_id") || 0);
    if (accountId > 0) body.account_id = accountId;
    try {
      if (aiStatus) aiStatus.textContent = "Generating AI content calendar...";
      const payload = await fetchJSON(`/api/planning/ai-calendar/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(body),
      });
      const plan = payload.plan || {};
      const rows = Array.isArray(plan.calendar_items) ? plan.calendar_items : [];
      if (aiStatus) {
        aiStatus.textContent = `AI plan ready${payload.account_context?.page_name ? ` for ${payload.account_context.page_name}` : ""}. Generated ${rows.length} items.`;
      }
      if (aiResult) {
        aiResult.innerHTML = `
          <article class="ai-report-card">
            <h3>Strategy summary</h3>
            <p>${plan.strategy_summary || "-"}</p>
            <p><strong>Cadence:</strong> ${plan.cadence_recommendation || "-"}</p>
            <p><strong>Best time:</strong> ${plan.best_time_recommendation || "-"}</p>
          </article>
          <div class="planning-ai-list">
            ${rows
              .map(
                (row) => `
                  <article class="planning-ai-item">
                    <p class="eyebrow">${row.day_label || "-"}</p>
                    <h3>${row.post_type || "-"} | ${row.platform || "-"}</h3>
                    <p><strong>Topic:</strong> ${row.topic || "-"}</p>
                    <p><strong>Hook:</strong> ${row.hook || "-"}</p>
                    <p><strong>CTA:</strong> ${row.cta || "-"}</p>
                    <p><strong>Best time:</strong> ${row.best_time_window || "-"}</p>
                    <p><strong>Goal:</strong> ${row.goal || "-"}</p>
                  </article>
                `
              )
              .join("")}
          </div>
        `;
      }
    } catch (err) {
      if (aiStatus) aiStatus.textContent = `AI planner unavailable: ${err.message}`;
      if (aiResult) aiResult.innerHTML = "<p class='ai-output-empty'>Unable to generate AI content calendar.</p>";
    }
  });

  (async () => {
    const input = createForm?.querySelector("[name='start_at']");
    if (input) input.value = fmtLocalInput(new Date().toISOString());
    await loadItems();
    await loadTags();
  })();
})();
