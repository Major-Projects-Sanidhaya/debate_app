import { Linking, StyleSheet, Text } from 'react-native';

import { Button, colors, Screen } from '@/components/ui';

// TODO: replace with the real support address before launch.
const SUPPORT_EMAIL = 'support@example.com';

/**
 * Terminal state: rendered instead of the whole app once the server says
 * account_suspended, so a banned device cannot reach matchmaking at all.
 */
export function Suspended() {
  const mailto = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent('Debate account suspension appeal')}`;
  return (
    <Screen style={styles.screen}>
      <Text style={styles.title}>Account suspended</Text>
      <Text style={styles.copy}>
        This account can no longer join debates. Suspensions come from reports by other
        participants or from our automated safety review.
      </Text>
      <Text style={styles.copy}>
        If you believe this was a mistake, you can contact support and we will take another look.
      </Text>
      <Button
        label="Contact support"
        variant="secondary"
        onPress={() => void Linking.openURL(mailto).catch(() => {})}
      />
      <Text style={styles.fineprint}>{SUPPORT_EMAIL}</Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  screen: { justifyContent: 'center', padding: 28, gap: 16 },
  title: { color: colors.text, fontSize: 30, fontWeight: '800' },
  copy: { color: colors.textDim, fontSize: 16, lineHeight: 24 },
  fineprint: { color: colors.textDim, fontSize: 13, textAlign: 'center' },
});
