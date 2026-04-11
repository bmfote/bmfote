#!/usr/bin/env node
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const args = process.argv.slice(2);
const command = args[0];

if (!command || command === "--help" || command === "-h") {
  console.log(`
bmfote — cloud context for AI agents

Usage:
  npx bmfote setup     Connect this machine to cloud memory
  npx bmfote deploy    Stand up your own cloud memory backend

Commands:
  setup    Configure Claude Code on this machine
           (adds MCP server, hooks, and config)
  deploy   Create your own Turso database + Railway server
           (requires turso CLI + railway CLI, both free tier)
`);
  process.exit(0);
}

function runScript(name) {
  const script = path.join(__dirname, "..", "installer", name);
  if (!fs.existsSync(script)) {
    console.error(`Error: ${name} not found in package.`);
    process.exit(1);
  }
  const child = spawn("bash", [script, ...args.slice(1)], {
    stdio: "inherit",
    env: { ...process.env },
  });
  child.on("close", (code) => process.exit(code || 0));
}

if (command === "setup") {
  runScript("setup.sh");
} else if (command === "deploy") {
  runScript("deploy.sh");
} else {
  console.error(`Unknown command: ${command}`);
  console.error('Run "npx bmfote --help" for usage.');
  process.exit(1);
}
