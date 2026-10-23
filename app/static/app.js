"use strict";

const $ = (sel) => document.querySelector(sel);

const treeEl = $("#file-tree");
const workspacePathEl = $("#workspace-path");
const previewLinkEl = $("#preview-link");
const codeEl = $("#code-content");
const gutterEl = $("#gutter");
const editorEl = $("#editor");
const copyBtn = $("#copy-button");
const tabEl = $("#active-tab");
const terminalEl = $("#terminal");
const terminalMetaEl = $("#terminal-meta");
const chatLogEl = $("#chat-log");
const formEl = $("#run-form");
const inputEl = $("#request-input");
const buttonEl = $("#run-button");
const runStatusEl = $("#run-status");

/* ------------------------------------------------------------------ */
/* file tree + editor                                                  */
/* ------------------------------------------------------------------ */

function renderTree(nodes, container) {
  const ul = document.createElement("ul");
  for (const node of nodes) {
    const li = document.createElement("li");
    if (node.type === "dir") {
      li.className = "dir";
      const name = document.createElement("span");
      name.className = "dir-name";
      name.textContent = node.name;
      li.appendChild(name);
      renderTree(node.children, li);
    } else {
      li.className = "file";
      li.textContent = node.name;
      li.title = node.path;
      li.addEventListener("click", () => openFile(node.path, li));
    }
    ul.appendChild(li);
  }
  container.appendChild(ul);
}

async function refreshPreviewLink() {
  try {
    const res = await fetch("/api/preview-info");
    const info = await res.json();
    if (info.available) {
      previewLinkEl.href = info.url;
      previewLinkEl.hidden = false;
    } else {
      previewLinkEl.hidden = true;
    }
  } catch {
    previewLinkEl.hidden = true;
  }
}

async function refreshTree() {
  try {
    const res = await fetch("/api/files");
    const data = await res.json();
    workspacePathEl.textContent = data.root;
    treeEl.innerHTML = "";
    renderTree(data.tree, treeEl);
    if (!data.tree.length) {
      treeEl.textContent = "(workspace is empty)";
    }
  } catch (err) {
    treeEl.textContent = "Could not load file tree: " + err.message;
  }
  refreshPreviewLink();
}

/* Syntax highlighting via the vendored highlight.js (fully offline).
   Language is picked from the file extension; unknown types fall
   back to plain (escaped) text. */
const LANGUAGE_BY_EXT = {
  py: "python",
  html: "xml",
  htm: "xml",
  css: "css",
  js: "javascript",
  mjs: "javascript",
  json: "json",
  md: "markdown",
};

function languageForPath(path) {
  const match = /\.([A-Za-z0-9]+)$/.exec(path || "");
  if (!match) return null;
  return LANGUAGE_BY_EXT[match[1].toLowerCase()] || null;
}

let currentFilePath = null;
let currentFileContent = null;

function renderGutter(content) {
  if (!content) {
    gutterEl.textContent = "";
    return;
  }
  const lineCount = content.replace(/\n$/, "").split("\n").length;
  const numbers = new Array(lineCount);
  for (let i = 0; i < lineCount; i += 1) numbers[i] = String(i + 1);
  gutterEl.textContent = numbers.join("\n");
}

function renderCode(path, content) {
  currentFilePath = path;
  currentFileContent = content;
  tabEl.textContent = path.split("/").pop();
  const language = languageForPath(path);
  codeEl.className = "hljs" + (language ? " language-" + language : "");
  if (
    language &&
    window.hljs &&
    typeof window.hljs.highlight === "function" &&
    window.hljs.getLanguage(language)
  ) {
    /* highlight.js escapes the source itself; the raw text is
       never injected as HTML. */
    codeEl.innerHTML = window.hljs.highlight(content, { language }).value;
  } else {
    /* Plain fallback: textContent can never be parsed as markup. */
    codeEl.textContent = content;
  }
  renderGutter(content);
  copyBtn.disabled = false;
  editorEl.scrollTop = 0;
  editorEl.scrollLeft = 0;
}

async function openFile(path, li) {
  try {
    const res = await fetch("/api/file?path=" + encodeURIComponent(path));
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    document.querySelectorAll(".tree li.file.selected").forEach((el) => el.classList.remove("selected"));
    if (li) li.classList.add("selected");
    renderCode(path, data.content);
  } catch (err) {
    currentFilePath = null;
    currentFileContent = null;
    codeEl.className = "hljs";
    codeEl.textContent = "// Could not open file: " + err.message;
    gutterEl.textContent = "";
    copyBtn.disabled = true;
  }
}

