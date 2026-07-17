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
npm install --legacy-peer-deps
cp .env.example .env   # set EXPO_PUBLIC_API_URL to your Mac's LAN IP
```

`--legacy-peer-deps` is needed because `@config-plugins/react-native-webrtc`
declares peer support up to Expo SDK 56 while the app is on SDK 57; the
plugin only writes permissions into the native projects at prebuild and works
fine.

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
   video full-bleed, local preview PiP, mic/cam toggles, Report (coming
   soon), End (`POST /matches/{id}/end` → Home), Next (end + requeue with the
   same selection). "Opponent left" overlay when the peer disconnects.

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
- `src/state/` — zustand stores (auth/device identity, selection + match)
- `src/app/` — expo-router screens (`_layout` gates on auth, `index` Home,
  `queue`, `room`)
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
