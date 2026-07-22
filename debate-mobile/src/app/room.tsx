import {
  isTrackReference,
  useLocalParticipant,
  useRemoteParticipants,
  useRoomContext,
  useTracks,
} from '@livekit/components-react';
import { AudioSession, LiveKitRoom, VideoTrack } from '@livekit/react-native';
import { useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { Redirect, router } from 'expo-router';
import { DisconnectReason, RoomEvent, Track } from 'livekit-client';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BackHandler,
  Linking,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { api } from '@/api/client';
import { AGENT_IDENTITY } from '@/api/fact-check';
import type { MatchFound, ReportReason } from '@/api/types';
import { FactCheckButton, FactCheckChip, Toast, VerdictSheet } from '@/components/fact-check';
import { ReportModal } from '@/components/report-modal';
import { Button, colors, Screen } from '@/components/ui';
import { useFactCheck } from '@/hooks/use-fact-check';
import { useAuthStore } from '@/state/auth-store';
import { useSessionStore } from '@/state/session-store';

export default function RoomScreen() {
  const match = useSessionStore((s) => s.match);
  const clearMatch = useSessionStore((s) => s.clearMatch);
  const token = useAuthStore((s) => s.token);

  const [camPerm, requestCam] = useCameraPermissions();
  const [micPerm, requestMic] = useMicrophonePermissions();
  const [audioReady, setAudioReady] = useState(Platform.OS === 'web');
  const [connectionLost, setConnectionLost] = useState(false);

  // Synchronous guard: survives rapid Next/End spamming, where state alone
  // would let two taps through before the next render.
  const leavingRef = useRef(false);

  useEffect(() => {
    if (camPerm && !camPerm.granted && camPerm.canAskAgain) void requestCam();
  }, [camPerm, requestCam]);
  useEffect(() => {
    if (micPerm && !micPerm.granted && micPerm.canAskAgain) void requestMic();
  }, [micPerm, requestMic]);

  useEffect(() => {
    if (Platform.OS === 'web') return;
    let active = true;
    void AudioSession.startAudioSession().then(() => {
      if (active) setAudioReady(true);
    });
    return () => {
      active = false;
      void AudioSession.stopAudioSession();
    };
  }, []);

  const leave = (destination: '/' | '/queue') => {
    if (leavingRef.current || !match) return;
    leavingRef.current = true;
    if (token) {
      // Fire and forget: ending is idempotent server-side, and the room
      // teardown must not block on the network.
      void api.endMatch(token, match.match_id).catch(() => {});
    }
    clearMatch();
    router.replace(destination);
  };
  const endToHome = () => leave('/');
  // Next: end this match and requeue with the same topic/stance/mode —
  // the selection is still in the session store, so /queue re-joins directly.
  const nextOpponent = () => leave('/queue');

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      endToHome();
      return true;
    });
    return () => sub.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match, token]);

  if (!match) return <Redirect href="/" />;

  const stillRequesting =
    !camPerm ||
    !micPerm ||
    (!camPerm.granted && camPerm.canAskAgain) ||
    (!micPerm.granted && micPerm.canAskAgain);
  if (stillRequesting || !audioReady) {
    return (
      <Screen style={styles.centerScreen}>
        <Text style={styles.dim}>Preparing camera and microphone…</Text>
      </Screen>
    );
  }

  if (!camPerm.granted || !micPerm.granted) {
    return (
      <Screen style={styles.centerScreen}>
        <Text style={styles.permTitle}>Camera & microphone needed</Text>
        <Text style={styles.permCopy}>
          A video debate needs both. Enable Camera and Microphone for Debate in your device
          settings, then come back.
        </Text>
        <Button label="Open Settings" onPress={() => void Linking.openSettings()} />
        <Button label="Back to Home" variant="ghost" onPress={endToHome} />
      </Screen>
    );
  }

  return (
    <LiveKitRoom
      serverUrl={match.livekit_url}
      token={match.livekit_token}
      connect
      audio
      video
      onDisconnected={() => {
        if (!leavingRef.current) setConnectionLost(true);
      }}
    >
      <RoomInner
        match={match}
        connectionLost={connectionLost}
        onEnd={endToHome}
        onNext={nextOpponent}
      />
    </LiveKitRoom>
  );
}

