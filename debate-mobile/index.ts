// Custom entry: LiveKit's WebRTC globals must be registered before any app
// code (livekit-client reads them at import time). ES module evaluation order
// guarantees livekit-globals runs to completion before expo-router/entry.
import '@/lib/livekit-globals';
import 'expo-router/entry';
