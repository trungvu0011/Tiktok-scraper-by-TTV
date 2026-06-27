(() => {
  const DEFAULT_SETTINGS = {
    apiBaseUrl: "http://127.0.0.1:3010",
    extensionToken: "",
  };

  const IDS = {
    root: "ttg-root",
    button: "ttg-generate-btn",
    panel: "ttg-panel",
    status: "ttg-status",
    meta: "ttg-meta",
    textarea: "ttg-textarea",
    copyBtn: "ttg-copy-btn",
    closeBtn: "ttg-close-btn",
  };

  let ui = null;
  let isLoading = false;
  let lastHref = location.href;

  function isTikTokVideoPage() {
    return (
      location.hostname.includes("tiktok.com") &&
      /\/@[^/]+\/video\/\d+/.test(location.pathname)
    );
  }

  function getSettings() {
    return new Promise((resolve) => {
      if (!chrome?.storage?.sync) {
        resolve(DEFAULT_SETTINGS);
        return;
      }

      chrome.storage.sync.get(DEFAULT_SETTINGS, (stored) => {
        resolve(stored || DEFAULT_SETTINGS);
      });
    });
  }

  function normalizeBaseUrl(value) {
    return (value || "").trim().replace(/\/$/, "");
  }

  function getEndpoint(baseUrl) {
    return `${normalizeBaseUrl(baseUrl)}/api/ext/v1/transcript`;
  }

  function buildTranscriptText(segments) {
    if (!Array.isArray(segments)) return "";
    return segments
      .map((s) => (typeof s?.text === "string" ? s.text.trim() : ""))
      .filter(Boolean)
      .join("\n");
  }

  function setStatus(message, type = "info") {
    if (!ui?.statusEl) return;
    ui.statusEl.textContent = message;
    ui.statusEl.className = `ttg-status ttg-status-${type}`;
  }

  function setMeta(text) {
    if (!ui?.metaEl) return;
    ui.metaEl.textContent = text || "";
  }

  function setTranscript(text) {
    if (!ui?.textareaEl) return;
    ui.textareaEl.value = text || "";
  }

  function showPanel() {
    if (!ui?.panelEl) return;
    ui.panelEl.classList.add("ttg-show");
  }

  function hidePanel() {
    if (!ui?.panelEl) return;
    ui.panelEl.classList.remove("ttg-show");
  }

  function setButtonLoading(loading) {
    if (!ui?.buttonEl) return;
    isLoading = loading;
    ui.buttonEl.disabled = loading;
    ui.buttonEl.textContent = loading ? "Generating..." : "Get Transcript";
  }

  async function handleCopy() {
    const content = ui?.textareaEl?.value || "";
    if (!content) {
      setStatus("No transcript to copy.", "error");
      return;
    }

    try {
      await navigator.clipboard.writeText(content);
      setStatus("Copied to clipboard.", "success");
    } catch {
      setStatus("Copy failed. Please copy manually.", "error");
    }
  }

  async function handleGenerate() {
    if (isLoading || !ui) return;

    setButtonLoading(true);
    showPanel();
    setMeta("");
    setStatus("Requesting transcript...", "info");
    setTranscript("");

    try {
      const settings = await getSettings();
      const baseUrl = normalizeBaseUrl(settings.apiBaseUrl);
      const extensionToken = (settings.extensionToken || "").trim();

      if (!baseUrl) {
        throw new Error("API Base URL is empty. Configure it in extension popup.");
      }
      if (!extensionToken) {
        throw new Error("Extension token is empty. Configure it in extension popup.");
      }

      const endpoint = getEndpoint(baseUrl);
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-extension-token": extensionToken,
        },
        body: JSON.stringify({
          url: location.href,
          language: "auto",
          includeFormats: ["txt"],
        }),
      });

      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(payload?.error || `Request failed: ${response.status}`);
      }

      const segments = Array.isArray(payload?.segments) ? payload.segments : [];
      const transcript =
        typeof payload?.formats?.txt === "string" && payload.formats.txt.trim()
          ? payload.formats.txt
          : buildTranscriptText(segments);

      const title = typeof payload?.title === "string" ? payload.title.trim() : "";
      const platform = typeof payload?.platform === "string" ? payload.platform.toUpperCase() : "UNKNOWN";
      const source = typeof payload?.source === "string" ? payload.source : "unknown";

      setMeta(`${platform} · ${segments.length} segments${title ? ` · ${title}` : ""}`);
      setStatus(`Transcript ready (source: ${source}).`, "success");
      setTranscript(transcript || "No transcript text returned.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      setStatus(message, "error");
      setTranscript("");
    } finally {
      setButtonLoading(false);
    }
  }

  function createUI() {
    const root = document.createElement("div");
    root.id = IDS.root;

    const button = document.createElement("button");
    button.id = IDS.button;
    button.type = "button";
    button.textContent = "Get Transcript";
    button.addEventListener("click", handleGenerate);

    const panel = document.createElement("div");
    panel.id = IDS.panel;

    const title = document.createElement("h3");
    title.textContent = "TikTok Transcript";

    const meta = document.createElement("div");
    meta.id = IDS.meta;
    meta.className = "ttg-meta";

    const status = document.createElement("div");
    status.id = IDS.status;
    status.className = "ttg-status ttg-status-info";
    status.textContent = "Click the button to generate transcript.";

    const textarea = document.createElement("textarea");
    textarea.id = IDS.textarea;
    textarea.readOnly = true;
    textarea.placeholder = "Transcript text will appear here...";

    const actions = document.createElement("div");
    actions.className = "ttg-actions";

    const copyBtn = document.createElement("button");
    copyBtn.id = IDS.copyBtn;
    copyBtn.type = "button";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", handleCopy);

    const closeBtn = document.createElement("button");
    closeBtn.id = IDS.closeBtn;
    closeBtn.type = "button";
    closeBtn.textContent = "Close";
    closeBtn.addEventListener("click", hidePanel);

    actions.appendChild(copyBtn);
    actions.appendChild(closeBtn);

    panel.appendChild(title);
    panel.appendChild(meta);
    panel.appendChild(status);
    panel.appendChild(textarea);
    panel.appendChild(actions);

    root.appendChild(button);
    root.appendChild(panel);

    return {
      rootEl: root,
      buttonEl: button,
      panelEl: panel,
      statusEl: status,
      metaEl: meta,
      textareaEl: textarea,
    };
  }

  function removeUI() {
    if (ui?.rootEl?.isConnected) {
      ui.rootEl.remove();
    }
    ui = null;
    isLoading = false;
  }

  function ensureUI() {
    if (!document.body) return;

    if (!isTikTokVideoPage()) {
      removeUI();
      return;
    }

    if (ui?.rootEl?.isConnected) return;

    ui = createUI();
    document.body.appendChild(ui.rootEl);
  }

  function onUrlMaybeChanged() {
    if (location.href === lastHref) return;
    lastHref = location.href;
    setTimeout(ensureUI, 200);
  }

  function init() {
    ensureUI();

    const observer = new MutationObserver(() => {
      onUrlMaybeChanged();
    });

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });

    setInterval(onUrlMaybeChanged, 800);
  }

  init();
})();