/* ------------------------------------------------------------------ */
/* copy the current file                                               */
/* ------------------------------------------------------------------ */

let copyResetTimer = null;

function flashCopyLabel(text, ok) {
  copyBtn.textContent = text;
  copyBtn.classList.toggle("copied", ok);
  if (copyResetTimer) clearTimeout(copyResetTimer);
  copyResetTimer = setTimeout(() => {
    copyBtn.textContent = "Copy";
    copyBtn.classList.remove("copied");
  }, 1200);
}

copyBtn.addEventListener("click", async () => {
  if (currentFileContent === null) return;
  /* Always the RAW file text, never the highlighted markup. */
  let ok = false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(currentFileContent);
      ok = true;
    } catch {
      ok = false;
    }
  }
  if (!ok) {
    /* Fallback for non-secure contexts (plain http on localhost). */
    const ta = document.createElement("textarea");
    ta.value = currentFileContent;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    document.body.removeChild(ta);
  }
  flashCopyLabel(ok ? "Copied" : "Copy failed", ok);
});

/* ------------------------------------------------------------------ */
/* chat + terminal                                                     */
/* ------------------------------------------------------------------ */

function addMessage(kind, who, text, preText) {
  const div = document.createElement("div");
  div.className = "msg " + kind;
  const whoEl = document.createElement("span");
  whoEl.className = "who";
  whoEl.textContent = who;
  div.appendChild(whoEl);
  const body = document.createElement("div");
  body.textContent = text;
  div.appendChild(body);
  if (preText) {
    const pre = document.createElement("pre");
    pre.textContent = preText;
    div.appendChild(pre);
  }
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

function addLinkMessage(kind, who, label, url) {
  const div = document.createElement("div");
  div.className = "msg " + kind;
  const whoEl = document.createElement("span");
  whoEl.className = "who";
  whoEl.textContent = who;
  div.appendChild(whoEl);
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = label;
  a.className = "preview-url";
  div.appendChild(a);
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

function setTerminal(text, meta) {
  terminalEl.textContent = text;
  terminalMetaEl.textContent = meta || "";
  terminalEl.scrollTop = terminalEl.scrollHeight;
}

/* ------------------------------------------------------------------ */
/* run phases                                                          */
/*                                                                     */
/* The chat and the terminal show ONLY the phase the run is in:        */
/* Thinking (supervisor/planner), Building (developer/debugger),       */
/* Testing (tester), then one final line: Done or Failed. The          */
/* agents' detailed output (plans, tester reports) stays in the        */
/* backend; it is never rendered here.                                 */
/* ------------------------------------------------------------------ */

const PHASE_BY_AGENT = {
  supervisor: "Thinking",
  planner: "Thinking",
  developer: "Building",
  debugger: "Building",
  tester: "Testing",
};

let phaseTrail = [];
let lastPhase = null;

function renderTerminal() {
  terminalEl.textContent = phaseTrail.join("\n");
  terminalMetaEl.textContent = "";
  terminalEl.scrollTop = terminalEl.scrollHeight;
}

function showPhase(phase) {
  if (!phase || phase === lastPhase) return;
  lastPhase = phase;
  phaseTrail.push(phase);
  const div = document.createElement("div");
  div.className = "phase-line";
  div.textContent = phase;
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
  renderTerminal();
}

function resetPhases() {
  phaseTrail = [];
  lastPhase = null;
  renderTerminal();
}

function finishPhase(word) {
  phaseTrail.push(word);
  const div = document.createElement("div");
  div.className = "phase-line final";
  div.textContent = word;
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
  renderTerminal();
}

function setAgentDot(agent, state) {
  const dot = document.querySelector(`#agent-list li[data-agent="${agent}"] .agent-dot`);
  if (dot) dot.className = "agent-dot " + state;
}

function resetAgents() {
  document.querySelectorAll("#agent-list li").forEach((li) => {
    li.querySelector(".agent-dot").className = "agent-dot idle";
  });
}

function setRunning(running) {
  buttonEl.disabled = running;
  inputEl.disabled = running;
  if (running) {
    runStatusEl.textContent = "running";
    runStatusEl.className = "status-pill running";
  } else if (!runStatusEl.dataset.final) {
    runStatusEl.textContent = "idle";
    runStatusEl.className = "status-pill idle";
  }
  /* If dataset.final is set, run_finished already put the right
     label/class on the pill; leave it alone. */
}

/* ------------------------------------------------------------------ */
/* events (SSE)                                                        */
/* ------------------------------------------------------------------ */

function connectEvents() {
  const source = new EventSource("/api/events");
  source.onmessage = (evt) => {
    let event;
    try {
      event = JSON.parse(evt.data);
    } catch {
      return;
    }
    handleEvent(event);
  };
  source.onerror = () => {
    /* EventSource reconnects on its own; nothing to do. */
  };
}

let seenEvents = 0;

function handleEvent(event) {
  seenEvents += 1;
  switch (event.type) {
    case "run_started":
      resetAgents();
      resetPhases();
      setRunning(true);
      runStatusEl.dataset.final = "";
      addMessage("user", "You", event.request);
      break;
    case "agent_started":
      setAgentDot(event.agent, "active");
      showPhase(PHASE_BY_AGENT[event.agent]);
      break;
    case "agent": {
      setAgentDot(event.agent, "done");
      /* No agent prose in the chat: only the phase lines above.
         The developer finishing still refreshes the Explorer and
         opens the first generated file. */
      if (event.agent === "developer" && event.files_created && event.files_created.length) {
        refreshTree().then(() => {
          const first = event.files_created.find((f) => f.endsWith(".py")) || event.files_created[0];
          if (first) openFile(first, null);
        });
      }
      break;
    }
    case "preview":
      addLinkMessage("system", "Preview", "Preview: " + event.url, event.url);
      refreshPreviewLink();
      break;
    case "run_finished": {
      const s = event.summary || {};
      const result = s.test_result || "";
      const passed = result.startsWith("STATUS: PASSED");
      runStatusEl.dataset.final = passed ? "done" : "error";
      runStatusEl.textContent = passed ? "passed" : "finished";
      runStatusEl.className = "status-pill " + (passed ? "done" : "error");
      if (passed) {
        finishPhase("Done");
      } else if (result.startsWith("STATUS:") || result.startsWith("BLOCKED")) {
        finishPhase("Failed");
      } else if (s.final_response) {
        /* A question's answer or a direct action's outcome is the
           run's result text, not agent chatter: show it. */
        addMessage("agent", "Result", s.final_response);
      }
      refreshTree();
      break;
    }
    case "error":
      runStatusEl.dataset.final = "error";
      runStatusEl.textContent = "error";
      addMessage("error", "Error", event.message);
      break;
    case "status":
      setRunning(event.status.running);
      break;
  }
}

/* ------------------------------------------------------------------ */
/* run form                                                            */
/* ------------------------------------------------------------------ */

formEl.addEventListener("submit", async (evt) => {
  evt.preventDefault();
  const request = inputEl.value.trim();
  if (!request) return;
  setRunning(true);
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request }),
    });
    if (res.status === 409) {
      addMessage("system", "System", "A run is already in progress.");
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    inputEl.value = "";
  } catch (err) {
    setRunning(false);
    addMessage("error", "Error", "Could not start run: " + err.message);
  }
});

inputEl.addEventListener("keydown", (evt) => {
  if (evt.key === "Enter" && (evt.metaKey || evt.ctrlKey)) {
    formEl.requestSubmit();
  }
});

/* ------------------------------------------------------------------ */

(async function init() {
  await refreshTree();
  try {
    const res = await fetch("/api/status");
    const status = await res.json();
    for (const [agent, state] of Object.entries(status.agents || {})) {
      setAgentDot(agent, state);
    }
    if (status.running) setRunning(true);
    if (status.last_summary) {
      const last = status.last_summary.test_result || "";
      if (last.startsWith("STATUS: PASSED")) phaseTrail = ["Done"];
      else if (last.startsWith("STATUS:") || last.startsWith("BLOCKED")) phaseTrail = ["Failed"];
      renderTerminal();
    }
  } catch {
    /* status is best-effort at startup */
  }
  addMessage("system", "System", "Ready. Describe what to build and press Run agents (Ctrl+Enter). Progress streams here live.");
  connectEvents();
})();
