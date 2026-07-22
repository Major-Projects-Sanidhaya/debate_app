import type { AuthResponse, ReportBody, Topic } from '@/api/types';
import { REPORT_DETAILS_MAX } from '@/api/types';

/** debate-api's `detail` for a banned device/user, on REST and the WS alike. */
export const SUSPENDED_DETAIL = 'account_suspended';

export const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000').replace(
  /\/+$/,
  '',
);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  token?: string;
}

async function request<T>(path: string, { method = 'GET', body, token }: RequestOptions = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') detail = data.detail;
    } catch {
      // keep generic detail
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  authDevice: (deviceId: string) =>
    request<AuthResponse>('/auth/device', {
      method: 'POST',
      body: { device_id: deviceId, over_18: true },
    }),

  getTopics: (token: string) => request<Topic[]>('/topics', { token }),

  endMatch: (token: string, matchId: string) =>
    request<void>(`/matches/${matchId}/end`, { method: 'POST', token }),

  reportMatch: (token: string, matchId: string, body: ReportBody) => {
    const details = body.details?.trim();
    return request<void>(`/matches/${matchId}/report`, {
      method: 'POST',
      token,
      body: {
        reason: body.reason,
        // Omit empty details; hard-cap length so the server never 422s on it.
        ...(details ? { details: details.slice(0, REPORT_DETAILS_MAX) } : {}),
      },
    });
  },

  blockOpponent: (token: string, matchId: string) =>
    request<void>(`/matches/${matchId}/block`, { method: 'POST', token }),
};

/** True only for a suspension 403 — not for "not a participant" 403s. */
export function isSuspendedError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403 && err.message === SUSPENDED_DETAIL;
}

/** Websocket origin derived from API_URL's scheme: https -> wss, http -> ws. */
export function matchmakingWsUrl(token: string): string {
  // Derived from the single configured base URL, so a build can never pair a
  // production API with a development socket.
  const wsBase = API_URL.startsWith('https://')
    ? `wss://${API_URL.slice('https://'.length)}`
    : API_URL.startsWith('http://')
      ? `ws://${API_URL.slice('http://'.length)}`
      : API_URL;
  return `${wsBase}/ws/match?token=${encodeURIComponent(token)}`;
}
