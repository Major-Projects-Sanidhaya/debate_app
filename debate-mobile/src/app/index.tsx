import { router } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { api, ApiError, isSuspendedError } from '@/api/client';
import type { FactCheckMode, Stance, Topic } from '@/api/types';
import { Button, colors, Screen, Segmented } from '@/components/ui';
import { useAuthStore } from '@/state/auth-store';
import { useSessionStore } from '@/state/session-store';

export default function Home() {
  const token = useAuthStore((s) => s.token);
  const reauth = useAuthStore((s) => s.reauth);
  const suspend = useAuthStore((s) => s.suspend);
  const setSelection = useSessionStore((s) => s.setSelection);

  const [topics, setTopics] = useState<Topic[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [topicId, setTopicId] = useState<number | null>(null);
  const [stance, setStance] = useState<Stance>('pro');
  const [mode, setMode] = useState<FactCheckMode>('on_demand');

  const loadTopics = useCallback(async () => {
    if (!token) return;
    setLoadError(null);
    setTopics(null);
    try {
      setTopics(await api.getTopics(token));
    } catch (err) {
      // A banned device cold-starting still has a stored token; its first
      // authed request is what reveals the ban.
      if (isSuspendedError(err)) {
        suspend();
        return;
      }
      // Expired token → silent re-auth once, then retry.
      if (err instanceof ApiError && err.status === 401) {
        const fresh = await reauth();
        if (fresh) {
          try {
            setTopics(await api.getTopics(fresh));
            return;
          } catch {
            // fall through to the error state
          }
        }
      }
      setLoadError('Could not load topics. Is the server reachable?');
    }
  }, [token, reauth, suspend]);

  useEffect(() => {
    void loadTopics();
  }, [loadTopics]);

  const selectedTopic = topics?.find((t) => t.id === topicId) ?? null;

  const findOpponent = () => {
    if (!selectedTopic) return;
    setSelection({
      topicId: selectedTopic.id,
      topicTitle: selectedTopic.title,
      stance,
      mode,
    });
    router.push('/queue');
  };

  return (
    <Screen style={styles.container}>
      <Text style={styles.heading}>Pick your battle</Text>

      <View style={styles.topicsBox}>
        {loadError ? (
          <View style={styles.centerBox}>
            <Text style={styles.error}>{loadError}</Text>
            <Button label="Retry" variant="secondary" onPress={loadTopics} />
          </View>
        ) : topics === null ? (
          <View style={styles.centerBox}>
            <Text style={styles.dim}>Loading topics…</Text>
          </View>
        ) : (
          <FlatList
            data={topics}
            keyExtractor={(t) => String(t.id)}
            renderItem={({ item }) => {
              const active = item.id === topicId;
              return (
                <Pressable
                  onPress={() => setTopicId(item.id)}
                  style={[styles.topicRow, active && styles.topicRowActive]}
                >
                  <Text style={[styles.topicTitle, active && { color: colors.text }]}>
                    {item.title}
                  </Text>
                </Pressable>
              );
            }}
          />
        )}
      </View>

      <View style={styles.controls}>
        <Text style={styles.label}>Your stance</Text>
        <Segmented
          options={[
            { value: 'pro', label: 'PRO — for it' },
            { value: 'con', label: 'CON — against it' },
          ]}
          value={stance}
          onChange={setStance}
          activeColors={{ pro: colors.pro, con: colors.con }}
        />

        <Text style={styles.label}>Fact-checking</Text>
        <Segmented
          options={[
            { value: 'auto', label: 'Auto fact-check' },
            { value: 'on_demand', label: 'On-demand only' },
          ]}
          value={mode}
          onChange={setMode}
        />
        <Text style={styles.hint}>Auto only activates if both debaters opt in.</Text>

        <Button label="Find Opponent" disabled={!selectedTopic} onPress={findOpponent} />
        <Pressable onPress={() => router.push('/guidelines')} style={styles.guidelinesLink}>
          <Text style={styles.guidelinesLinkText}>Community guidelines</Text>
        </Pressable>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: 20, gap: 16 },
  heading: { color: colors.text, fontSize: 28, fontWeight: '800' },
  topicsBox: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
  },
  centerBox: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 14, padding: 20 },
  topicRow: {
    paddingVertical: 15,
    paddingHorizontal: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  topicRowActive: { backgroundColor: '#1F3050' },
  topicTitle: { color: colors.textDim, fontSize: 16, fontWeight: '500' },
  controls: { gap: 10 },
  label: { color: colors.textDim, fontSize: 13, textTransform: 'uppercase', letterSpacing: 1 },
  hint: { color: colors.textDim, fontSize: 13 },
  error: { color: colors.danger, textAlign: 'center' },
  dim: { color: colors.textDim },
  guidelinesLink: { alignSelf: 'center', paddingVertical: 4 },
  guidelinesLinkText: {
    color: colors.textDim,
    fontSize: 13,
    textDecorationLine: 'underline',
  },
});
