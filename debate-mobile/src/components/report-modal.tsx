import { useState } from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { ReportReason } from '@/api/types';
import { REPORT_DETAILS_MAX, REPORT_REASONS } from '@/api/types';
import { Button, colors } from '@/components/ui';

/**
 * Report sheet. Both actions submit the same report; "Report & leave" also
 * runs the caller's End flow. Submission errors are surfaced by the caller
 * as a toast, so the modal just closes.
 */
export function ReportModal({
  visible,
  submitting,
  onCancel,
  onSubmit,
}: {
  visible: boolean;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (reason: ReportReason, details: string, leave: boolean) => void;
}) {
  const [reason, setReason] = useState<ReportReason | null>(null);
  const [details, setDetails] = useState('');

  const reset = () => {
    setReason(null);
    setDetails('');
  };
  const cancel = () => {
    reset();
    onCancel();
  };
  const submit = (leave: boolean) => {
    if (!reason || submitting) return;
    onSubmit(reason, details, leave);
    reset();
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={cancel}
      statusBarTranslucent
    >
      <KeyboardAvoidingView
        style={styles.backdrop}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.sheet}>
          <Text style={styles.title}>Report this person</Text>
          <Text style={styles.subtitle}>
            Reports are reviewed by our safety team. Pick what happened.
          </Text>

          <ScrollView style={styles.reasons} keyboardShouldPersistTaps="handled">
            {REPORT_REASONS.map((option) => {
              const selected = option.value === reason;
              return (
                <Pressable
                  key={option.value}
                  onPress={() => setReason(option.value)}
                  style={[styles.reason, selected && styles.reasonSelected]}
                >
                  <View style={[styles.radio, selected && styles.radioSelected]} />
                  <Text style={[styles.reasonLabel, selected && { color: colors.text }]}>
                    {option.label}
                  </Text>
                </Pressable>
              );
            })}

            <Text style={styles.detailsLabel}>Anything else? (optional)</Text>
            <TextInput
              style={styles.input}
              value={details}
              onChangeText={(text) => setDetails(text.slice(0, REPORT_DETAILS_MAX))}
              placeholder="Add context for the reviewer"
              placeholderTextColor={colors.textDim}
              multiline
              maxLength={REPORT_DETAILS_MAX}
            />
            <Text style={styles.counter}>
              {details.length}/{REPORT_DETAILS_MAX}
            </Text>
          </ScrollView>

          <View style={styles.actions}>
            <Button
              label="Report"
              disabled={!reason}
              loading={submitting}
              onPress={() => submit(false)}
            />
            <Button
              label="Report & leave"
              variant="danger"
              disabled={!reason || submitting}
              onPress={() => submit(true)}
            />
            <Button label="Cancel" variant="ghost" disabled={submitting} onPress={cancel} />
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: colors.bg,
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 20,
    paddingBottom: 32,
    gap: 10,
    maxHeight: '88%',
  },
  title: { color: colors.text, fontSize: 22, fontWeight: '800' },
  subtitle: { color: colors.textDim, fontSize: 14 },
  reasons: { flexGrow: 0 },
  reason: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 13,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: 8,
  },
  reasonSelected: { borderColor: colors.accent, backgroundColor: '#1F3050' },
  radio: {
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 2,
    borderColor: colors.border,
  },
  radioSelected: { borderColor: colors.accent, backgroundColor: colors.accent },
  reasonLabel: { color: colors.textDim, fontSize: 15, fontWeight: '600', flexShrink: 1 },
  detailsLabel: { color: colors.textDim, fontSize: 13, marginTop: 6, marginBottom: 6 },
  input: {
    color: colors.text,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 12,
    minHeight: 76,
    textAlignVertical: 'top',
    fontSize: 15,
  },
  counter: { color: colors.textDim, fontSize: 11, alignSelf: 'flex-end', marginTop: 4 },
  actions: { gap: 8, marginTop: 4 },
});
