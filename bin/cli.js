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

function loadConfig() {
  const envUrl = process.env.CCTX_URL;
  const envToken = process.env.CCTX_TOKEN;
  if (envUrl && envToken) return { url: envUrl.replace(/\/$/, ""), token: envToken };

  const cfgFile = path.join(os.homedir(), ".claude", "cctx.env");
  if (fs.existsSync(cfgFile)) {
    const text = fs.readFileSync(cfgFile, "utf-8");
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
    console.error("Run: npx cctx setup --url <API_URL> --token <API_TOKEN>");
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

async function cmdLaunch(rest) {
  const sub = rest[0];

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

// ---------- help ----------

function showHelp() {
  console.log(`
cctx — cloud context for AI agents
One SQLite file across every AI surface. Hooks auto-capture. FTS in <100ms.

Commands:
  cctx setup --url <API_URL> --token <API_TOKEN>   Wire up this machine
  cctx status                                      Connection + stats
  cctx search "query"                              FTS over all messages
  cctx launch <name>                               Resume bookmarked session
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
