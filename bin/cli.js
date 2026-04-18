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

function currentSessionId() {
  try {
    const out = execSync("ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -1", {
      encoding: "utf-8",
    }).trim();
    return out ? path.basename(out, ".jsonl") : null;
  } catch { return null; }
}

function resolveClaudeBin() {
  try { return execSync("which claude", { encoding: "utf-8" }).trim() || "claude"; }
  catch { return "claude"; }
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

async function cmdSearch(rest) {
  const q = rest.join(" ").trim();
  if (!q) {
    console.error('Usage: cctx search "query"');
    process.exit(1);
  }
  const results = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
  if (!results || !results.length) {
    console.log(`No results for: ${q}`);
    return;
  }
  for (const r of results) {
    const ts = (r.timestamp || "").slice(0, 10);
    console.log(`[${r.type}] ${ts} (${r.project || "unknown"})`);
    console.log(`  ${r.snippet}`);
    console.log(`  uuid=${r.uuid}`);
    console.log("");
  }
}

function pickBookmark(bookmarks) {
  return new Promise((resolve) => {
    const stdin = process.stdin;
    const stdout = process.stdout;
    let items = [...bookmarks];
    let idx = 0;

    const render = () => {
      const width = stdout.columns || 80;
      const lines = [
        "\x1b[1mcctx\x1b[0m — Select a thread to resume",
        "\x1b[2m↑↓ navigate  ↵ select  d delete  q quit\x1b[0m",
        "",
      ];
      for (let i = 0; i < items.length; i++) {
        const b = items[i];
        const date = (b.last_active || "").slice(0, 10) || "—";
        const name = b.name.length > width - 16 ? b.name.slice(0, width - 17) + "…" : b.name;
        const pad = " ".repeat(Math.max(1, width - 4 - name.length - date.length));
        if (i === idx) lines.push(`\x1b[7m> ${name}${pad}${date}\x1b[0m`);
        else lines.push(`  ${name}${pad}\x1b[2m${date}\x1b[0m`);
      }
      stdout.write("\x1b[2J\x1b[H" + lines.join("\n") + "\n");
    };

    const cleanup = () => {
      stdin.removeListener("data", onData);
      if (stdin.isTTY) stdin.setRawMode(false);
      stdin.pause();
      stdout.write("\x1b[?25h");
    };

    const onData = async (buf) => {
      const s = buf.toString();
      if (s === "\x03" || s === "q") { cleanup(); stdout.write("\n"); resolve(null); return; }
      if (s === "\x1b[A" || s === "k") { idx = (idx - 1 + items.length) % items.length; render(); return; }
      if (s === "\x1b[B" || s === "j") { idx = (idx + 1) % items.length; render(); return; }
      if (s === "\r" || s === "\n") { cleanup(); resolve(items[idx]); return; }
      if (s === "d") {
        const victim = items[idx];
        if (!victim) return;
        try { await api.del(`/api/bookmarks/${encodeURIComponent(victim.name)}`); } catch (e) {
          cleanup(); stdout.write(`\nError: ${e.message}\n`); resolve(null); return;
        }
        items.splice(idx, 1);
        if (idx >= items.length) idx = Math.max(0, items.length - 1);
        if (!items.length) { cleanup(); stdout.write("\n(no bookmarks left)\n"); resolve(null); return; }
        render();
      }
    };

    stdout.write("\x1b[?25l");
    stdin.setRawMode(true);
    stdin.resume();
    stdin.on("data", onData);
    render();
  });
}

async function cmdLaunch(rest) {
  const sub = rest[0];

  if (!sub && process.stdin.isTTY && process.stdout.isTTY) {
    const bookmarks = (await api.get("/api/bookmarks")) || [];
    if (!bookmarks.length) {
      console.log('(none) — save with: cctx launch --save "name"');
      return;
    }
    const picked = await pickBookmark(bookmarks);
    if (!picked) return;
    const claudeBin = resolveClaudeBin();
    const child = spawn(claudeBin, ["--resume", picked.session_id], { stdio: "inherit", env: process.env });
    child.on("exit", (code) => process.exit(code || 0));
    return;
  }

  if (sub === "--save") {
    const name = rest[1];
    const sid = rest[2] || currentSessionId();
    if (!name) {
      console.error('Usage: cctx launch --save "name" [session_id]');
      process.exit(1);
    }
    if (!sid) {
      console.error("Could not detect current session. Provide a session_id.");
      process.exit(1);
    }
    await api.post("/api/bookmarks", { name, session_id: sid });
    console.log(`✓ ${name} → ${sid.slice(0, 8)}…`);
    return;
  }

  if (sub === "--remove") {
    const name = rest[1];
    if (!name) {
      console.error('Usage: cctx launch --remove "name"');
      process.exit(1);
    }
    await api.del(`/api/bookmarks/${encodeURIComponent(name)}`);
    console.log(`✓ removed ${name}`);
    return;
  }

  if (sub === "--list") {
    const bookmarks = (await api.get("/api/bookmarks")) || [];
    if (!bookmarks.length) {
      console.log('(none) — save with: cctx launch --save "name"');
      return;
    }
    for (const b of bookmarks) {
      const date = (b.last_active || "").slice(0, 10);
      console.log(`${b.name}\t${b.session_id}\t${date}`);
    }
    return;
  }

  // Plain resume: cctx launch <name>
  if (sub && !sub.startsWith("-")) {
    const bookmarks = (await api.get("/api/bookmarks")) || [];
    const hit = bookmarks.find((b) => b.name === sub);
    if (!hit) {
      console.error(`No bookmark named "${sub}".`);
      process.exit(1);
    }
    const claudeBin = resolveClaudeBin();
    const child = spawn(claudeBin, ["--resume", hit.session_id], { stdio: "inherit", env: process.env });
    child.on("exit", (code) => process.exit(code || 0));
    return;
  }

  console.error('Usage: cctx launch <name> | --save "name" | --list | --remove "name"');
  console.error("Tip: pipe to fzf → cctx launch --list | fzf | awk '{print $1}' | xargs cctx launch");
  process.exit(1);
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
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    console.error("cctx start requires a TTY.");
    process.exit(1);
  }

  const registry = readRegistry();
  const cwd = process.cwd();
  const cwdSlug = path.basename(cwd) || "home";
  const cwdRegistered = Object.values(registry).some(
    (e) => e && e.cwd === cwd
  );

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

  const claudeBin = resolveClaudeBin();
  const child = spawn(claudeBin, [], {
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
  cctx search "query"                              FTS over all messages
  cctx launch                                      Arrow-key picker over bookmarks (specific threads)
  cctx launch <name>                               Resume bookmarked session by name
  cctx launch --save "name" [session_id]           Bookmark a session
  cctx launch --list                               List bookmarks (tab-delimited; pipe to fzf)
  cctx launch --remove "name"                      Delete a bookmark

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
      case "search": return await cmdSearch(args.slice(1));
      case "launch": return await cmdLaunch(args.slice(1));
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
