# debate-mobile

Expo (React Native + TypeScript) client for the debate app: pick a contested
topic and a stance, get matched with a stranger who disagrees, debate over
live video with LiveKit. Backend contract lives in
[debate-api](../debate-api); local infra in [debate-infra](../debate-infra).

**Expo Go does not work for this app.** LiveKit needs native WebRTC modules,
so it runs in a custom dev client — `npx expo run:ios` / `run:android` builds
one automatically.

## Prerequisites

- Node 20+
- **iOS**: Xcode (with the license accepted: `sudo xcodebuild -license accept`)
  and CocoaPods (`brew install cocoapods`)
- **Android** (optional): Android Studio + an emulator or device
- [debate-infra](../debate-infra) up (`make up`) and
  [debate-api](../debate-api) running on port 8000

## First run

```sh
npm install
cp .env.example .env   # set EXPO_PUBLIC_API_URL to your Mac's LAN IP
```

`.npmrc` sets `legacy-peer-deps=true` because
`@config-plugins/react-native-webrtc` declares peer support up to Expo SDK 56
while the app is on SDK 57; the plugin only writes permissions into the native
projects at prebuild and works fine. EAS's cloud builder reads the same file —
don't delete it, or remote builds fail during `npm install`.

Then:

```sh
npx expo run:ios            # iOS simulator (expo run handles prebuild + pods)
npx expo run:ios --device   # physical iPhone (pick it from the list)
npx expo run:android        # Android emulator/device
```

The first build takes a while (native compile); after that, day-to-day work
is just the Metro server (`npx expo start`) reusing the installed dev client.

`EXPO_PUBLIC_*` values are inlined at bundle time — after editing `.env`,
restart Metro (`npx expo start -c`).

## Shipping to testers

See **[BUILD_AND_SHIP.md](BUILD_AND_SHIP.md)** for the EAS runbook — an Android
APK you can share as a link (no Play account needed) and iOS TestFlight.

Two things to know before the first build:

- **Replace `REPLACEME`** in `app.config.js`. The bundle identifier is the app's
  permanent store identity; changing it after shipping creates a new app.
- **`EXPO_PUBLIC_API_URL` is inlined at build time**, per `eas.json` profile —
  `development` points at your LAN, `preview`/`production` at the deployed API.
  Changing it always needs a rebuild, never just a reload.

## Flow

1. **Bootstrap** — first launch generates a device UUID (SecureStore), shows
   a blocking 18+ age gate, then `POST /auth/device` stores the JWT.
   Subsequent launches skip straight to Home.
2. **Home** — topic list, PRO/CON stance selector, fact-check mode selector
   (auto only activates if both debaters opt in), Find Opponent.
3. **Queue** — opens the matchmaking websocket (`useMatchmaking` hook owns
   the lifecycle), joins, reconnects with exponential backoff, Cancel returns
   Home.
4. **Room** — on `match_found`, connects to LiveKit using the per-match
   `livekit_url` + `livekit_token` from the server (never hardcoded). Remote
   video full-bleed, local preview PiP, mic/cam toggles, Report, End
   (`POST /matches/{id}/end` → Home), Next (end + requeue with the same
   selection). "Opponent left" overlay when the peer disconnects.

## Safety & moderation

Client side of debate-api's moderation contract. All of it is JS-only — no new
native modules, so it ships without a rebuild.

- **Community guidelines** — shown once immediately after the age gate with an
  Accept button; acceptance persists in SecureStore
  (`guidelines_accepted_v1`), so it never appears again. Readable any time from
  the link on Home (`/guidelines`).
- **Report** — the Room screen's Report button opens a sheet with the six
  contract reasons (human labels, enum values on the wire), an optional
  ≤500-char details field, and two actions: **Report** (submit, stay in the
  debate, success toast) and **Report & leave** (submit, then the normal End
  flow). Failures show a non-blocking toast; "& leave" still leaves, because
  a failed report shouldn't trap someone in a room with the person they're
  reporting.
- **Block** — the Room overflow menu (`⋯`) has **Block & leave**, and the
  "Opponent left" overlay offers **Block this person** while the match id is
  still known. Both `POST /matches/{id}/block`, then run the End flow home.
- **Suspended** — a `403 account_suspended` from any authed request, or an
  `account_suspended` error on the matchmaking socket, switches the app to a
  full-screen Suspended state that replaces all navigation (the socket also
  stops retrying). A banned device cold-starts into it: the stored token still
  looks valid locally, so the ban surfaces on Home's first `/topics` call.
  Support contact is a `mailto:` placeholder — **set a real address in
  `src/components/suspended.tsx` before launch.**
