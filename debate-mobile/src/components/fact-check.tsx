import * as WebBrowser from 'expo-web-browser';
import { useEffect, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import type { VerdictMessage, VerdictValue } from '@/api/fact-check';
import type { Stance } from '@/api/types';
import { colors } from '@/components/ui';
import type { AgentChipStatus, RequestPhase } from '@/hooks/use-fact-check';
import { useSessionStore } from '@/state/session-store';

const VERDICT_COLORS: Record<VerdictValue, string> = {
  true: colors.pro,
  false: colors.danger,
  misleading: '#D29922',
  unverifiable: colors.textDim,
};

const CHIP_LABELS: Record<AgentChipStatus, string> = {
  connecting: 'connecting…',
  active: 'active',
  unavailable: 'unavailable',
};

export function FactCheckChip({ status }: { status: AgentChipStatus }) {
  const dotColor = status === 'active' ? colors.pro : colors.textDim;
  return (
    <View style={styles.chip}>
      <View style={[styles.chipDot, { backgroundColor: dotColor }]} />
      <Text style={styles.chipText}>
        Fact-checker · <Text style={status === 'active' ? styles.chipActive : undefined}>
          {CHIP_LABELS[status]}
        </Text>
      </Text>
    </View>
  );
}

export function FactCheckButton({
  status,
  phase,
  onPress,
}: {
  status: AgentChipStatus;
  phase: RequestPhase;
  onPress: () => void;
}) {
  const cooldownUntil = useSessionStore((s) => s.cooldownUntil);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (cooldownUntil === null || cooldownUntil <= Date.now()) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [cooldownUntil]);

  const cooldownLeft =
    cooldownUntil !== null ? Math.max(0, Math.ceil((cooldownUntil - now) / 1000)) : 0;

  let label = 'Fact check';
  if (phase === 'requesting') label = 'Requesting…';
  else if (phase === 'checking') label = 'Checking their last 30s…';
  else if (cooldownLeft > 0) label = `Fact check (${cooldownLeft}s)`;

  const disabled = status !== 'active' || phase !== 'idle' || cooldownLeft > 0;

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.fcButton,
        disabled && styles.fcButtonDisabled,
        pressed && !disabled && { opacity: 0.75 },
      ]}
    >
      <Text style={styles.fcButtonLabel}>{label}</Text>
    </Pressable>
  );
}

