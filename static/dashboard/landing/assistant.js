(() => {
  const widget = document.querySelector("[data-homepage-assistant]");
  if (!widget) return;

  const dialog = widget.querySelector("dialog");
  if (typeof dialog.show !== "function") return;

  const launcher = widget.querySelector(".assistant-launcher");
  const closeButton = dialog.querySelector(".assistant-close");
  const chat = dialog.querySelector(".assistant-body");
  const contact = dialog.querySelector(".assistant-contact");
  const showContact = chat.querySelector("[data-show-contact]");
  const showChat = contact.querySelector("[data-show-chat]");
  const askForm = chat.querySelector("form");
  const question = askForm.elements.question;
  const questionStatus = askForm.querySelector("[role=status]");
  const suggestions = chat.querySelectorAll("[data-question]");
  const log = chat.querySelector("[role=log]");
  const contactForm = contact.querySelector("form");
  const contactStatus = contact.querySelector("[role=status]");
  const viewport = window.visualViewport;

  function fitDialog() {
    if (!dialog.open) return;

    // Mobile keyboards can shrink the visible viewport without changing the layout viewport.
    const keyboardGap = Math.max(0, document.documentElement.clientHeight - viewport.offsetTop - viewport.height);
    dialog.style.setProperty("--assistant-viewport-height", viewport.height + "px");
    dialog.style.setProperty("--assistant-viewport-bottom", keyboardGap + "px");
    const focused = document.activeElement;
    if (dialog.contains(focused) && focused.matches("input")) {
      focused.scrollIntoView({ block: "nearest" });
    }
  }

  viewport?.addEventListener("resize", fitDialog);
  viewport?.addEventListener("scroll", fitDialog);

  launcher.addEventListener("click", () => {
    dialog.show();
    launcher.setAttribute("aria-expanded", "true");
    closeButton.focus();
    if (viewport) fitDialog();
  });

  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") dialog.close();
  });
  dialog.addEventListener("close", () => {
    launcher.setAttribute("aria-expanded", "false");
    launcher.focus({ preventScroll: true });
  });

  showContact.addEventListener("click", () => {
    chat.hidden = true;
    contact.hidden = false;
    showChat.focus();
  });
  showChat.addEventListener("click", () => {
    contact.hidden = true;
    chat.hidden = false;
    showContact.focus();
  });

  async function post(form) {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { Accept: "application/json" },
    });
    if (!response.headers.get("Content-Type")?.includes("application/json")) {
      throw new Error("Refresh the page and try again, or use the Help page.");
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Try again in a moment.");
    return data;
  }

  function addMessage(text, user = false, link, linkLabel) {
    const message = document.createElement("div");
    message.className = user ? "assistant-message assistant-message-user" : "assistant-message";
    message.textContent = text;
    if (link) {
      const paragraph = document.createElement("p");
      const anchor = document.createElement("a");
      anchor.href = link;
      anchor.textContent = linkLabel;
      paragraph.append(anchor);
      message.append(paragraph);
    }
    log.append(message);
    if (log.childElementCount > 14) log.firstElementChild.remove();
    log.scrollTop = log.scrollHeight;
  }

  askForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const send = askForm.querySelector("[type=submit]");
    const submitted = question.value.trim();
    if (send.disabled) return;
    if (!submitted) {
      questionStatus.textContent = "Enter a question.";
      return;
    }
    send.disabled = true;
    suggestions.forEach((suggestion) => { suggestion.disabled = true; });
    questionStatus.textContent = "Looking up an answer…";
    try {
      const data = await post(askForm);
      addMessage(submitted, true);
      addMessage(data.answer, false, data.link, data.link_label);
      if (question.value.trim() === submitted) question.value = "";
      questionStatus.textContent = "";
    } catch (error) {
      questionStatus.textContent = error instanceof TypeError ? "Check your connection and try again." : error.message;
    } finally {
      send.disabled = false;
      suggestions.forEach((suggestion) => { suggestion.disabled = false; });
    }
  });

  suggestions.forEach((suggestion) => {
    suggestion.addEventListener("click", () => {
      question.value = suggestion.dataset.question;
      askForm.requestSubmit();
    });
  });

  contactForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const send = contactForm.querySelector("[type=submit]");
    if (send.disabled) return;
    send.disabled = true;
    contactStatus.textContent = "Saving your request…";
    try {
      const data = await post(contactForm);
      contactForm.reset();
      contactStatus.textContent = data.message;
      contactForm.hidden = true;
      if (dialog.open && !contact.hidden) contactStatus.focus();
    } catch (error) {
      contactStatus.textContent = error instanceof TypeError ? "Check your connection and try again." : error.message;
    } finally {
      send.disabled = false;
    }
  });

  widget.hidden = false;
})();
