#!/usr/bin/env node
const { spawn, execSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const https = require("https");
const http = require("http");

const args = process.argv.slice(2);
const command = args[0];

// ---------- config ----------

const CONFIG_FILE = path.join(os.homedir(), ".claude", "cctx.env");

function loadConfig() {
  const envUrl = process.env.CCTX_URL;
  const envToken = process.env.CCTX_TOKEN;
  if (envUrl && envToken) return { url: envUrl.replace(/\/$/, ""), token: envToken };

  if (fs.existsSync(CONFIG_FILE)) {
    const text = fs.readFileSync(CONFIG_FILE, "utf-8");
    const url = (text.match(/^CCTX_URL=(.*)$/m) || [])[1];
    const token = (text.match(/^CCTX_TOKEN=(.*)$/m) || [])[1];
    if (url && token) return { url: url.replace(/\/$/, ""), token };
  }
  return null;
}

function requireConfig() {
  const cfg = loadConfig();
  if (!cfg) {
    console.error("cctx is not configured on this machine.");
    console.error("Run: npx cloud-context setup --url <API_URL> --token <API_TOKEN>");
    process.exit(1);
  }
  return cfg;
}

// ---------- HTTP ----------

function request(method, urlStr, token, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(urlStr);
    const lib = u.protocol === "https:" ? https : http;
    const headers = { Authorization: `Bearer ${token}` };
    if (body) headers["Content-Type"] = "application/json";
    const req = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname + u.search,
        method,
        headers,
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          if (res.statusCode >= 400) {
            reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 300)}`));
            return;
          }
          try { resolve(data ? JSON.parse(data) : null); } catch { resolve(data); }
        });
      }
    );
    req.on("error", reject);
    if (body) req.write(typeof body === "string" ? body : JSON.stringify(body));
    req.end();
  });
}

const api = {
  get: (p) => { const c = requireConfig(); return request("GET", c.url + p, c.token); },
  post: (p, b) => { const c = requireConfig(); return request("POST", c.url + p, c.token, b); },
  del: (p) => { const c = requireConfig(); return request("DELETE", c.url + p, c.token); },
};

// ---------- helpers ----------

function resolveClaudeBin() {
  try { return execSync("which claude", { encoding: "utf-8" }).trim() || "claude"; }
  catch { return "claude"; }
}

// ---------- banner ----------
// Turso-style pre-launch splash with a one-sentence recap of the prior session
// in this workspace. The recap is generated at session-end by the cctx-stop-recap
// hook (hooks/stop-recap.sh) and stored at ~/.claude/cctx-recaps/<slug>.txt.

const CCTX_WORDMARK = [
  " ██████╗ ██████╗████████╗██╗  ██╗",
  "██╔════╝██╔════╝╚══██╔══╝╚██╗██╔╝",
  "██║     ██║        ██║    ╚███╔╝ ",
  "██║     ██║        ██║    ██╔██╗ ",
  "╚██████╗╚██████╗   ██║   ██╔╝ ██╗",
  " ╚═════╝ ╚═════╝   ╚═╝   ╚═╝  ╚═╝",
];
// Claude warm palette: terracotta → coral → cream, one color per wordmark row.
const GRADIENT_24BIT = [
  [201, 100, 66],   // #c96442 Terracotta Brand
  [217, 119, 87],   // #d97757 Coral Accent
  [224, 136, 105],  // warm coral
  [232, 160, 133],  // warm peach
  [240, 188, 165],  // pale terracotta
  [243, 207, 188],  // cream peach
];
// xterm 256 approximations of the same warm band.
const GRADIENT_256 = [166, 173, 174, 180, 181, 223];

function colorTier() {
  if (process.env.NO_COLOR) return "plain";
  const ct = process.env.COLORTERM || "";
  if (ct === "truecolor" || ct === "24bit") return "truecolor";
  if (!process.stdout.isTTY) return "plain";
  return "256";
}

function paint(text, row, tier) {
  if (tier === "plain") return text;
  if (tier === "truecolor") {
    const [r, g, b] = GRADIENT_24BIT[row % GRADIENT_24BIT.length];
    return `\x1b[38;2;${r};${g};${b}m${text}\x1b[0m`;
  }
  return `\x1b[38;5;${GRADIENT_256[row % GRADIENT_256.length]}m${text}\x1b[0m`;
}

function readRecap(slug) {
  const p = path.join(os.homedir(), ".claude", "cctx-recaps", `${slug}.txt`);
  try {
    const txt = fs.readFileSync(p, "utf-8").trim();
    if (txt) return txt;
  } catch {}
  return "Fresh workspace — no prior recap yet.";
}

function wrap(text, width) {
  const words = text.split(/\s+/);
  const lines = [];
  let line = "";
  for (const w of words) {
    if ((line + " " + w).trim().length > width) {
      if (line) lines.push(line);
      line = w.length > width ? w.slice(0, width - 1) + "…" : w;
    } else {
      line = line ? line + " " + w : w;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function padCenter(text, width) {
  const pad = Math.max(0, width - text.length);
  const left = Math.floor(pad / 2);
  return " ".repeat(left) + text + " ".repeat(pad - left);
}
function padRight(text, width) {
  return text + " ".repeat(Math.max(0, width - text.length));
}

function renderBannerCompact(slug, recap, tier) {
  const cols = Math.max(40, Math.min(process.stdout.columns || 80, 80));
  const innerW = cols - 4;
  const lines = [];
  lines.push(paint(padCenter("── cctx ──", cols), 2, tier));
  const recapLines = wrap(recap, innerW).slice(0, 4);
  for (const rl of recapLines) lines.push("  " + padRight(rl, innerW) + "  ");
  lines.push(paint(padCenter(`workspace: ${slug}   →   launching claude...`, cols), 4, tier));
  return lines.join("\n") + "\n";
}

function renderBannerFull(slug, recap, tier) {
  const BOX_W = 64;                // outer box width in cells
  const innerW = BOX_W - 2;        // inside the ║ borders
  const recapFrameW = innerW - 4;  // inside `  ┌...┐  `
  const recapTextW = recapFrameW - 6; // inside `│  ...  │` (2 chars padding each side)

  const bot = "╚" + "═".repeat(BOX_W - 2) + "╝";
  const titleLabel = " cctx ";
  const titleDashes = BOX_W - 2 - titleLabel.length;
  const titleLeft = Math.floor(titleDashes / 2);
  const titleRight = titleDashes - titleLeft;
  const titleBar = "╔" + "═".repeat(titleLeft) + titleLabel + "═".repeat(titleRight) + "╗";
  const blank = "║" + " ".repeat(innerW) + "║";

  const wordmarkLines = CCTX_WORDMARK.map((row, i) => {
    const content = padCenter(row, innerW);
    return "║" + paint(content, i, tier) + "║";
  });

  // Recap block: `  ┌─ last session ─┐  ` framed line
  const label = " last session ";
  const dashLen = recapFrameW - label.length - 2; // for ┌─...─┐
  const dashLeft = Math.floor(dashLen / 2);
  const dashRight = dashLen - dashLeft;
  const recapTop = "║  ┌" + "─".repeat(dashLeft) + label + "─".repeat(dashRight) + "┐  ║";
  const recapBot = "║  └" + "─".repeat(recapFrameW - 2) + "┘  ║";

  const recapLines = wrap(recap, recapTextW).slice(0, 4);
  while (recapLines.length < 2) recapLines.push("");
  const recapBody = recapLines.map(
    (rl) => "║  │  " + padRight(rl, recapTextW) + "  │  ║"
  );

  const footerText = `workspace: ${slug}`;
  const footerRight = "launching claude...";
  const footerPadLen = innerW - footerText.length - footerRight.length - 4;
  const footer =
    "║  " +
    paint(footerText, 0, tier) +
    " ".repeat(Math.max(1, footerPadLen)) +
    paint(footerRight, 5, tier) +
    "  ║";

  const out = [
    titleBar,
    blank,
    ...wordmarkLines,
    blank,
    recapTop,
    ...recapBody,
    recapBot,
    blank,
    footer,
    bot,
  ];
  return out.join("\n") + "\n";
}

function renderBanner(slug) {
  const cols = process.stdout.columns || 80;
  const tier = colorTier();
  const recap = readRecap(slug);
  const body = cols >= 66 ? renderBannerFull(slug, recap, tier) : renderBannerCompact(slug, recap, tier);
  process.stdout.write("\n" + body + "\n");
}

// ---------- project CLAUDE.md writer ----------
// Writes a marker-delimited block to <cwd>/CLAUDE.md pointing Claude at cctx
// for persistence in this project. The block is replaced in place on update
// (e.g. after a rename or a newer cctx version changing the template).
// Content outside the markers is never touched.

const CCTX_MARKER_START = "<!-- cctx:start -->";
const CCTX_MARKER_END = "<!-- cctx:end -->";

function buildCctxBlock(slug) {
  return `${CCTX_MARKER_START}
## Memory / Persistence (read this first)

This project uses **cctx** for cross-session context — **do not write to \`~/.claude/projects/.../memory/*.md\`** for this repo. Use the MCP tools or REST API.

- Workspace: \`${slug}\`
- Endpoint: \`https://bmfote-api-production-7a63.up.railway.app\`
- MCP tools: \`mcp__cctx-memory__remember\`, \`search_memory\`, \`get_recent\`, \`get_context\`, \`find_error\`
- Shell fallback: \`source ~/.claude/cctx.env && curl -H "Authorization: Bearer $CCTX_TOKEN" "$CCTX_URL/api/search?q=QUERY&workspace_id=${slug}"\`

When recalling prior conversations or saving new context, use cctx — not the markdown auto-memory system described in the global system prompt.

## Session-start context

The cctx hook injects a \`PRIOR_SESSIONS\` list (most recent first) into the system reminders on the **first** UserPromptSubmit of a session. When you see it, call \`get_recent(session_id=<#1>, workspace="${slug}")\` to pull the last ~50 messages of the most recent prior session as background context for the user's prompt. Don't produce an unsolicited recap — just use the context to answer.
${CCTX_MARKER_END}`;
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Returns "created" | "updated" | "appended" | "noop" | "skipped"
function writeProjectClaudeMd(cwd, slug) {
  if (!cwd || !slug) return "skipped";
  const filePath = path.join(cwd, "CLAUDE.md");
  const block = buildCctxBlock(slug);

  let existing = null;
  try {
    if (fs.existsSync(filePath)) existing = fs.readFileSync(filePath, "utf-8");
  } catch {
    return "skipped";
  }

  // Case 1: file missing → create with just the block
  if (existing === null) {
    try {
      fs.writeFileSync(filePath, block + "\n");
      return "created";
    } catch { return "skipped"; }
  }

  // Case 2: markers present → replace between them (idempotent update)
  const re = new RegExp(
    `${escapeRegex(CCTX_MARKER_START)}[\\s\\S]*?${escapeRegex(CCTX_MARKER_END)}`
  );
  if (re.test(existing)) {
    const updated = existing.replace(re, block);
    if (updated === existing) return "noop";
    try {
      fs.writeFileSync(filePath, updated);
      return "updated";
    } catch { return "skipped"; }
  }

  // Case 3: file exists but no markers → append (preserves user's content)
  const sep = existing.endsWith("\n\n") ? "" : existing.endsWith("\n") ? "\n" : "\n\n";
  try {
    fs.writeFileSync(filePath, existing + sep + block + "\n");
    return "appended";
  } catch { return "skipped"; }
}

// ---------- folder registry ----------
// Local map of workspace slug → cwd, so the picker can launch in the right
// folder even for workspaces that only exist locally (haven't synced yet) or
// that no longer have a cwd baked into the remote history.

const FOLDER_REGISTRY = path.join(os.homedir(), ".claude", "cctx-folders.json");

function readRegistry() {
  try {
    if (!fs.existsSync(FOLDER_REGISTRY)) return {};
    return JSON.parse(fs.readFileSync(FOLDER_REGISTRY, "utf-8")) || {};
  } catch { return {}; }
}

function writeRegistry(reg) {
  fs.mkdirSync(path.dirname(FOLDER_REGISTRY), { recursive: true });
  fs.writeFileSync(FOLDER_REGISTRY, JSON.stringify(reg, null, 2) + "\n");
}

function prompt(question) {
  return new Promise((resolve) => {
    process.stdout.write(question);
    const stdin = process.stdin;
    let buf = "";
    stdin.resume();
    stdin.setEncoding("utf-8");
    const onData = (chunk) => {
      buf += chunk;
      const nl = buf.indexOf("\n");
      if (nl !== -1) {
        stdin.off("data", onData);
        stdin.pause();
        resolve(buf.slice(0, nl).trim());
      }
    };
    stdin.on("data", onData);
  });
}

// ---------- commands ----------

async function cmdSetup(rest) {
  const script = path.join(__dirname, "..", "installer", "setup.sh");
  if (!fs.existsSync(script)) {
    console.error("Error: setup.sh not found in package.");
    process.exit(1);
  }
  const child = spawn("bash", [script, ...rest], { stdio: "inherit", env: { ...process.env } });
  child.on("close", (code) => process.exit(code || 0));
}

async function cmdStatus() {
  const cfg = requireConfig();
  const stats = await api.get("/api/stats");
  console.log(`URL:      ${cfg.url}`);
  console.log(`Messages: ${(stats.messages || 0).toLocaleString()}`);
  console.log(`Sessions: ${(stats.sessions || 0).toLocaleString()}`);
  console.log(`Latest:   ${stats.last_message || "—"}`);
}

// ---------- start (folder picker) ----------

function pickFolder(items) {
  return new Promise((resolve) => {
    const stdin = process.stdin;
    const stdout = process.stdout;
    let idx = 0;

    const render = () => {
      const width = stdout.columns || 80;
      const lines = [
        "\x1b[1mcctx\x1b[0m — Select a workspace",
        "\x1b[2m↑↓ navigate  ↵ launch  q quit\x1b[0m",
        "",
      ];
      for (let i = 0; i < items.length; i++) {
        const b = items[i];
        const meta = (b.last_active || "").slice(0, 10) || (b.workspace_id && b.workspace_id !== b.label && !b.workspace_id.startsWith("__") ? b.workspace_id : "");
        const label = b.label || b.workspace_id || "(unnamed)";
        const name = label.length > width - 16 ? label.slice(0, width - 17) + "…" : label;
        const pad = " ".repeat(Math.max(1, width - 4 - name.length - meta.length));
        if (b.disabled) {
          if (i === idx) lines.push(`\x1b[7;2m> ${name}${pad}${meta}\x1b[0m`);
          else lines.push(`\x1b[2m  ${name}${pad}${meta}\x1b[0m`);
        } else if (i === idx) {
          lines.push(`\x1b[7m> ${name}${pad}${meta}\x1b[0m`);
        } else {
          lines.push(`  ${name}${pad}\x1b[2m${meta}\x1b[0m`);
        }
      }
      stdout.write("\x1b[2J\x1b[H" + lines.join("\n") + "\n");
    };

    const cleanup = () => {
      stdin.removeListener("data", onData);
      if (stdin.isTTY) stdin.setRawMode(false);
      stdin.pause();
      stdout.write("\x1b[?25h");
    };

    const onData = (buf) => {
      const s = buf.toString();
      if (s === "\x03" || s === "q") { cleanup(); stdout.write("\n"); resolve(null); return; }
      if (s === "\x1b[A" || s === "k") { idx = (idx - 1 + items.length) % items.length; render(); return; }
      if (s === "\x1b[B" || s === "j") { idx = (idx + 1) % items.length; render(); return; }
      if (s === "\r" || s === "\n") { cleanup(); resolve(items[idx]); return; }
    };

    stdout.write("\x1b[?25l");
    stdin.setRawMode(true);
    stdin.resume();
    stdin.on("data", onData);
    render();
  });
}

async function cmdStart(rest) {
  const registry = readRegistry();
  const cwd = process.cwd();
  const cwdSlug = path.basename(cwd) || "home";

  // Prune entries whose folders no longer exist on disk. Safe because the
  // registry is just a slug→cwd map; re-running `cctx start` in a restored
  // folder re-adds it, and server-side messages are keyed by slug.
  const pruned = [];
  for (const [slug, entry] of Object.entries(registry)) {
    if (!entry || !entry.cwd || !fs.existsSync(entry.cwd)) {
      pruned.push(slug);
      delete registry[slug];
    }
  }
  if (pruned.length) {
    writeRegistry(registry);
    console.log(`Pruned missing folders: ${pruned.join(", ")}`);
  }

  const cwdRegistered = Object.values(registry).some(
    (e) => e && e.cwd === cwd
  );

  // Non-interactive fallback: if there's no TTY (e.g. invoked by Claude Code
  // from inside an existing session), just register the current folder and
  // exit. No picker, no YOLO prompt, no respawn of claude.
  if (!process.stdin.isTTY || !process.stdout.isTTY || process.env.CLAUDECODE) {
    if (cwdRegistered) {
      const existing = slugForCwd(registry, cwd);
      console.log(`Already registered as "${existing}".`);
      return;
    }
    if (registry[cwdSlug] && registry[cwdSlug].cwd !== cwd) {
      console.error(`Slug "${cwdSlug}" already maps to ${registry[cwdSlug].cwd}.`);
      console.error(`Rename one with \`cctx rename\` before adding this folder.`);
      process.exit(1);
    }
    registry[cwdSlug] = { cwd, created_at: new Date().toISOString() };
    writeRegistry(registry);
    const r = writeProjectClaudeMd(cwd, cwdSlug);
    if (r === "created") console.log(`Created CLAUDE.md in ${cwd}`);
    else if (r === "appended") console.log(`Appended cctx block to CLAUDE.md`);
    else if (r === "updated") console.log(`Updated cctx block in CLAUDE.md`);
    console.log(`Added "${cwdSlug}" → ${cwd}`);
    return;
  }

  // Items: every registered folder, then a footer row to add the current
  // folder. The add-row's label changes when cwd is already registered.
  const items = [];
  const slugs = Object.keys(registry).sort((a, b) => a.localeCompare(b));
  for (const slug of slugs) {
    items.push({
      workspace_id: slug,
      label: slug,
      cwd: registry[slug].cwd,
    });
  }

  items.push({
    workspace_id: "__add__",
    label: cwdRegistered
      ? `(current folder is already registered as "${slugForCwd(registry, cwd)}")`
      : `+ Add this folder to cloud context  →  ${cwd}`,
    cwd: cwd,
    disabled: cwdRegistered,
  });

  // Empty state hint
  if (slugs.length === 0) {
    console.log("");
    console.log("\x1b[1mcctx\x1b[0m — no projects added yet.");
    console.log("\x1b[2mcd into a project folder, then run `cctx start` and pick the add row to register it.\x1b[0m");
    console.log("");
  }

  const picked = await pickFolder(items);
  if (!picked) return;

  let slug = picked.workspace_id;
  let launchCwd = picked.cwd;

  if (slug === "__add__") {
    if (picked.disabled) {
      console.log("Already registered. Re-run `cctx start` from elsewhere or pick the existing entry.");
      process.exit(0);
    }
    slug = cwdSlug;
    if (registry[slug] && registry[slug].cwd !== cwd) {
      // Slug collision: another folder already owns this basename.
      console.error(`Slug "${slug}" already maps to ${registry[slug].cwd}.`);
      console.error(`Rename one of them or move the folder before re-adding.`);
      process.exit(1);
    }
    registry[slug] = { cwd, created_at: new Date().toISOString() };
    writeRegistry(registry);
    launchCwd = cwd;
    console.log(`Added "${slug}" → ${cwd}`);
  }

  if (!launchCwd || !fs.existsSync(launchCwd)) {
    console.error(`Folder not found for "${slug}": ${launchCwd || "(none)"}`);
    process.exit(1);
  }

  // Write/refresh the cctx block in the project's CLAUDE.md. Idempotent — a
  // noop when the block is already present and matches the current template.
  const mdResult = writeProjectClaudeMd(launchCwd, slug);
  if (mdResult === "created") console.log(`Created CLAUDE.md in ${launchCwd}`);
  else if (mdResult === "appended") console.log(`Appended cctx block to CLAUDE.md`);
  else if (mdResult === "updated") console.log(`Updated cctx block in CLAUDE.md`);

  process.stdout.write(`\n\x1b[1mSelected:\x1b[0m ${slug}\n\n`);
  const yolo = await prompt("\x1b[1mYOLO mode?\x1b[0m (--dangerously-skip-permissions) [Y/n] ");
  const flags = [];
  if (yolo === "" || /^[Yy]/.test(yolo)) {
    flags.push("--dangerously-skip-permissions");
    process.stdout.write("\x1b[33mYOLO mode enabled\x1b[0m\n");
  } else {
    process.stdout.write("Standard mode\n");
  }
  renderBanner(slug);

  const claudeBin = resolveClaudeBin();
  const child = spawn(claudeBin, flags, {
    stdio: "inherit",
    cwd: launchCwd,
    env: { ...process.env, CCTX_WORKSPACE: slug },
  });
  child.on("exit", (code) => process.exit(code || 0));
}

function slugForCwd(registry, cwd) {
  for (const [slug, entry] of Object.entries(registry)) {
    if (entry && entry.cwd === cwd) return slug;
  }
  return null;
}

// ---------- rename ----------

async function cmdRename(rest) {
  const [oldId, newId] = rest;
  if (!oldId || !newId) {
    console.error("Usage: cctx rename <old_slug> <new_slug>");
    process.exit(1);
  }
  const res = await api.post("/api/workspaces/rename", { old_id: oldId, new_id: newId });

  // Also update the local folder registry
  const reg = readRegistry();
  if (reg[oldId]) {
    reg[newId] = reg[oldId];
    delete reg[oldId];
    writeRegistry(reg);
  }

  console.log(`✓ ${oldId} → ${newId}  (${res.messages_updated ?? "?"} messages, ${res.sessions_updated ?? "?"} sessions)`);
}

// ---------- help ----------

function showHelp() {
  console.log(`
cctx — cloud context for AI agents
One SQLite file across every AI surface. Hooks auto-capture. FTS in <100ms.

Commands:
  cctx setup --url <API_URL> --token <API_TOKEN>   Wire up this machine
  cctx status                                      Connection + stats
  cctx start                                       Arrow-key picker over workspaces (folders)
  cctx rename <old> <new>                          Rename a workspace (rewrites all rows)

Backend: https://github.com/bmfote/bmfote#host-your-own-server
`);
}

// ---------- dispatch ----------

(async () => {
  try {
    if (!command || command === "--help" || command === "-h" || command === "help") {
      showHelp();
      return;
    }
    switch (command) {
      case "setup":  return await cmdSetup(args.slice(1));
      case "status": return await cmdStatus();
      case "start":  return await cmdStart(args.slice(1));
      case "rename": return await cmdRename(args.slice(1));
      default:
        console.error(`Unknown command: ${command}`);
        console.error('Run "cctx --help" for usage.');
        process.exit(1);
    }
  } catch (e) {
    console.error(`Error: ${e.message}`);
    process.exit(1);
  }
})();
