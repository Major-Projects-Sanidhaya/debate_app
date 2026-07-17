import * as Crypto from 'expo-crypto';
import { create } from 'zustand';

import { api, ApiError } from '@/api/client';
import { deleteItem, getItem, setItem } from '@/lib/storage';

const KEYS = { deviceId: 'device_id', token: 'auth_token', userId: 'user_id' } as const;

type AuthStatus =
  | 'loading' // reading SecureStore on launch
  | 'needs_gate' // no token yet — show the age gate
  | 'authenticating' // age gate accepted, POST /auth/device in flight
  | 'blocked' // server said 403 (banned)
  | 'ready';

interface AuthState {
  status: AuthStatus;
  deviceId: string | null;
  token: string | null;
  userId: string | null;
  error: string | null;
  bootstrap: () => Promise<void>;
  attestAndAuth: () => Promise<void>;
  reauth: () => Promise<string | null>;
  signOutLocally: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: 'loading',
  deviceId: null,
  token: null,
  userId: null,
  error: null,

  bootstrap: async () => {
    let deviceId = await getItem(KEYS.deviceId);
    if (!deviceId) {
      deviceId = Crypto.randomUUID();
      await setItem(KEYS.deviceId, deviceId);
    }
    const [token, userId] = await Promise.all([getItem(KEYS.token), getItem(KEYS.userId)]);
    if (token && userId) {
      set({ status: 'ready', deviceId, token, userId });
    } else {
      set({ status: 'needs_gate', deviceId });
    }
  },

  attestAndAuth: async () => {
    const { deviceId } = get();
    if (!deviceId) return;
    set({ status: 'authenticating', error: null });
    try {
      const res = await api.authDevice(deviceId);
      await Promise.all([setItem(KEYS.token, res.token), setItem(KEYS.userId, res.user_id)]);
      set({ status: 'ready', token: res.token, userId: res.user_id });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        set({ status: 'blocked', error: err.message });
      } else {
        set({ status: 'needs_gate', error: 'Could not reach the server. Check your connection.' });
      }
    }
  },

  // Silent re-auth for expired tokens: the device already passed the age
  // gate (it had a token), so no UI is needed unless the server blocks us.
  reauth: async () => {
    const { deviceId } = get();
    if (!deviceId) return null;
    try {
      const res = await api.authDevice(deviceId);
      await Promise.all([setItem(KEYS.token, res.token), setItem(KEYS.userId, res.user_id)]);
      set({ status: 'ready', token: res.token, userId: res.user_id });
      return res.token;
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        set({ status: 'blocked', error: err.message, token: null, userId: null });
      }
      return null;
    }
  },

  signOutLocally: async () => {
    await Promise.all([deleteItem(KEYS.token), deleteItem(KEYS.userId)]);
    set({ status: 'needs_gate', token: null, userId: null });
  },
}));
