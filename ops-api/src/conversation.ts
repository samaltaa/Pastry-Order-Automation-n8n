import { OrderDraft } from "./rules.js";

export const HISTORY_LIMIT = 6;
export const STATE_TTL_HOURS = 2;

export type Turn = { role: "customer" | "bot"; text: string };

export type ConversationState = {
  draft: OrderDraft | null;
  history: Turn[];
};

export type ConversationContext = ConversationState & {
  savedAddress: string | null;
};

export function emptyState(): ConversationState {
  return { draft: null, history: [] };
}

export function isExpired(updatedAt: Date, now: Date): boolean {
  return now.getTime() - updatedAt.getTime() > STATE_TTL_HOURS * 3_600_000;
}

export function advance(
  state: ConversationState,
  message: string,
  result: { valid: boolean; draft: OrderDraft | null; clarification: string | null },
): ConversationState {
  const reply = result.valid ? "Placing the order now." : result.clarification ?? "";
  const history: Turn[] = [
    ...state.history,
    { role: "customer" as const, text: message },
    ...(reply ? [{ role: "bot" as const, text: reply }] : []),
  ].slice(-HISTORY_LIMIT);
  return { draft: result.draft ?? state.draft, history };
}
