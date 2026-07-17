// Wire types for debate-api. These mirror the API contract exactly —
// do not rename fields.

export type Stance = 'pro' | 'con';
export type FactCheckMode = 'on_demand' | 'auto';

export interface Topic {
  id: number;
  title: string;
}

export interface AuthResponse {
  token: string;
  user_id: string;
}

export type ClientMessage =
  | { type: 'join'; topic_id: number; stance: Stance; fact_check_mode: FactCheckMode }
  | { type: 'cancel' };

export interface MatchFound {
  type: 'match_found';
  match_id: string;
  room_name: string;
  livekit_url: string;
  livekit_token: string;
  topic: Topic;
  your_stance: Stance;
  peer_stance: Stance;
  fact_check_mode: FactCheckMode;
}

export type ServerMessage =
  | { type: 'queued' }
  | MatchFound
  | { type: 'error'; message: string };

export function parseServerMessage(data: unknown): ServerMessage | null {
  if (typeof data !== 'string') return null;
  try {
    const msg = JSON.parse(data);
    if (msg && ['queued', 'match_found', 'error'].includes(msg.type)) {
      return msg as ServerMessage;
    }
  } catch {
    // fall through
  }
  return null;
}
