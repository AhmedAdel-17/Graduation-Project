// dev-launcher.cjs
//
// Starts BOTH halves of the dev stack so `npm run dev` "just works":
//   1. the FastAPI backend (uvicorn on :8000) — the dashboard proxies /api to it
//   2. the Vite dev server (:5173)
//
// Without the backend, every /api call and the analyze WebSocket fail
// ("Request failed (500)", "WebSocket connection error", health banner). If a
// backend is already listening on :8000 we leave it alone.
//
// Also keeps the original workaround: Vite 7 breaks when the project path
// contains '#' (treated as a URL fragment in internal file:// URLs), so we run
// Vite from a '#'-free junction.

const { execSync, spawn, spawnSync } = require("child_process");
const { existsSync } = require("fs");
const { createHash } = require("crypto");
const net = require("net");
const path = require("path");

const dashboardDir = process.cwd();
const repoRoot = path.dirname(dashboardDir); // backend runs from the repo root

// ── '#'-path junction workaround (Vite only) ────────────────────────────────
let viteCwd = dashboardDir;
if (dashboardDir.includes("#")) {
  const tag = createHash("md5").update(dashboardDir).digest("hex").slice(0, 8);
  const junction = `C:\\vj${tag}`;
  if (!existsSync(junction)) {
    try {
      execSync(
        `powershell -NoProfile -NonInteractive -Command ` +
          `"New-Item -ItemType Junction -Path '${junction}' -Target '${dashboardDir}' | Out-Null"`,
        { stdio: "pipe" }
      );
    } catch (e) {
      console.warn("[dev-launcher] Junction creation failed:", e.message);
    }
  }
  if (existsSync(junction)) {
    console.log(`[dev-launcher] Launching Vite from junction: ${junction}`);
    viteCwd = junction;
  }
}

function portInUse(port) {
  return new Promise((resolve) => {
    const sock = net.connect({ host: "127.0.0.1", port }, () => {
      sock.destroy();
      resolve(true);
    });
    sock.on("error", () => resolve(false));
    sock.setTimeout(800, () => {
      sock.destroy();
      resolve(false);
    });
  });
}

async function main() {
  let backend = null;

  if (await portInUse(8000)) {
    console.log("[dev-launcher] API server already running on :8000 — reusing it.");
  } else {
    const py = process.env.PYTHON || "python";
    console.log(
      "[dev-launcher] Starting FastAPI backend (uvicorn) on :8000 — first start can take ~10-20s…"
    );
    backend = spawn(
      py,
      ["-m", "uvicorn", "server.api_server:app", "--host", "127.0.0.1", "--port", "8000"],
      { cwd: repoRoot, stdio: "inherit", shell: false }
    );
    backend.on("error", (e) => {
      console.warn(
        `[dev-launcher] Could not start the backend (${e.message}).\n` +
          `  Start it manually in another terminal from the repo root:\n` +
          `    python -m uvicorn server.api_server:app --port 8000`
      );
      backend = null;
    });
    backend.on("exit", (code) => {
      if (code && code !== 0) {
        console.warn(
          `[dev-launcher] Backend exited (code ${code}). The dashboard's /api calls will fail until it runs.`
        );
      }
    });
  }

  const killBackend = () => {
    if (backend && !backend.killed) {
      try {
        backend.kill();
      } catch {
        /* ignore */
      }
    }
  };
  process.on("SIGINT", () => {
    killBackend();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    killBackend();
    process.exit(0);
  });

  // Run Vite (blocking until it exits), then tear the backend down.
  const result = spawnSync("npx", ["vite"], {
    cwd: viteCwd,
    stdio: "inherit",
    shell: true,
  });
  killBackend();
  process.exit(result.status || 0);
}

main();
