#!/usr/bin/env node
const { execSync, spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const args = process.argv.slice(2);
const command = args[0];

if (!command || command === "--help" || command === "-h") {
  console.log(`
bmfote — cloud context for AI agents

Usage:
  npx bmfote setup --url <railway-url> --token <api-token>

Commands:
  setup    Configure this machine for cloud memory
           (adds MCP server, hooks, and env vars to Claude Code)

Example:
  npx bmfote setup --url https://bmfote-api-production.up.railway.app --token abc123
`);
  process.exit(0);
}

if (command === "setup") {
  // Find the setup script bundled in this package
  const setupScript = path.join(__dirname, "..", "installer", "setup.sh");

  if (!fs.existsSync(setupScript)) {
    console.error("Error: setup.sh not found in package. This is a bug.");
    process.exit(1);
  }

  // Pass remaining args to the bash script
  const setupArgs = args.slice(1);
  const child = spawn("bash", [setupScript, ...setupArgs], {
    stdio: "inherit",
    env: { ...process.env },
  });

  child.on("close", (code) => {
    process.exit(code || 0);
  });
} else {
  console.error(`Unknown command: ${command}`);
  console.error('Run "npx bmfote --help" for usage.');
  process.exit(1);
}
