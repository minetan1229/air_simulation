/**
 * Kilo Code MCP server launcher (project-local, no external API keys).
 *
 * Each MCP server entry point is resolved relative to this file so that the
 * servers start regardless of the working directory the editor/CLI was
 * launched from. StdIO is inherited, so the child process speaks MCP directly
 * to the Kilo client.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NODE_MODULES = path.join(HERE, "node_modules");
const VENV_PYTHON = path.join(HERE, "venv", "Scripts", "python.exe");
const USER_AGENT = "paper-airplane-sim/1.0";

function npmEntry(pkg, rel) {
  return path.join(NODE_MODULES, ...pkg.split("/"), rel);
}

/** Newest Playwright Chromium already provisioned on this machine, if any. */
function findLocalChromium() {
  const root = path.join(os.homedir(), "AppData", "Local", "ms-playwright");
  if (!fs.existsSync(root)) return null;
  const revisions = fs
    .readdirSync(root)
    .filter((entry) => /^chromium-\d+$/.test(entry))
    .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
  for (const revision of revisions) {
    const exe = path.join(root, revision, "chrome-win64", "chrome.exe");
    if (fs.existsSync(exe)) return exe;
  }
  return null;
}

const SERVERS = {
  context7: () => ({
    command: process.execPath,
    args: [npmEntry("@upstash/context7-mcp", path.join("dist", "index.js"))],
  }),
  "sequential-thinking": () => ({
    command: process.execPath,
    args: [
      npmEntry(
        "@modelcontextprotocol/server-sequential-thinking",
        path.join("dist", "index.js"),
      ),
    ],
  }),
  memory: () => ({
    command: process.execPath,
    args: [npmEntry("@modelcontextprotocol/server-memory", path.join("dist", "index.js"))],
    env: {
      MEMORY_FILE_PATH: path.join(HERE, "memory.jsonl"),
      // 記憶ストアの無制限成長を防ぐ上限（件数 / バイト）。
      MEMORY_MAX_ENTRIES: "10000",
      MEMORY_MAX_SIZE_MB: "50",
    },
  }),
  fetch: () => ({
    command: VENV_PYTHON,
    args: ["-m", "mcp_server_fetch", "--user-agent", USER_AGENT],
  }),
  playwright: () => {
    const outputDir = path.join(HERE, "playwright");
    fs.mkdirSync(outputDir, { recursive: true });
    const args = [
      npmEntry("@playwright/mcp", "cli.js"),
      "--browser",
      "chromium",
      "--headless",
      "--isolated",
      "--timeout-navigation",
      "30000",
      "--output-dir",
      outputDir,
    ];
    const executable = findLocalChromium();
    if (executable) args.push("--executable-path", executable);
    return { command: process.execPath, args };
  },
};

const serverId = process.argv[2];
const factory = SERVERS[serverId];
if (!factory) {
  console.error(
    `[mcp-launch] unknown server id: ${serverId ?? "<none>"}. Known: ${Object.keys(SERVERS).join(", ")}`,
  );
  process.exit(1);
}

const spec = factory();
if (!fs.existsSync(spec.command)) {
  console.error(
    `[mcp-launch] ${serverId}: executable not found: ${spec.command}. Run the install steps in .kilo/mcp/README.md`,
  );
  process.exit(1);
}

const child = spawn(spec.command, spec.args, {
  stdio: "inherit",
  env: { ...process.env, ...(spec.env ?? {}) },
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (!child.killed) child.kill(signal);
  });
}

child.on("error", (error) => {
  console.error(`[mcp-launch] ${serverId}: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(0);
  }
  process.exit(code ?? 0);
});
