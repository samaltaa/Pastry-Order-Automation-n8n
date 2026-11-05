export const TIMEZONE = "America/Santo_Domingo";
export const UTC_OFFSET = "-04:00";
export const OPEN_HOUR = 8;
export const CLOSE_HOUR = 18;
export const MIN_LEAD_HOURS = 2;
export const MAX_DAYS_AHEAD = 14;
export const MAX_QUANTITY = 100;

export type MenuItem = { id: number; item_name: string; category: string; price: number };

export type OrderDraft = {
  items: { item_id: number; quantity: number }[];
  unmatched_items: string[];
  needs_clarification: boolean;
  delivery_address: string | null;
  delivery_at: string | null;
  customer_name: string | null;
  notes: string | null;
  confidence: number;
  clarification: string | null;
};

export type Violation = { code: string; message: string; modelFault: boolean };

export function localNow(now: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: TIMEZONE,
    weekday: "long",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function localDate(now: Date): string {
  return localNow(now).split(" ")[1].slice(0, 10);
}

export function checkDraft(draft: OrderDraft, menu: MenuItem[], now: Date): Violation[] {
  const violations: Violation[] = [];
  const menuIds = new Set(menu.map((m) => m.id));

  if (draft.unmatched_items.length > 0) {
    violations.push({
      code: "off_menu",
      message: `Sorry, we don't have ${draft.unmatched_items.join(", ")}. Send /menu to see what we bake.`,
      modelFault: false,
    });
  }
  if (draft.needs_clarification) {
    violations.push({ code: "ambiguous", message: "Could you clarify your order?", modelFault: false });
  }
  if (draft.items.length === 0 && draft.unmatched_items.length === 0) {
    violations.push({ code: "no_items", message: "What would you like to order? Send /menu to see options.", modelFault: false });
  }
  for (const item of draft.items) {
    if (!menuIds.has(item.item_id)) {
      violations.push({ code: "unknown_item_id", message: "I couldn't match one of the items.", modelFault: true });
    }
    if (!Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > MAX_QUANTITY) {
      violations.push({
        code: "bad_quantity",
        message: `Quantities must be between 1 and ${MAX_QUANTITY}.`,
        modelFault: false,
      });
    }
  }
  if (!draft.delivery_address) {
    violations.push({ code: "no_address", message: "What address should we deliver to?", modelFault: false });
  }
  if (!draft.delivery_at) {
    violations.push({ code: "no_time", message: "What day and time would you like it delivered?", modelFault: false });
  } else {
    violations.push(...checkDeliveryTime(draft.delivery_at, now));
  }
  return violations;
}

export function checkDeliveryTime(deliveryAt: string, now: Date): Violation[] {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(deliveryAt)) {
    return [{ code: "bad_datetime", message: "What day and time would you like it delivered?", modelFault: true }];
  }
  const when = new Date(`${deliveryAt}:00${UTC_OFFSET}`);
  if (Number.isNaN(when.getTime())) {
    return [{ code: "bad_datetime", message: "What day and time would you like it delivered?", modelFault: true }];
  }
  const [hour, minute] = deliveryAt.slice(11).split(":").map(Number);
  const minutes = hour * 60 + minute;
  if (minutes < OPEN_HOUR * 60 || minutes > CLOSE_HOUR * 60) {
    return [{
      code: "outside_hours",
      message: `We deliver between ${OPEN_HOUR}:00 and ${CLOSE_HOUR}:00. What time works for you?`,
      modelFault: false,
    }];
  }
  const hoursAhead = (when.getTime() - now.getTime()) / 3_600_000;
  if (hoursAhead < MIN_LEAD_HOURS) {
    return [{
      code: "too_soon",
      message: `We need at least ${MIN_LEAD_HOURS} hours to bake. Could you pick a later time?`,
      modelFault: false,
    }];
  }
  if (hoursAhead > MAX_DAYS_AHEAD * 24) {
    return [{
      code: "too_far",
      message: `We take orders up to ${MAX_DAYS_AHEAD} days ahead. Could you pick an earlier date?`,
      modelFault: false,
    }];
  }
  return [];
}

export function clarificationFor(draft: OrderDraft, violations: Violation[], savedAddress: string | null = null): string {
  const policy = violations.find((v) => ["off_menu", "outside_hours", "too_soon", "too_far", "bad_quantity"].includes(v.code));
  if (policy) return policy.message;
  if (savedAddress && violations.some((v) => v.code === "no_address") && violations.every((v) => ["no_address", "ambiguous"].includes(v.code))) {
    return `¿Lo enviamos a ${savedAddress} otra vez? / Same address as last time? Reply "sí" or send a new address.`;
  }
  return draft.clarification ?? violations[0]?.message ?? "Could you tell me a bit more about your order?";
}
