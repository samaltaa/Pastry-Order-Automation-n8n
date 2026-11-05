import { Pool } from "pg";
import { buildApp } from "./app.js";
import { loadConfig } from "./config.js";
import { migrate } from "./db/migrate.js";

const config = loadConfig();
const pool = new Pool({ connectionString: config.databaseUrl, max: 10 });
const app = buildApp({ pool, config });

try {
  const applied = await migrate(pool);
  app.log.info({ applied }, applied.length ? "migrations applied" : "database up to date");
  await app.listen({ port: config.port, host: "0.0.0.0" });
} catch (error) {
  app.log.error(error, "failed to start");
  process.exit(1);
}

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, async () => {
    app.log.info({ signal }, "shutting down");
    await app.close();
    await pool.end();
    process.exit(0);
  });
}
