// dev-launcher.cjs
// Workaround: Vite 7 breaks when the project path contains '#' (treated as a
// URL fragment in internal file:// URLs). This launcher creates a Windows
// junction at a '#'-free path, changes CWD to that junction, and then starts
// Vite — so Vite's root (= CWD) never contains '#'.

const { execSync } = require("child_process");
const { existsSync } = require("fs");
const { createHash } = require("crypto");
const { spawnSync } = require("child_process");

const dir = process.cwd();

if (dir.includes("#")) {
  const tag = createHash("md5").update(dir).digest("hex").slice(0, 8);
  const junction = `C:\\vj${tag}`;

  if (!existsSync(junction)) {
    try {
      execSync(
        `powershell -NoProfile -NonInteractive -Command ` +
          `"New-Item -ItemType Junction -Path '${junction}' -Target '${dir}' | Out-Null"`,
        { stdio: "pipe" }
      );
    } catch (e) {
      console.warn("[dev-launcher] Junction creation failed:", e.message);
    }
  }

  if (existsSync(junction)) {
    console.log(`[dev-launcher] Launching from junction: ${junction}`);
    process.chdir(junction);
  }
}

// Run Vite in the (possibly changed) CWD.
// spawnSync blocks until Vite exits, which is correct for a dev server launcher.
const result = spawnSync("npx", ["vite"], {
  stdio: "inherit",
  shell: true,
});

process.exit(result.status || 0);
