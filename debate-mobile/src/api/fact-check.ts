// Data-channel contract with debate-agent (LiveKit topic "fact_check").
// Field names mirror the contract exactly — do not rename.

import type { FactCheckMode, Stance } from '@/api/types';

export const FACT_CHECK_TOPIC = 'fact_check';
export const AGENT_IDENTITY = 'fc-agent';

export type VerdictValue = 'true' | 'false' | 'misleading' | 'unverifiable';
export type VerdictConfidence = 'high' | 'medium' | 'low';

export interface VerdictSource {
  title: string;
  url: string;
}

export interface AgentReadyMessage {
  type: 'agent_ready';
}

export interface FactCheckStatusMessage {
  type: 'fact_check_status';
  request_id: string;
  status: 'checking';
  target_stance: Stance;
}

export interface VerdictMessage {
  type: 'verdict';
  request_id: string;
  match_id: string;
  claim: string;
  verdict: VerdictValue;
  confidence: VerdictConfidence;
  summary: string;
  sources: VerdictSource[];
  speaker_stance: Stance;
  mode: FactCheckMode;
  ts: number;
}

export interface FactCheckErrorMessage {
  type: 'fact_check_error';
  request_id: string;
  message: string;
}

export type AgentMessage =
  | AgentReadyMessage
  | FactCheckStatusMessage
  | VerdictMessage
  | FactCheckErrorMessage;

export type FactCheckRequestMessage = { type: 'fact_check_request' };

const VERDICT_VALUES: readonly string[] = ['true', 'false', 'misleading', 'unverifiable'];
const CONFIDENCES: readonly string[] = ['high', 'medium', 'low'];
const STANCES: readonly string[] = ['pro', 'con'];
const MODES: readonly string[] = ['on_demand', 'auto'];

// Hermes gained TextEncoder/TextDecoder late; fall back to manual UTF-8 so
// data-channel parsing can't crash on older runtimes.

export function encodeUtf8(text: string): Uint8Array {
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(text);
  const bytes: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const code = text.codePointAt(i)!;
    if (code > 0xffff) i++;
    if (code < 0x80) bytes.push(code);
    else if (code < 0x800) bytes.push(0xc0 | (code >> 6), 0x80 | (code & 63));
    else if (code < 0x10000)
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 63), 0x80 | (code & 63));
    else
      bytes.push(
        0xf0 | (code >> 18),
        0x80 | ((code >> 12) & 63),
        0x80 | ((code >> 6) & 63),
        0x80 | (code & 63),
      );
  }
  return Uint8Array.from(bytes);
}

export function decodeUtf8(data: Uint8Array): string {
  if (typeof TextDecoder !== 'undefined') return new TextDecoder().decode(data);
  let out = '';
  let i = 0;
  while (i < data.length) {
    const b = data[i];
    if (b < 0x80) {
      out += String.fromCharCode(b);
      i += 1;
    } else if (b < 0xe0) {
      out += String.fromCharCode(((b & 31) << 6) | (data[i + 1] & 63));
      i += 2;
    } else if (b < 0xf0) {
      out += String.fromCharCode(
        ((b & 15) << 12) | ((data[i + 1] & 63) << 6) | (data[i + 2] & 63),
      );
      i += 3;
    } else {
      out += String.fromCodePoint(
        ((b & 7) << 18) | ((data[i + 1] & 63) << 12) | ((data[i + 2] & 63) << 6) | (data[i + 3] & 63),
      );
      i += 4;
    }
  }
  return out;
}

function parseSources(raw: unknown): VerdictSource[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(
      (s): s is VerdictSource =>
        !!s && typeof s === 'object' && typeof s.title === 'string' && typeof s.url === 'string',
    )
    .map((s) => ({ title: s.title, url: s.url }));
}

/** Defensive parse of agent -> room messages; returns null for anything off-contract. */
export function parseAgentMessage(payload: Uint8Array): AgentMessage | null {
  let msg: unknown;
  try {
    msg = JSON.parse(decodeUtf8(payload));
  } catch {
    return null;
  }
  if (!msg || typeof msg !== 'object') return null;
  const m = msg as Record<string, unknown>;

  switch (m.type) {
    case 'agent_ready':
      return { type: 'agent_ready' };

    case 'fact_check_status':
      if (
        typeof m.request_id === 'string' &&
        m.status === 'checking' &&
        typeof m.target_stance === 'string' &&
        STANCES.includes(m.target_stance)
      ) {
        return {
          type: 'fact_check_status',
          request_id: m.request_id,
          status: 'checking',
          target_stance: m.target_stance as Stance,
        };
      }
      return null;

    case 'verdict':
      if (
        typeof m.request_id === 'string' &&
        typeof m.match_id === 'string' &&
        typeof m.claim === 'string' &&
        typeof m.verdict === 'string' &&
        VERDICT_VALUES.includes(m.verdict) &&
        typeof m.confidence === 'string' &&
        CONFIDENCES.includes(m.confidence) &&
        typeof m.summary === 'string' &&
        typeof m.speaker_stance === 'string' &&
        STANCES.includes(m.speaker_stance) &&
        typeof m.mode === 'string' &&
        MODES.includes(m.mode) &&
        typeof m.ts === 'number'
      ) {
        return {
          type: 'verdict',
          request_id: m.request_id,
          match_id: m.match_id,
          claim: m.claim,
          verdict: m.verdict as VerdictValue,
          confidence: m.confidence as VerdictConfidence,
          summary: m.summary,
          sources: parseSources(m.sources),
          speaker_stance: m.speaker_stance as Stance,
          mode: m.mode as FactCheckMode,
          ts: m.ts,
        };
      }
      return null;

    case 'fact_check_error':
      if (typeof m.request_id === 'string' && typeof m.message === 'string') {
        return { type: 'fact_check_error', request_id: m.request_id, message: m.message };
      }
      return null;

    default:
      return null; // unknown types are ignored by design
  }
}
