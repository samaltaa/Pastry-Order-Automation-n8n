import { readdir, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Pool } from "pg";

const DB_DIR = process.env.DB_DIR ?? resolve(dirname(fileURLToPath(import.meta.url)), "../../../db");
const LOCK_ID = 7_270_001;

export async function migrate(pool: Pool): Promise<string[]> {
  const client = await pool.connect();
  try {
    await client.query("SELECT pg_advisory_lock($1)", [LOCK_ID]);
    await client.query(
      "CREATE TABLE IF NOT EXISTS schema_migrations (name VARCHAR(200) PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())"
    );
    const done = new Set((await client.query("SELECT name FROM schema_migrations")).rows.map((row) => row.name));
    const files = (await readdir(join(DB_DIR, "migrations"))).filter((file) => file.endsWith(".sql")).sort();

    const applied: string[] = [];
    for (const file of files) {
      if (done.has(file)) continue;
      const sql = await readFile(join(DB_DIR, "migrations", file), "utf8");
      await client.query("BEGIN");
      try {
        await client.query(sql);
        await client.query("INSERT INTO schema_migrations (name) VALUES ($1)", [file]);
        await client.query("COMMIT");
      } catch (error) {
        await client.query("ROLLBACK");
        throw new Error(`Migration ${file} failed: ${(error as Error).message}`);
      }
      applied.push(file);
    }

    await client.query(await readFile(join(DB_DIR, "seed.sql"), "utf8"));
    return applied;
  } finally {
    await client.query("SELECT pg_advisory_unlock($1)", [LOCK_ID]).catch(() => undefined);
    client.release();
  }
}
