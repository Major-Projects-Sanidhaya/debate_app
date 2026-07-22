import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button, colors, Screen } from '@/components/ui';

const RULES: { title: string; body: string }[] = [
  {
    title: 'Argue with the idea, not the person',
    body: 'You are here to disagree. Take the other side apart as hard as you like — their reasoning, their evidence, their conclusions. What you may not do is go after the person making the argument.',
  },
  {
    title: 'No harassment or hate',
    body: 'No slurs, no demeaning people for their race, religion, ethnicity, nationality, gender, sexuality, or disability, and no piling on someone to humiliate them. Strong language about politics is fine; cruelty aimed at a human being is not.',
  },
  {
    title: 'No sexual content or nudity',
    body: 'Keep your camera clean. Nudity, sexual acts, and sexual propositions all end the debate and can end your account.',
  },
  {
    title: 'No threats',
    body: 'Never threaten violence against your opponent or anyone else, even as a joke or as hyperbole. We treat credible threats as a serious safety matter.',
  },
  {
    title: 'Adults only',
    body: 'You must be 18 or older to use Debate. If your opponent appears to be a minor, end the debate and report it — that report is the fastest way to get them off the platform.',
  },
  {
    title: 'Debates are transcribed and screened',
    body: 'Audio is transcribed live so we can fact-check claims, and both transcripts and video are automatically screened for the rules above. Serious violations can end a debate while it is happening.',
  },
  {
    title: 'Breaking these rules gets you banned',
    body: 'Depending on what happened, we may end the debate, suspend your account, or ban your device permanently. Repeated reports from different people lead to an automatic suspension.',
  },
];

export function Guidelines({
  mode,
  onAccept,
  onClose,
}: {
  mode: 'gate' | 'view';
  onAccept?: () => void;
  onClose?: () => void;
}) {
  return (
    <Screen style={styles.screen}>
      <Text style={styles.title}>Community guidelines</Text>
      <Text style={styles.intro}>
        Debate pairs you with a stranger who disagrees with you. That only works if everyone
        follows a few rules.
      </Text>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        {RULES.map((rule) => (
          <View key={rule.title} style={styles.rule}>
            <Text style={styles.ruleTitle}>{rule.title}</Text>
            <Text style={styles.ruleBody}>{rule.body}</Text>
          </View>
        ))}
        <Text style={styles.footer}>
          You can report or block your opponent at any point during a debate, from the buttons on
          the debate screen.
        </Text>
      </ScrollView>

      {mode === 'gate' ? (
        <Button label="I understand — continue" onPress={onAccept} />
      ) : (
        <Button label="Close" variant="secondary" onPress={onClose} />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  screen: { padding: 20, gap: 12 },
  title: { color: colors.text, fontSize: 28, fontWeight: '800' },
  intro: { color: colors.textDim, fontSize: 15, lineHeight: 22 },
  scroll: { flex: 1 },
  scrollContent: { gap: 16, paddingVertical: 8, paddingBottom: 16 },
  rule: { gap: 4 },
  ruleTitle: { color: colors.text, fontSize: 16, fontWeight: '700' },
  ruleBody: { color: colors.textDim, fontSize: 14, lineHeight: 21 },
  footer: { color: colors.textDim, fontSize: 13, lineHeight: 20, fontStyle: 'italic' },
});
