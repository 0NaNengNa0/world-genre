// Triggers the genre_pipeline Airflow DAG once, on every `npm run start`.
//
// `docker compose up -d --wait` blocks until containers report healthy, but
// that only means the scheduler process is alive - the separate
// airflow-dag-processor service still needs a moment to parse
// dags/genre_pipeline_dag.py and register it before `airflow dags trigger`
// can find it. Rather than guess a fixed delay, retry the trigger itself
// for a bit - it's a cheap no-op once it succeeds.
//
// Triggering a DAG that's paused (the default in this project's
// docker-compose.yaml: AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=true)
// still runs it - "paused" only stops the *schedule* from auto-firing.
const { execSync } = require("child_process");

const MAX_ATTEMPTS = 15;
const DELAY_MS = 4000;

function sleepSync(ms) {
  const cmd =
    process.platform === "win32"
      ? `ping 127.0.0.1 -n ${Math.ceil(ms / 1000) + 1} > nul`
      : `sleep ${ms / 1000}`;
  execSync(cmd);
}

function tryTrigger() {
  try {
    execSync("docker compose exec -T airflow-scheduler airflow dags trigger genre_pipeline", {
      cwd: "backend",
      stdio: "pipe",
    });
    return true;
  } catch {
    return false;
  }
}

console.log("[genre_pipeline] waiting for Airflow to be ready to trigger...");
for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
  if (tryTrigger()) {
    console.log(`[genre_pipeline] triggered a run (attempt ${attempt}/${MAX_ATTEMPTS})`);
    process.exit(0);
  }
  console.log(`[genre_pipeline] not ready yet, retrying (${attempt}/${MAX_ATTEMPTS})...`);
  sleepSync(DELAY_MS);
}

console.warn(
  "[genre_pipeline] could not trigger automatically after retries - " +
    "trigger it manually from the Airflow UI (localhost:8080) or run:\n" +
    "  docker compose exec airflow-scheduler airflow dags trigger genre_pipeline"
);
process.exit(0); // don't fail `npm run start` just because the trigger didn't land
