// Rebuilds the database from raw files ALREADY on disk, making no network
// calls at all.
//
// `npm run start` triggers the Airflow DAG, which runs every extractor:
// Last.fm (API key, one request per country plus one per artist), MusicBrainz
// (~1 req/sec, hours on a cold cache), Deezer, Wikidata and the kworb scrape.
// That's the right thing when you want fresh data and the wrong thing
// entirely when you just changed some CSS.
//
// This runs only the stages that read from data/raw and write to Postgres:
//     run_init_db  -> schema (idempotent)
//     run_cleanse  -> data/raw/**      -> data/processed/**
//     run_load     -> data/processed + data/raw/kworb -> Postgres
//
// No API keys are used and no quota is spent, so it's safe to run as often as
// you like.
const { execSync, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..");
const BACKEND = path.join(REPO_ROOT, "backend");
const RAW_DIR = path.join(BACKEND, "data", "raw");

// Matches the interpreter the other npm scripts use, with a POSIX fallback so
// this still works from WSL or a Mac.
const PYTHON = process.platform === "win32"
  ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
  : path.join(REPO_ROOT, ".venv", "bin", "python");

function fail(message) {
  console.error(`\n[offline] ${message}\n`);
  process.exit(1);
}

function countRawFiles() {
  if (!fs.existsSync(RAW_DIR)) return 0;
  let total = 0;
  for (const source of fs.readdirSync(RAW_DIR)) {
    const dir = path.join(RAW_DIR, source);
    if (!fs.statSync(dir).isDirectory()) continue;
    total += fs.readdirSync(dir).filter((f) => f.endsWith(".json")).length;
  }
  return total;
}

function run(label, args) {
  process.stdout.write(`[offline] ${label} ... `);
  const result = spawnSync(PYTHON, args, { cwd: BACKEND, encoding: "utf8" });
  if (result.status !== 0) {
    console.log("failed");
    // Python's own message is far more useful than anything this wrapper
    // could invent, so surface it rather than summarising it.
    console.error(result.stderr || result.stdout || "(no output)");
    fail(`${label} failed.`);
  }
  console.log("done");
}

if (!fs.existsSync(PYTHON)) {
  fail(
    `No virtualenv found at ${PYTHON}\n` +
      `Create one at the repo root and install deps:\n` +
      `  python -m venv .venv\n` +
      `  .venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt`,
  );
}

const rawFiles = countRawFiles();
if (rawFiles === 0) {
  fail(
    "data/raw is empty, so there's nothing to rebuild from.\n" +
      "This command deliberately makes no network calls - it reuses data a\n" +
      "previous run already fetched. Populate it once with a full run:\n" +
      "  npm run start\n" +
      "after which `npm run offline` works from the cached files.",
  );
}
console.log(`[offline] found ${rawFiles} raw files - no network calls needed`);

// The warehouse is the one dependency that must actually be running. Only
// app-postgres is started: Airflow's five containers are what makes a cold
// `npm run start` slow, and nothing here schedules a DAG.
process.stdout.write("[offline] starting app-postgres ... ");
try {
  execSync("docker compose up -d app-postgres --wait", {
    cwd: BACKEND,
    stdio: "pipe",
  });
  console.log("ready");
} catch (error) {
  console.log("failed");
  fail(
    "Could not start app-postgres. Is Docker Desktop running?\n" +
      String(error.stderr || error.message).trim(),
  );
}

run("applying schema", ["-m", "scripts.run_init_db"]);
run("cleansing raw data", ["-m", "scripts.run_cleanse"]);
run("loading into Postgres", ["-m", "scripts.run_load"]);

console.log("[offline] database rebuilt from cached data - starting app\n");
