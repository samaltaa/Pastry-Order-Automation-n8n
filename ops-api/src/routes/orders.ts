import { FastifyInstance } from "fastify";
import { z } from "zod";
import type { Deps } from "../app.js";
import { createOrder, orderWithItems } from "../services/orders.js";
import { MAX_QUANTITY } from "../services/rules.js";

const orderSchema = z.object({
  customer_name: z.string().min(2),
  customer_phone: z.string().min(3),
  delivery_address: z.string().min(4),
  delivery_time: z.string().min(3),
  items: z
    .array(z.object({ item_id: z.number().int().positive(), quantity: z.number().int().positive().max(MAX_QUANTITY) }))
    .min(1),
  notes: z.string().nullish(),
  idempotency_key: z.string().min(1),
  chat_id: z.string().optional(),
});

const idSchema = z.object({ id: z.coerce.number().int().positive() });

export async function orderRoutes(app: FastifyInstance, { pool }: Deps) {
  app.post("/orders", async (request, reply) => {
    const result = await createOrder(pool, orderSchema.parse(request.body));
    if (result.status === "unknown_items") {
      reply.code(400);
      return { ok: false, message: "Unknown menu item(s): " + result.missing.join(", ") };
    }
    return { ok: true, duplicate: result.status === "duplicate", ...(await orderWithItems(pool, result.orderId)) };
  });

  app.get("/orders/:id", async (request, reply) => {
    const { id } = idSchema.parse(request.params);
    const found = await pool.query("SELECT id FROM orders WHERE id = $1", [id]);
    if (found.rowCount === 0) {
      reply.code(404);
      return { ok: false, message: "Order not found." };
    }
    return { ok: true, ...(await orderWithItems(pool, id)) };
  });

  app.post("/orders/:id/status", async (request, reply) => {
    const { id } = idSchema.parse(request.params);
    const { status } = z
      .object({ status: z.enum(["pending", "baking", "out_for_delivery", "delivered", "cancelled"]) })
      .parse(request.body);
    const result = await pool.query(
      "UPDATE orders SET status = $1, updated_at = NOW() WHERE id = $2 RETURNING *",
      [status, id]
    );
    if (result.rowCount === 0) {
      reply.code(404);
      return { ok: false, message: "Order not found." };
    }
    return { ok: true, order: result.rows[0] };
  });
}
