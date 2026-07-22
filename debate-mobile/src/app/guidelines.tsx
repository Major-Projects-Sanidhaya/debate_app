import { router } from 'expo-router';

import { Guidelines } from '@/components/guidelines';

/** Read-only view, linked from Home. The one-time gate lives in _layout. */
export default function GuidelinesScreen() {
  return <Guidelines mode="view" onClose={() => router.back()} />;
}
