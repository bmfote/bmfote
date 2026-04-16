#!/usr/bin/env node
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const args = process.argv.slice(2);
const command = args[0];

if (!command || command === "--help" || command === "-h") {
  console.log(`
cctx — cloud context for AI agents

Usage:
  npx cctx setup --url <API_URL> --token <API_TOKEN>

To deploy your own backend, see:
  https://github.com/bmfote/bmfote#part-1-deploy-the-server
`);
  process.exit(0);
}

if (command !== "setup") {
  console.error(`Unknown command: ${command}`);
  console.error('Run "npx cctx --help" for usage.');
  process.exit(1);
}

const script = path.join(__dirname, "..", "installer", "setup.sh");
if (!fs.existsSync(script)) {
  console.error("Error: setup.sh not found in package.");
  process.exit(1);
}
const child = spawn("bash", [script, ...args.slice(1)], {
  stdio: "inherit",
  env: { ...process.env },
});
child.on("close", (code) => process.exit(code || 0));
