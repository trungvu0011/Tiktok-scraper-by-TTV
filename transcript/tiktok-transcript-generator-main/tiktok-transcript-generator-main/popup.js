const DEFAULT_SETTINGS = {
  apiBaseUrl: "http://127.0.0.1:3010",
  extensionToken: "",
};

function $(id) {
  return document.getElementById(id);
}

function setStatus(message, isError = false) {
  const el = $("status");
  if (!el) return;
  el.textContent = message;
  el.style.color = isError ? "#ff7b7b" : "#7cc4ff";
}

function normalizeBaseUrl(value) {
  return (value || "").trim().replace(/\/$/, "");
}

function loadSettings() {
  chrome.storage.sync.get(DEFAULT_SETTINGS, (settings) => {
    $("apiBaseUrl").value = settings.apiBaseUrl || DEFAULT_SETTINGS.apiBaseUrl;
    $("extensionToken").value = settings.extensionToken || "";
  });
}

function saveSettings() {
  const apiBaseUrl = normalizeBaseUrl($("apiBaseUrl").value);
  const extensionToken = ($("extensionToken").value || "").trim();

  if (!apiBaseUrl) {
    setStatus("API Base URL is required.", true);
    return;
  }

  if (!extensionToken) {
    setStatus("Extension token is required.", true);
    return;
  }

  chrome.storage.sync.set({ apiBaseUrl, extensionToken }, () => {
    setStatus("Saved.");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  $("saveBtn").addEventListener("click", saveSettings);
});
