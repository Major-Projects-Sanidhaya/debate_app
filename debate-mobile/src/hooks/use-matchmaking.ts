import { useCallback, useEffect, useRef, useState } from 'react';

import { matchmakingWsUrl } from '@/api/client';
import type { ClientMessage, MatchFound } from '@/api/types';
import { parseServerMessage } from '@/api/types';
import type { Selection } from '@/state/session-store';

export type MatchmakingStatus = 'connecting' | 'searching' | 'reconnecting' | 'failed';

interface MatchmakingState {
  status: MatchmakingStatus;
  error: string | null;
  attempt: number;
}

const MAX_BACKOFF_MS = 15_000;
const backoffDelay = (attempt: number) =>
  Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS) + Math.random() * 500;

/**
 * Owns the matchmaking websocket lifecycle: connect, join, reconnect with
 * backoff, cancel, and cleanup. One socket per hook instance, one join per
 * connection — the client-side guard against double joins. Passing a null
 * selection makes the hook inert.
 */
export function useMatchmaking(
  selection: Selection | null,
  token: string | null,
  onMatch: (match: MatchFound) => void,
) {
  const [state, setState] = useState<MatchmakingState>({
    status: 'connecting',
    error: null,
    attempt: 0,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const doneRef = useRef(false); // matched or cancelled: stop reconnecting
  const onMatchRef = useRef(onMatch);
  onMatchRef.current = onMatch;

  useEffect(() => {
    if (!selection || !token) return;
    doneRef.current = false;
    let attempt = 0;

    const connect = () => {
      if (doneRef.current) return;
      setState({ status: attempt === 0 ? 'connecting' : 'reconnecting', error: null, attempt });
      const ws = new WebSocket(matchmakingWsUrl(token));
      wsRef.current = ws;
      let joined = false;

      ws.onopen = () => {
        if (joined) return; // never double-join on one connection
        joined = true;
        const join: ClientMessage = {
          type: 'join',
          topic_id: selection.topicId,
          stance: selection.stance,
          fact_check_mode: selection.mode,
        };
        ws.send(JSON.stringify(join));
      };

      ws.onmessage = (event) => {
        const msg = parseServerMessage(event.data);
        if (!msg) return;
        if (msg.type === 'queued') {
          attempt = 0; // healthy connection: reset backoff
          setState({ status: 'searching', error: null, attempt: 0 });
        } else if (msg.type === 'match_found') {
          doneRef.current = true;
          onMatchRef.current(msg);
          ws.close();
        } else if (msg.type === 'error') {
          // e.g. "already in queue" from a not-yet-reaped previous socket:
          // surface it and let the retry cycle recover.
          setState((s) => ({ ...s, error: msg.message }));
        }
      };

      ws.onclose = () => {
        if (doneRef.current || wsRef.current !== ws) return;
        const delay = backoffDelay(attempt);
        attempt += 1;
        setState((s) => ({ status: 'reconnecting', error: s.error, attempt }));
        timerRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // RN fires onerror followed by onclose; onclose drives the retry.
      };
    };

    connect();

    return () => {
      doneRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          const cancel: ClientMessage = { type: 'cancel' };
          ws.send(JSON.stringify(cancel));
        } catch {
          // socket already going away — server cleans up on disconnect too
        }
      }
      ws?.close();
    };
  }, [selection, token]);

  // Explicit cancel for the Cancel button; navigation unmount does the same.
  const cancel = useCallback(() => {
    doneRef.current = true;
    if (timerRef.current) clearTimeout(timerRef.current);
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: 'cancel' } satisfies ClientMessage));
      } catch {
        // ignore
      }
    }
    ws?.close();
  }, []);

  return { ...state, cancel };
}