function RoomInner({
  match,
  connectionLost,
  onEnd,
  onNext,
}: {
  match: MatchFound;
  connectionLost: boolean;
  onEnd: () => void;
  onNext: () => void;
}) {
  const tracks = useTracks([Track.Source.Camera]);
  const remoteParticipants = useRemoteParticipants();
  const { isCameraEnabled, isMicrophoneEnabled, localParticipant } = useLocalParticipant();
  const room = useRoomContext();
  const token = useAuthStore((s) => s.token);

  const [reportVisible, setReportVisible] = useState(false);
  const [menuVisible, setMenuVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // debate-api deletes the LiveKit room when moderation ends a match — the
  // only thing that removes a room out from under a live debate. Any other
  // reason (or none at all) falls back to the generic ended state.
  const [endedByModeration, setEndedByModeration] = useState(false);
  useEffect(() => {
    if (!room) return;
    const onDisconnected = (reason?: DisconnectReason) => {
      if (reason === DisconnectReason.ROOM_DELETED) setEndedByModeration(true);
    };
    room.on(RoomEvent.Disconnected, onDisconnected);
    return () => {
      room.off(RoomEvent.Disconnected, onDisconnected);
    };
  }, [room]);

  // The fact-check agent is a hidden participant; it must never render or
  // count toward opponent presence, even if its tracks/identity ever surface.
  const remoteTrack = tracks.find(
    (t) =>
      isTrackReference(t) &&
      !t.participant.isLocal &&
      t.participant.identity !== AGENT_IDENTITY,
  );
  const localTrack = tracks.find((t) => isTrackReference(t) && t.participant.isLocal);

  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((message: string) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(message);
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  }, []);
  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
  }, []);

  const { chipStatus, phase, requestFactCheck } = useFactCheck(showToast);

  // "Opponent left" only after they were actually here, not while connecting.
  const [opponentJoined, setOpponentJoined] = useState(false);
  const opponentPresent = remoteParticipants.some((p) => p.identity !== AGENT_IDENTITY);
  useEffect(() => {
    if (opponentPresent) setOpponentJoined(true);
  }, [opponentPresent]);
  const opponentLeft = opponentJoined && !opponentPresent;

  const stanceColor = (s: string) => (s === 'pro' ? colors.pro : colors.con);

  const submitReport = async (reason: ReportReason, details: string, leave: boolean) => {
    if (!token) return;
    setSubmitting(true);
    try {
      await api.reportMatch(token, match.match_id, { reason, details });
      setReportVisible(false);
      showToast('Report sent. Our safety team will review it.');
    } catch {
      setReportVisible(false);
      showToast('Could not send the report. Please try again.');
    } finally {
      setSubmitting(false);
      // Leaving is the user's stated intent — honour it even if the report
      // failed, rather than trapping them in a room with this person.
      if (leave) onEnd();
    }
  };

  const blockAndLeave = async () => {
    setMenuVisible(false);
    if (token) {
      // Fire and forget: the block is idempotent server-side and leaving
      // must not wait on the network.
      void api.blockOpponent(token, match.match_id).catch(() => {});
    }
    onEnd();
  };

  return (
    <View style={styles.room}>
      {remoteTrack && isTrackReference(remoteTrack) ? (
        <VideoTrack trackRef={remoteTrack} style={StyleSheet.absoluteFill} objectFit="cover" />
      ) : (
        <View style={[StyleSheet.absoluteFill, styles.remotePlaceholder]}>
          <Text style={styles.dim}>
            {opponentPresent ? 'Opponent has no video (mic only)' : 'Waiting for opponent…'}
          </Text>
        </View>
      )}

      <View style={styles.header}>
        <Text style={styles.topic} numberOfLines={1}>
          {match.topic.title}
        </Text>
        <Text style={styles.stances}>
          You: <Text style={{ color: stanceColor(match.your_stance) }}>
            {match.your_stance.toUpperCase()}
          </Text>{' '}
          vs Them:{' '}
          <Text style={{ color: stanceColor(match.peer_stance) }}>
            {match.peer_stance.toUpperCase()}
          </Text>
        </Text>
        <FactCheckChip status={chipStatus} />
      </View>

      <Toast message={toast} />

      <View style={styles.pip}>
        {localTrack && isTrackReference(localTrack) && isCameraEnabled ? (
          <VideoTrack trackRef={localTrack} style={styles.pipVideo} mirror objectFit="cover" />
        ) : (
          <View style={[styles.pipVideo, styles.pipOff]}>
            <Text style={styles.pipOffText}>Camera off</Text>
          </View>
        )}
      </View>

      <View style={styles.controls}>
        <VerdictSheet yourStance={match.your_stance} />
        <FactCheckButton status={chipStatus} phase={phase} onPress={requestFactCheck} />
        <View style={styles.toggleRow}>
          <Button
            label={isMicrophoneEnabled ? 'Mute mic' : 'Unmute mic'}
            variant="secondary"
            onPress={() => void localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)}
          />
          <Button
            label={isCameraEnabled ? 'Camera off' : 'Camera on'}
            variant="secondary"
            onPress={() => void localParticipant.setCameraEnabled(!isCameraEnabled)}
          />
          <Button label="Report" variant="secondary" onPress={() => setReportVisible(true)} />
          <Button label="⋯" variant="secondary" onPress={() => setMenuVisible(true)} />
        </View>
        <View style={styles.actionRow}>
          <View style={{ flex: 1 }}>
            <Button label="Next" onPress={onNext} />
          </View>
          <View style={{ flex: 1 }}>
            <Button label="End" variant="danger" onPress={onEnd} />
          </View>
        </View>
      </View>

      {endedByModeration || opponentLeft || connectionLost ? (
        <View style={styles.overlay}>
          <View style={styles.overlayCard}>
            <Text style={styles.overlayTitle}>
              {endedByModeration
                ? 'Debate ended'
                : connectionLost
                  ? 'Connection lost'
                  : 'Opponent left'}
            </Text>
            <Text style={styles.dim}>
              {endedByModeration
                ? 'This debate was ended by moderation review.'
                : connectionLost
                  ? 'The room disconnected.'
                  : 'Your opponent ended the debate or dropped out.'}
            </Text>
            {opponentLeft && !endedByModeration ? (
              <Button
                label="Block this person"
                variant="secondary"
                onPress={() => void blockAndLeave()}
              />
            ) : null}
            <Button label="Next opponent" onPress={onNext} />
            <Button label="Home" variant="secondary" onPress={onEnd} />
          </View>
        </View>
      ) : null}

      <ReportModal
        visible={reportVisible}
        submitting={submitting}
        onCancel={() => setReportVisible(false)}
        onSubmit={(reason, details, leave) => void submitReport(reason, details, leave)}
      />

      <Modal
        visible={menuVisible}
        transparent
        animationType="fade"
        onRequestClose={() => setMenuVisible(false)}
        statusBarTranslucent
      >
        <Pressable style={styles.menuBackdrop} onPress={() => setMenuVisible(false)}>
          <View style={styles.menuCard}>
            <Button
              label="Block & leave"
              variant="danger"
              onPress={() => void blockAndLeave()}
            />
            <Text style={styles.menuHint}>
              Blocking ends this debate and makes sure you are never matched with this person
              again.
            </Text>
            <Button label="Cancel" variant="ghost" onPress={() => setMenuVisible(false)} />
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  centerScreen: { justifyContent: 'center', alignItems: 'center', padding: 24, gap: 16 },
  room: { flex: 1, backgroundColor: '#000' },
  remotePlaceholder: {
    backgroundColor: colors.bg,
    justifyContent: 'center',
    alignItems: 'center',
  },
  header: {
    position: 'absolute',
    top: 54,
    left: 16,
    right: 16,
    backgroundColor: 'rgba(13, 17, 23, 0.78)',
    borderRadius: 12,
    padding: 12,
    gap: 4,
  },
  topic: { color: colors.text, fontSize: 16, fontWeight: '700' },
  stances: { color: colors.textDim, fontSize: 14, fontWeight: '600' },
  pip: {
    position: 'absolute',
    top: 120,
    right: 16,
    width: 108,
    height: 150,
    borderRadius: 12,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: colors.border,
  },
  pipVideo: { flex: 1 },
  pipOff: { backgroundColor: colors.surface, justifyContent: 'center', alignItems: 'center' },
  pipOffText: { color: colors.textDim, fontSize: 12 },
  controls: {
    position: 'absolute',
    bottom: 36,
    left: 16,
    right: 16,
    gap: 10,
  },
  toggleRow: { flexDirection: 'row', gap: 8, justifyContent: 'center', flexWrap: 'wrap' },
  actionRow: { flexDirection: 'row', gap: 10 },
  overlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.72)',
    justifyContent: 'center',
    padding: 28,
  },
  overlayCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 22,
    gap: 12,
  },
  overlayTitle: { color: colors.text, fontSize: 22, fontWeight: '800' },
  menuBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'flex-end',
    padding: 20,
    paddingBottom: 40,
  },
  menuCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 18,
    gap: 10,
  },
  menuHint: { color: colors.textDim, fontSize: 13, lineHeight: 19, textAlign: 'center' },
  permTitle: { color: colors.text, fontSize: 22, fontWeight: '800', textAlign: 'center' },
  permCopy: { color: colors.textDim, fontSize: 15, textAlign: 'center', lineHeight: 22 },
  dim: { color: colors.textDim, fontSize: 15, textAlign: 'center' },
});
