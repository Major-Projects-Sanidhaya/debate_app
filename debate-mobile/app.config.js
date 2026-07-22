// Expo app config. This is JS rather than app.json purely so it can carry
// comments — see BUILD_AND_SHIP.md for the full first-build runbook.
//
// ┌──────────────────────────────────────────────────────────────────────────┐
// │  ⚠️  PICK A REAL BUNDLE ID BEFORE YOUR FIRST BUILD                        │
// │                                                                          │
// │  Replace REPLACEME below (in BOTH ios.bundleIdentifier and               │
// │  android.package) with a reverse-domain name you control, e.g.           │
// │  com.yourcompany.debate.                                                 │
// │                                                                          │
// │  Changing it after you have shipped a build effectively creates a NEW    │
// │  app: a new App Store / Play listing, TestFlight testers do not carry    │
// │  over, and existing installs will never update. It is the one value      │
// │  here that is expensive to change later.                                 │
// │                                                                          │
// │  Everything else (name, icon, splash, colours) is cosmetic — change it   │
// │  whenever you like.                                                      │
// └──────────────────────────────────────────────────────────────────────────┘
const BUNDLE_ID = 'com.REPLACEME.debate';

export default {
  expo: {
    // Display name under the icon. Placeholder — change freely.
    name: 'Debate',
    // Identifies the project on Expo's servers. Keep stable once `eas init` has run.
    slug: 'debate-mobile',
    version: '1.0.0',
    orientation: 'portrait',
    icon: './assets/images/icon.png',
    scheme: 'debatemobile',
    userInterfaceStyle: 'dark',

    ios: {
      bundleIdentifier: BUNDLE_ID,
      supportsTablet: false,
      infoPlist: {
        // Keeps mic audio alive if the app is backgrounded mid-debate.
        UIBackgroundModes: ['audio'],
      },
    },

    android: {
      package: BUNDLE_ID,
      adaptiveIcon: {
        backgroundColor: '#0D1117',
        foregroundImage: './assets/images/android-icon-foreground.png',
        backgroundImage: './assets/images/android-icon-background.png',
        monochromeImage: './assets/images/android-icon-monochrome.png',
      },
      predictiveBackGestureEnabled: false,
    },

    web: {
      output: 'static',
      favicon: './assets/images/favicon.png',
    },

    plugins: [
      'expo-router',
      [
        'expo-splash-screen',
        {
          backgroundColor: '#0D1117',
          image: './assets/images/splash-icon.png',
          imageWidth: 160,
        },
      ],
      'expo-secure-store',
      // LiveKit + WebRTC native setup. The permission strings below are what
      // iOS and Android show in the OS permission prompts.
      '@livekit/react-native-expo-plugin',
      [
        '@config-plugins/react-native-webrtc',
        {
          cameraPermission:
            'Debate uses your camera so your opponent can see you during video debates.',
          microphonePermission:
            'Debate uses your microphone so your opponent can hear you during video debates.',
        },
      ],
      'expo-web-browser',
    ],

    experiments: {
      typedRoutes: true,
      reactCompiler: true,
    },

    // NOTE: `eas init` will print a projectId and ask you to add it here —
    // it can auto-edit app.json but not app.config.js. Uncomment and paste:
    // extra: { eas: { projectId: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' } },
  },
};
