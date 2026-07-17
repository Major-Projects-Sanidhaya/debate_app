import type { AuthResponse, Topic } from '@/api/types';

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
};

export function matchmakingWsUrl(token: string): string {
  const wsBase = API_URL.replace(/^http/, 'ws');
  return `${wsBase}/ws/match?token=${encodeURIComponent(token)}`;
}
