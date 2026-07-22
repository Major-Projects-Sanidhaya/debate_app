import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { ActivityIndicator, View } from 'react-native';

import { AgeGate } from '@/components/age-gate';
import { Guidelines } from '@/components/guidelines';
import { Suspended } from '@/components/suspended';
import { colors } from '@/components/ui';
import { useAuthStore } from '@/state/auth-store';
import { useGuidelinesStore } from '@/state/guidelines-store';

export default function RootLayout() {
  const status = useAuthStore((s) => s.status);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const guidelinesAccepted = useGuidelinesStore((s) => s.accepted);
  const loadGuidelines = useGuidelinesStore((s) => s.load);
  const acceptGuidelines = useGuidelinesStore((s) => s.accept);

  useEffect(() => {
    void bootstrap();
    void loadGuidelines();
  }, [bootstrap, loadGuidelines]);

  // Suspension outranks everything: a banned device gets no app at all,
  // whether the ban surfaced at auth, on the matchmaking socket, or on a
  // cold start whose first authed request came back 403.
  if (status === 'suspended') {
    return (
      <>
        <StatusBar style="light" />
        <Suspended />
      </>
    );
  }

  if (status === 'loading' || guidelinesAccepted === null) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, justifyContent: 'center' }}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  // Blocking age gate: no navigation exists until the device is attested.
  if (status !== 'ready') {
    return (
      <>
        <StatusBar style="light" />
        <AgeGate />
      </>
    );
  }

  // Shown exactly once, after the age gate; acceptance persists in SecureStore.
  if (!guidelinesAccepted) {
    return (
      <>
        <StatusBar style="light" />
        <Guidelines mode="gate" onAccept={() => void acceptGuidelines()} />
      </>
    );
  }

  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.bg },
        }}
      />
    </>
  );
}
