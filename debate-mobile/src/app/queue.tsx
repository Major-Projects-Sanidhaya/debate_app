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

  const { status, error, cancel } = useMatchmaking(selection, token, onMatch);

  if (!selection) return <Redirect href="/" />;

  const onCancel = () => {
    cancel();
    router.back();
  };

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
});
