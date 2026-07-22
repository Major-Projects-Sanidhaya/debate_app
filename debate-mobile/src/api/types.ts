// Wire types for debate-api. These mirror the API contract exactly —
// do not rename fields.

export type Stance = 'pro' | 'con';
export type FactCheckMode = 'on_demand' | 'auto';

/** Report reasons — the enum values are the API contract; labels are ours. */
export type ReportReason =
  | 'harassment'
  | 'hate_speech'
  | 'sexual_content'
  | 'violence_threat'
  | 'underage'
  | 'spam_other';

export const REPORT_REASONS: { value: ReportReason; label: string }[] = [
  { value: 'harassment', label: 'Harassment or bullying' },
  { value: 'hate_speech', label: 'Hate speech' },
  { value: 'sexual_content', label: 'Sexual content or nudity' },
  { value: 'violence_threat', label: 'Threats of violence' },
  { value: 'underage', label: 'They appear to be under 18' },
  { value: 'spam_other', label: 'Spam or something else' },
];

export const REPORT_DETAILS_MAX = 500;

export interface ReportBody {
  reason: ReportReason;
  details?: string;
}

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
