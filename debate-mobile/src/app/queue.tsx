import { Redirect, router } from 'expo-router';
import { useCallback } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import type { MatchFound } from '@/api/types';
import { Button, colors, Screen } from '@/components/ui';
import { useMatchmaking } from '@/hooks/use-matchmaking';
import { useAuthStore } from '@/state/auth-store';
import { useSessionStore } from '@/state/session-store';

export default function Queue() {
  const token = useAuthStore((s) => s.token);
  const selection = useSessionStore((s) => s.selection);
  const setMatch = useSessionStore((s) => s.setMatch);

  const onMatch = useCallback(
    (match: MatchFound) => {
      setMatch(match);
      router.replace('/room');
    },
    [setMatch],
  );

  const { status, error, cancel, retry } = useMatchmaking(selection, token, onMatch);

  if (!selection) return <Redirect href="/" />;

  const onCancel = () => {
    cancel();
    router.back();
  };

  // Unreachable API: stop pretending to search and offer a way forward.
  if (status === 'failed') {
    return (
      <Screen style={styles.container}>
        <View style={styles.center}>
          <Text style={styles.searching}>Can&apos;t reach the server</Text>
          <Text style={styles.errorCopy}>
            We couldn&apos;t connect to matchmaking. Check your internet connection and try
            again.
          </Text>
          <View style={styles.errorActions}>
            <Button label="Try again" onPress={retry} />
            <Button label="Back" variant="secondary" onPress={onCancel} />
          </View>
        </View>
      </Screen>
    );
  }

  return (
    <Screen style={styles.container}>
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.accent} />
        <Text style={styles.searching}>Searching for an opponent…</Text>

        <View style={styles.card}>
          <Text style={styles.topic}>{selection.topicTitle}</Text>
          <Text style={styles.meta}>
            Your stance:{' '}
            <Text
              style={{
                color: selection.stance === 'pro' ? colors.pro : colors.con,
                fontWeight: '700',
              }}
            >
              {selection.stance.toUpperCase()}
            </Text>
          </Text>
          <Text style={styles.meta}>
            Fact-check: {selection.mode === 'auto' ? 'Auto (if both opt in)' : 'On-demand'}
          </Text>
        </View>

        {status === 'reconnecting' ? (
          <Text style={styles.warn}>Connection lost — retrying…</Text>
        ) : null}
        {error ? <Text style={styles.warn}>{error}</Text> : null}
      </View>

      <Button label="Cancel" variant="secondary" onPress={onCancel} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: 20, gap: 16 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 18 },
  searching: { color: colors.text, fontSize: 20, fontWeight: '700' },
  card: {
    alignSelf: 'stretch',
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 18,
    gap: 8,
    alignItems: 'center',
  },
  topic: { color: colors.text, fontSize: 18, fontWeight: '700', textAlign: 'center' },
  meta: { color: colors.textDim, fontSize: 15 },
  warn: { color: '#D29922', textAlign: 'center' },
  errorCopy: {
    color: colors.textDim,
    fontSize: 15,
    lineHeight: 22,
    textAlign: 'center',
    paddingHorizontal: 8,
  },
  errorActions: { alignSelf: 'stretch', gap: 10 },
});