- **Moderation-ended debates** — debate-api deletes the LiveKit room when
  moderation ends a match, so the client listens for a `ROOM_DELETED`
  disconnect reason and shows "This debate was ended by moderation review."
  Any other disconnect reason falls back to the existing generic ended state.
  (The RN `LiveKitRoom`'s `onDisconnected` prop drops the reason argument, so
  this listens to `RoomEvent.Disconnected` on the room object instead.)

## Testing with two clients

**The iOS simulator has no camera.** It can *receive* remote video and *send*
mic audio, but it cannot send video. So:

- **Two-way video** needs a physical iPhone **plus** a second physical device,
  or an Android emulator with a virtual camera (AVD camera set to
  "Emulated"/webcam).
- A simulator + physical device pair still exercises the full matchmaking and
  audio path — the phone just sees a black tile for the simulator.

Networking rules (this trips everyone up):

- Both devices must reach the backend, so `EXPO_PUBLIC_API_URL` must be your
  **Mac's LAN IP** (e.g. `http://10.0.0.115:8000`) — `localhost` only works on
  the iOS simulator, because the simulator shares the Mac's network stack; on
  a phone, `localhost` is the phone.
- Media flows to the address LiveKit advertises. debate-infra renders your
  LAN IP into LiveKit's `node_ip` (`make up` there); if you switch Wi-Fi,
  re-run `make down && make up` in debate-infra or calls will connect with no
  audio/video.
- Phone and Mac must be on the same Wi-Fi without AP isolation.

## Fact-checking agent

When [debate-agent](../debate-agent) is running, it joins every match as the
hidden participant `fc-agent` and talks JSON over the LiveKit data channel
(topic `fact_check`). The Room screen:

- shows a **Fact-checker chip** in the header — "connecting…" until the
  agent's `agent_ready`, then "active"; "unavailable" if nothing arrives
  within 10s. Debates work fine with no agent running.
- enables the **Fact check** button only while the agent is active and no
  request is in flight; a tap publishes `{"type":"fact_check_request"}`, shows
  "Checking their last 30s…" on `fact_check_status`, and applies a visible
  10s cooldown after each verdict/error (the server enforces the same).
- collects **verdict cards** in a bottom sheet (newest first): claim,
  color-coded verdict pill, confidence, summary, "Your claim"/"Their claim"
  attribution, an AUTO badge for auto-mode checks, and tappable sources
  (opens in an in-app browser). The unseen count badges the sheet handle;
  the feed is in-memory and clears on Next/End.
- shows `fact_check_error` messages as a non-blocking toast.

A dead agent has no push signal (hidden participants emit no leave events),
so an unanswered request (15s) marks the agent unavailable and disables the
button — video is unaffected. Contract types and the defensive parser live in
`src/api/fact-check.ts`; the lifecycle is `src/hooks/use-fact-check.ts`.

## Where things live

- `src/api/` — typed client + wire types matching the contract exactly
- `src/hooks/use-matchmaking.ts` — websocket lifecycle: join, backoff
  reconnect, cancel, double-join guard
- `src/state/` — zustand stores (auth/device identity + suspension, selection +
  match, guidelines acceptance)
- `src/components/` — shared UI plus the safety surfaces: `guidelines`,
  `suspended`, `report-modal`
- `src/app/` — expo-router screens (`_layout` gates on suspension → auth →
  guidelines; then `index` Home, `queue`, `room`, `guidelines`)
- `index.ts` — custom entry calling LiveKit's `registerGlobals()` before the
  router loads

Camera/mic permissions (iOS usage strings, Android `CAMERA` /
`RECORD_AUDIO`) are configured in `app.json` via the LiveKit Expo plugin and
`@config-plugins/react-native-webrtc`.

## Troubleshooting

- **Matched but no video/audio** → LiveKit is advertising a stale IP; re-run
  `make up` in debate-infra (see above), then Next to get a fresh room.
- **Stuck on "Searching"** with API reachable → check the api logs; make sure
  both clients picked the *same topic* and *opposite stances*.
- **`Network request failed` on first launch** → wrong `EXPO_PUBLIC_API_URL`,
  or Metro wasn't restarted after changing `.env`.
- **Build fails in Xcode** → accept the license (`sudo xcodebuild -license
  accept`), `pod repo update`, retry.
