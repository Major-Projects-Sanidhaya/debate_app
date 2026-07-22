import { create } from 'zustand';

import { getItem, setItem } from '@/lib/storage';

const KEY = 'guidelines_accepted_v1';

interface GuidelinesState {
  /** null while reading SecureStore on launch. */
  accepted: boolean | null;
  load: () => Promise<void>;
  accept: () => Promise<void>;
}

export const useGuidelinesStore = create<GuidelinesState>((set) => ({
  accepted: null,

  load: async () => {
    const stored = await getItem(KEY);
    set({ accepted: stored === 'true' });
  },

  accept: async () => {
    // Optimistic: the gate closes immediately; a failed write only means the
    // user is asked once more on a later launch.
    set({ accepted: true });
    await setItem(KEY, 'true');
  },
}));
