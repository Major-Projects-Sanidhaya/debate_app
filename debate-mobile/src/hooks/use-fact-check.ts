import { useDataChannel } from '@livekit/components-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { encodeUtf8, FACT_CHECK_TOPIC, parseAgentMessage } from '@/api/fact-check';
import { useSessionStore } from '@/state/session-store';

export type AgentChipStatus = 'connecting' | 'active' | 'unavailable';
export type RequestPhase = 'idle' | 'requesting' | 'checking';

const AGENT_READY_TIMEOUT_MS = 10_000;
// No reply at all to a request -> the agent is likely gone.
const REQUEST_ACK_TIMEOUT_MS = 15_000;
// Status arrived, pipeline is running (search + up to 2 claims with retries).
const CHECK_RESULT_TIMEOUT_MS = 60_000;
const COOLDOWN_MS = 10_000; // mirrors the server-side cooldown

/**
 * Owns the fact-check agent lifecycle on the data channel: readiness chip,
 * request phases, response timeouts (the only way to notice a dead hidden
 * participant), and the client-side cooldown.
 */
export function useFactCheck(onError: (message: string) => void) {
  const [chipStatus, setChipStatus] = useState<AgentChipStatus>('connecting');
  const [phase, setPhase] = useState<RequestPhase>('idle');
  const chipRef = useRef<AgentChipStatus>('connecting');
  const phaseRef = useRef<RequestPhase>('idle');
  const readyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const responseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const setAgentReady = useSessionStore((s) => s.setAgentReady);
  const setRequestInFlight = useSessionStore((s) => s.setRequestInFlight);
  const setCooldownUntil = useSessionStore((s) => s.setCooldownUntil);
  const addVerdict = useSessionStore((s) => s.addVerdict);

  const setChip = useCallback(
    (status: AgentChipStatus) => {
      chipRef.current = status;
      setChipStatus(status);
      setAgentReady(status === 'active');
    },
    [setAgentReady],
  );

  const updatePhase = useCallback(
    (next: RequestPhase) => {
      phaseRef.current = next;
      setPhase(next);
      setRequestInFlight(next !== 'idle');
    },
    [setRequestInFlight],
  );

  const clearResponseTimer = useCallback(() => {
    if (responseTimerRef.current) {
      clearTimeout(responseTimerRef.current);
      responseTimerRef.current = null;
    }
  }, []);

  const markActive = useCallback(() => {
    if (readyTimerRef.current) {
      clearTimeout(readyTimerRef.current);
      readyTimerRef.current = null;
    }
    if (chipRef.current !== 'active') setChip('active');
  }, [setChip]);

  const finishRequest = useCallback(() => {
    clearResponseTimer();
    if (phaseRef.current !== 'idle') {
      updatePhase('idle');
      setCooldownUntil(Date.now() + COOLDOWN_MS);
    }
  }, [clearResponseTimer, setCooldownUntil, updatePhase]);

  const failRequest = useCallback(
    (message: string) => {
      clearResponseTimer();
      updatePhase('idle');
      // A request the agent never answered is our only signal that the
      // hidden participant is gone — degrade to "unavailable".
      setChip('unavailable');
      onErrorRef.current(message);
    },
    [clearResponseTimer, setChip, updatePhase],
  );

  const { send } = useDataChannel(FACT_CHECK_TOPIC, (dataMessage) => {
    const msg = parseAgentMessage(dataMessage.payload);
    if (msg === null) return; // off-contract payloads are ignored
    markActive(); // any valid agent message proves it is alive

    if (msg.type === 'fact_check_status') {
      if (phaseRef.current === 'requesting') {
        updatePhase('checking');
        clearResponseTimer();
        responseTimerRef.current = setTimeout(
          () => failRequest('The fact-check timed out.'),
          CHECK_RESULT_TIMEOUT_MS,
        );
      }
    } else if (msg.type === 'verdict') {
      addVerdict(msg); // AUTO verdicts land here too, outside any request
      if (phaseRef.current !== 'idle') finishRequest();
    } else if (msg.type === 'fact_check_error') {
      onErrorRef.current(msg.message);
      if (phaseRef.current !== 'idle') finishRequest();
    }
  });

  useEffect(() => {
    readyTimerRef.current = setTimeout(() => {
      if (chipRef.current !== 'active') setChip('unavailable');
    }, AGENT_READY_TIMEOUT_MS);
    return () => {
      if (readyTimerRef.current) clearTimeout(readyTimerRef.current);
      if (responseTimerRef.current) clearTimeout(responseTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const requestFactCheck = useCallback(() => {
    if (chipRef.current !== 'active' || phaseRef.current !== 'idle') return;
    const { cooldownUntil } = useSessionStore.getState();
    if (cooldownUntil !== null && Date.now() < cooldownUntil) return;

    updatePhase('requesting');
    responseTimerRef.current = setTimeout(
      () => failRequest('The fact-checker is not responding.'),
      REQUEST_ACK_TIMEOUT_MS,
    );
    send(encodeUtf8(JSON.stringify({ type: 'fact_check_request' })), { reliable: true }).catch(
      () => failRequest('Could not send the fact-check request.'),
    );
  }, [failRequest, send, updatePhase]);

  return { chipStatus, phase, requestFactCheck };
}
