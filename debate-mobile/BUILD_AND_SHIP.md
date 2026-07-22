# Building and shipping Debate

How to get this app onto other people's phones, written for someone who has
never used EAS. Two paths:

- **Android APK** — a link/QR anyone can install. **No Google Play account
  needed.** Start here.
- **iOS TestFlight** — needs a paid Apple Developer account ($99/yr).

Deploy [debate-api](../debate-api/DEPLOY.md) first: a shared build needs a
public API URL, not your LAN.

---

## Step 0 — before your first build (do not skip)

Open `app.config.js` and replace **`REPLACEME`**:

```js
const BUNDLE_ID = 'com.REPLACEME.debate';   // -> com.yourcompany.debate
```

This is the app's permanent identity on both stores. Changing it after you
ship creates a *new* app — new listing, new TestFlight testers, and existing
installs never update. Use a reverse-domain name you control. Everything else
(name, icon, splash) is cosmetic and safe to change any time.

Then set your API URL in `eas.json`. Each build profile has its own — they are
baked into the binary, not read at runtime:

| Profile | `EXPO_PUBLIC_API_URL` |
|---|---|
| `development` | `http://<your-Mac-LAN-IP>:8000` (from `make lan-ip` in debate-infra) |
| `preview` | `https://<your-api>.up.railway.app` |
| `production` | `https://<your-api>.up.railway.app` |

---

## Step 1 — set up EAS

```sh
npm install -g eas-cli
eas login          # create a free Expo account if you don't have one
cd debate-mobile
eas init           # links this folder to an Expo project
```

`eas init` prints a **project ID**. Because this app uses `app.config.js`
(so the config can carry comments), EAS cannot write it in for you — paste it
in yourself, at the bottom of `app.config.js`:

```js
extra: { eas: { projectId: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' } },
```

Verify it resolved: `npx expo config --type public | grep projectId`.

---

## Step 2 — Android APK (the shareable one)

```sh
eas build -p android --profile preview
```

First run asks to generate an Android keystore — say yes and let EAS keep it.
The build runs on Expo's machines (~10–20 min). When it finishes you get a
build page with a **QR code and an install link**.

Send that link to testers. On the phone: open it, download the APK, and accept
"install from unknown sources" when Android asks. That's it — no Play Console,
no review, no test-group setup.

The APK includes the debug-free JS bundle and points at whatever
`EXPO_PUBLIC_API_URL` the `preview` profile had at build time.

---

## Step 3 — iOS TestFlight

Requires an **Apple Developer Program** membership ($99/yr). There is no way to
distribute an iOS build to arbitrary phones without one.

**3a. Create the app record** at [App Store Connect](https://appstoreconnect.apple.com)
→ **Apps → +** → New App:
- Platform: iOS
- Bundle ID: the one from Step 0 (register it under Certificates, Identifiers
  & Profiles first if it isn't in the dropdown)
- SKU: anything unique, e.g. `debate-001`

**3b. Build.** Let EAS manage signing — it creates the distribution certificate
and provisioning profile for you:

```sh
eas build -p ios --profile production
```

It will prompt for your Apple ID and walk through credentials. To inspect or
reset them later: `eas credentials`.

**3c. Upload:**

```sh
eas submit -p ios --latest
```

**3d. Distribute in App Store Connect → TestFlight:**

- **Internal testers** (up to 100 people on your team): available within
  minutes of processing, **no review**. This is what you want for early
  testing.
- **External testers** (up to 10,000, shareable public link): requires
  **Beta App Review** — usually a day or so, and it can be rejected. You'll
  need a description and test instructions.

Apple emails testers an invite; they install via the TestFlight app.

---

## Step 4 — Google Play internal testing (later)

Only when you want Play distribution. The `production` profile builds an
Android App Bundle (`.aab`), which is what Play requires:

```sh
eas build -p android --profile production
eas submit -p android --latest
```

Needs a Google Play Console account (one-time $25) and, for the first upload, a
manually created app entry plus a service-account key for `eas submit`. After
that, **Play Console → Testing → Internal testing** distributes to up to 100
testers with no review.

Until you need Play, the Step 2 APK is simpler and has no gatekeeper.

---

## Troubleshooting

**"I changed the API URL but the app still hits the old one."**
`EXPO_PUBLIC_*` variables are **inlined into the JavaScript bundle at build
time** — they are not read at runtime. Changing `EXPO_PUBLIC_API_URL` in
`eas.json` (or `.env`) **always requires a new build**; a JS reload, an OTA
update, or restarting the app will not pick it up. This is verified behaviour,
not a guess: the built bundle literally contains the URL string.

The same applies locally — after editing `.env`, restart Metro with
`npx expo start -c`.

**Build fails during `npm install` (ERESOLVE / peer dependency).**
`.npmrc` in this folder sets `legacy-peer-deps=true`, which EAS's cloud build
also reads. `@config-plugins/react-native-webrtc` declares support only up to
SDK 56 while this app is on 57; the plugin merely writes camera/mic permissions
at prebuild and works fine. Don't delete `.npmrc`.

**`npx expo-doctor` says "Check native tooling versions" failed.**
That's CocoaPods missing on *your Mac*. It only affects local `npx expo run:ios`
builds — EAS's build machines have it. Install with `brew install cocoapods` if
you want to build iOS locally. This is the one check that is expected to fail
here.

**Testers see "Can't reach the server" / "Could not load topics".**
The build's API URL is wrong or debate-api is down. Check
`curl https://<your-api>/healthz`. The app degrades honestly here — the queue
screen gives up after 5 connection attempts and offers **Try again** rather
than spinning forever.

**Android: "App not installed" or the install is blocked.**
The phone needs "install unknown apps" allowed for the browser doing the
download. If a previous build with the *same* bundle ID but a *different*
signing key is installed, uninstall it first.

**iOS: build succeeds but `eas submit` rejects it.**
Usually the bundle identifier in `app.config.js` doesn't match the App Store
Connect record, or the app record doesn't exist yet. Both are Step 3a.

---

## What each profile is for

| Profile | Distribution | Output | Use |
|---|---|---|---|
| `development` | internal | dev client | Local work against the LAN API; pair with `npx expo start --dev-client` |
| `preview` | internal | **APK** | Share with testers, no store account |
| `production` | store | AAB / IPA | TestFlight and the stores; `autoIncrement` bumps the build number |

Local development is unchanged: `.env` still holds your LAN URL, and
`npx expo start` / `npx expo run:ios` work exactly as before.

---

## Before a real public release

- Replace the placeholder icon and splash in `assets/images/` (currently a
  generated wordmark).
- Set a real support address in `src/components/suspended.tsx`
  (`support@example.com` today).
- Point `CORS_ORIGINS` on debate-api at your real origins if you ship a web
  client.
- Both stores require a privacy policy URL, and Apple will ask about the
  camera/microphone usage and user-generated content moderation — this app has
  reporting, blocking, and automated screening, which is what App Review looks
  for in a live-video social app.
