import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Button, colors, Screen } from '@/components/ui';
import { useAuthStore } from '@/state/auth-store';

export function AgeGate() {
  const { status, error, attestAndAuth } = useAuthStore();
  const [checked, setChecked] = useState(false);

  if (status === 'blocked') {
    return (
      <Screen style={styles.container}>
        <Text style={styles.title}>Account blocked</Text>
        <Text style={styles.copy}>{error ?? 'This device is not allowed to use Debate.'}</Text>
      </Screen>
    );
  }

  return (
    <Screen style={styles.container}>
      <Text style={styles.title}>Debate</Text>
      <Text style={styles.copy}>
        Live, unmoderated video debates with strangers who disagree with you. You must be an
        adult to participate.
      </Text>

      <Pressable style={styles.checkboxRow} onPress={() => setChecked((c) => !c)}>
        <View style={[styles.checkbox, checked && styles.checkboxChecked]}>
          {checked ? <Text style={styles.checkmark}>✓</Text> : null}
        </View>
        <Text style={styles.checkboxLabel}>I confirm that I am 18 or older</Text>
      </Pressable>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Button
        label="Continue"
        disabled={!checked}
        loading={status === 'authenticating'}
        onPress={attestAndAuth}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { justifyContent: 'center', padding: 24, gap: 20 },
  title: { color: colors.text, fontSize: 34, fontWeight: '800' },
  copy: { color: colors.textDim, fontSize: 16, lineHeight: 23 },
  checkboxRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  checkbox: {
    width: 26,
    height: 26,
    borderRadius: 7,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxChecked: { backgroundColor: colors.accent, borderColor: colors.accent },
  checkmark: { color: colors.text, fontWeight: '700' },
  checkboxLabel: { color: colors.text, fontSize: 16 },
  error: { color: colors.danger },
});
