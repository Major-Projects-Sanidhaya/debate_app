import { create } from 'zustand';

import type { VerdictMessage } from '@/api/fact-check';
import type { FactCheckMode, MatchFound, Stance } from '@/api/types';

export interface Selection {
  topicId: number;
  topicTitle: string;
  stance: Stance;
  mode: FactCheckMode;
}

interface SessionState {
  selection: Selection | null;
  match: MatchFound | null;
  // Fact-check agent state — in-memory only, reset on every match change.
  agentReady: boolean;
  requestInFlight: boolean;
  cooldownUntil: number | null; // epoch ms
  verdicts: VerdictMessage[]; // newest first
  unseenVerdicts: number;
  setSelection: (selection: Selection) => void;
  setMatch: (match: MatchFound) => void;
  clearMatch: () => void;
  setAgentReady: (ready: boolean) => void;
  setRequestInFlight: (inFlight: boolean) => void;
  setCooldownUntil: (until: number | null) => void;
  addVerdict: (verdict: VerdictMessage) => void;
  markVerdictsSeen: () => void;
}

const FACT_CHECK_RESET = {
  agentReady: false,
  requestInFlight: false,
  cooldownUntil: null,
  verdicts: [] as VerdictMessage[],
  unseenVerdicts: 0,
};

export const useSessionStore = create<SessionState>((set) => ({
  selection: null,
  match: null,
  ...FACT_CHECK_RESET,
  setSelection: (selection) => set({ selection }),
  setMatch: (match) => set({ match, ...FACT_CHECK_RESET }),
  // Next/End both clear the match, which also wipes the verdict feed.
  clearMatch: () => set({ match: null, ...FACT_CHECK_RESET }),
  setAgentReady: (agentReady) => set({ agentReady }),
  setRequestInFlight: (requestInFlight) => set({ requestInFlight }),
  setCooldownUntil: (cooldownUntil) => set({ cooldownUntil }),
  addVerdict: (verdict) =>
    set((s) => ({
      verdicts: [verdict, ...s.verdicts],
      unseenVerdicts: s.unseenVerdicts + 1,
    })),
  markVerdictsSeen: () => set({ unseenVerdicts: 0 }),
}));
