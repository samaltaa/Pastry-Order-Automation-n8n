import { timingSafeEqual } from "node:crypto";
import Fastify, { FastifyServerOptions } from "fastify";
import { Pool } from "pg";
import { z } from "zod";
import { Config } from "./config.js";
import { llmRoutes } from "./routes/llm.js";
import { orderRoutes } from "./routes/orders.js";
import { reportRoutes } from "./routes/reports.js";
import { systemRoutes } from "./routes/system.js";

export type Deps = { pool: Pool; config: Config };

function sameSecret(given: unknown, expected: string): boolean {
  if (typeof given !== "string") return false;
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function buildApp(deps: Deps, options: FastifyServerOptions = { logger: true }) {
  const app = Fastify(options);

  app.addHook("onRequest", async (request, reply) => {
    if (request.url === "/health") return;
    if (!sameSecret(request.headers["x-api-key"], deps.config.apiKey)) {
      return reply.code(401).send({ ok: false, message: "Invalid API key." });
    }
  });

  app.setErrorHandler((error, request, reply) => {
    if (error instanceof z.ZodError) {
      reply.code(400).send({ ok: false, message: "Invalid request.", issues: error.issues });
      return;
    }
    request.log.error(error);
    reply.code(500).send({ ok: false, message: "Internal error." });
  });

  app.register(async (scope) => {
    await systemRoutes(scope, deps);
    await llmRoutes(scope, deps);
    await orderRoutes(scope, deps);
    await reportRoutes(scope, deps);
  });

  return app;
}
