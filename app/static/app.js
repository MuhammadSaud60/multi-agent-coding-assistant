"use strict";

const $ = (sel) => document.querySelector(sel);

const treeEl = $("#file-tree");
const workspacePathEl = $("#workspace-path");
const previewLinkEl = $("#preview-link");
const codeEl = $("#code-content");
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

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/* Very small Python syntax tinting: comments, strings, keywords,
   decorators, numbers. Good enough for a read-only viewer. */
function highlightPython(src) {
  const tokenRe =
    /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(@\w+)|\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|lambda|None|True|False|and|or|not|in|is|pass|raise|yield|async|await|global|nonlocal|assert|del|break|continue|print)\b|\b(\d+(?:\.\d+)?)\b/g;
  let out = "";
  let last = 0;
  let m;
  while ((m = tokenRe.exec(src)) !== null) {
    out += escapeHtml(src.slice(last, m.index));
    const cls = m[1] ? "tok-com" : m[2] ? "tok-str" : m[3] ? "tok-dec" : m[4] ? "tok-kw" : "tok-num";
    out += `<span class="${cls}">${escapeHtml(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  out += escapeHtml(src.slice(last));
  return out;
}

async function openFile(path, li) {
  try {
    const res = await fetch("/api/file?path=" + encodeURIComponent(path));
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    document.querySelectorAll(".tree li.file.selected").forEach((el) => el.classList.remove("selected"));
    if (li) li.classList.add("selected");
    tabEl.textContent = path.split("/").pop();
    codeEl.innerHTML = path.endsWith(".py")
      ? highlightPython(data.content)
      : escapeHtml(data.content);
    $("#editor").scrollTop = 0;
  } catch (err) {
    codeEl.textContent = "// Could not open file: " + err.message;
  }
}

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
      setRunning(true);
      runStatusEl.dataset.final = "";
      addMessage("user", "You", event.request);
      setTerminal("", "run started");
      break;
    case "agent_started":
      setAgentDot(event.agent, "active");
      break;
    case "agent": {
      setAgentDot(event.agent, "done");
      const pre = event.plan
        ? event.plan
        : event.test_result && event.agent === "tester"
          ? event.test_result
          : null;
      addMessage("agent", event.agent, event.message, pre);
      if (event.agent === "developer" && event.files_created && event.files_created.length) {
        refreshTree().then(() => {
          const first = event.files_created.find((f) => f.endsWith(".py")) || event.files_created[0];
          if (first) openFile(first, null);
        });
      }
      if (event.agent === "tester" && event.test_result) {
        setTerminal(event.test_result, event.test_result.startsWith("STATUS: PASSED") ? "tests passed" : "tests failed");
      }
      break;
    }
    case "preview":
      addLinkMessage("system", "Preview", "Preview: " + event.url, event.url);
      refreshPreviewLink();
      break;
    case "run_finished": {
      const s = event.summary || {};
      const passed = (s.test_result || "").startsWith("STATUS: PASSED");
      runStatusEl.dataset.final = passed ? "done" : "error";
      runStatusEl.textContent = passed ? "passed" : "finished";
      runStatusEl.className = "status-pill " + (passed ? "done" : "error");
      addMessage(
        "system",
        "Run complete",
        passed ? "Tests passed." : "Run finished. Check the terminal for the test result."
      );
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
      setTerminal(status.last_summary.test_result || "", "last run");
    }
  } catch {
    /* status is best-effort at startup */
  }
  addMessage("system", "System", "Ready. Describe what to build and press Run agents (Ctrl+Enter). Progress streams here live.");
  connectEvents();
})();