function VerdictCard({ verdict, yourStance }: { verdict: VerdictMessage; yourStance: Stance }) {
  const attribution = verdict.speaker_stance === yourStance ? 'Your claim' : 'Their claim';
  return (
    <View style={styles.card}>
      <View style={styles.cardTopRow}>
        <View style={[styles.pill, { backgroundColor: VERDICT_COLORS[verdict.verdict] }]}>
          <Text style={styles.pillText}>{verdict.verdict.toUpperCase()}</Text>
        </View>
        <Text style={styles.cardMeta}>{verdict.confidence} confidence</Text>
        {verdict.mode === 'auto' ? (
          <View style={styles.autoBadge}>
            <Text style={styles.autoBadgeText}>AUTO</Text>
          </View>
        ) : null}
      </View>
      <Text style={styles.cardAttribution}>{attribution}</Text>
      <Text style={styles.cardClaim}>“{verdict.claim}”</Text>
      <Text style={styles.cardSummary}>{verdict.summary}</Text>
      {verdict.sources.map((source, index) => (
        <Pressable
          key={`${source.url}-${index}`}
          onPress={() => {
            if (/^https?:\/\//.test(source.url)) {
              void WebBrowser.openBrowserAsync(source.url).catch(() => {});
            }
          }}
          style={styles.sourceRow}
        >
          <Text style={styles.sourceText} numberOfLines={1}>
            ↗ {source.title}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

export function VerdictSheet({ yourStance }: { yourStance: Stance }) {
  const verdicts = useSessionStore((s) => s.verdicts);
  const unseen = useSessionStore((s) => s.unseenVerdicts);
  const markVerdictsSeen = useSessionStore((s) => s.markVerdictsSeen);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (open && unseen > 0) markVerdictsSeen();
  }, [open, unseen, markVerdictsSeen]);

  return (
    <View>
      {open ? (
        <View style={styles.sheetBody}>
          <FlatList
            data={verdicts}
            keyExtractor={(v, index) => `${v.request_id}-${index}`}
            renderItem={({ item }) => <VerdictCard verdict={item} yourStance={yourStance} />}
            ListEmptyComponent={
              <Text style={styles.sheetEmpty}>No fact-checks yet in this debate.</Text>
            }
          />
        </View>
      ) : null}
      <Pressable style={styles.handle} onPress={() => setOpen((o) => !o)}>
        <Text style={styles.handleText}>
          Verdicts{verdicts.length > 0 ? ` (${verdicts.length})` : ''} {open ? '▾' : '▴'}
        </Text>
        {!open && unseen > 0 ? (
          <View style={styles.unseenBadge}>
            <Text style={styles.unseenBadgeText}>{unseen}</Text>
          </View>
        ) : null}
      </Pressable>
    </View>
  );
}

export function Toast({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <View style={styles.toast} pointerEvents="none">
      <Text style={styles.toastText}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 2,
  },
  chipDot: { width: 8, height: 8, borderRadius: 4 },
  chipText: { color: colors.textDim, fontSize: 12, fontWeight: '600' },
  chipActive: { color: colors.pro },
  fcButton: {
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 12,
    paddingVertical: 12,
    backgroundColor: '#1F3050',
    borderWidth: 1,
    borderColor: colors.accent,
  },
  fcButtonDisabled: { opacity: 0.45, borderColor: colors.border },
  fcButtonLabel: { color: colors.text, fontSize: 15, fontWeight: '700' },
  sheetBody: {
    maxHeight: 340,
    backgroundColor: 'rgba(13, 17, 23, 0.96)',
    borderTopLeftRadius: 14,
    borderTopRightRadius: 14,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 10,
    marginBottom: 4,
  },
  sheetEmpty: { color: colors.textDim, textAlign: 'center', padding: 18 },
  handle: {
    flexDirection: 'row',
    alignSelf: 'center',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 6,
    paddingHorizontal: 16,
    borderRadius: 999,
    backgroundColor: 'rgba(22, 27, 34, 0.9)',
    borderWidth: 1,
    borderColor: colors.border,
  },
  handleText: { color: colors.text, fontSize: 13, fontWeight: '600' },
  unseenBadge: {
    minWidth: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: colors.danger,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 5,
  },
  unseenBadgeText: { color: colors.text, fontSize: 11, fontWeight: '800' },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 12,
    marginBottom: 8,
    gap: 5,
  },
  cardTopRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pill: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  pillText: { color: '#fff', fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  cardMeta: { color: colors.textDim, fontSize: 12 },
  autoBadge: {
    marginLeft: 'auto',
    borderWidth: 1,
    borderColor: colors.textDim,
    borderRadius: 5,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  autoBadgeText: { color: colors.textDim, fontSize: 10, fontWeight: '800' },
  cardAttribution: { color: colors.accent, fontSize: 12, fontWeight: '700' },
  cardClaim: { color: colors.text, fontSize: 15, fontWeight: '600', lineHeight: 21 },
  cardSummary: { color: colors.textDim, fontSize: 13, lineHeight: 19 },
  sourceRow: { paddingVertical: 3 },
  sourceText: { color: colors.accent, fontSize: 13 },
  toast: {
    position: 'absolute',
    top: 118,
    left: 24,
    right: 24,
    backgroundColor: 'rgba(22, 27, 34, 0.95)',
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    zIndex: 10,
  },
  toastText: { color: colors.text, fontSize: 13, textAlign: 'center' },
});
