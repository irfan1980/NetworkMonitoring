# dashboard

Expo (SDK 57, TypeScript) app for the Service Bus monitoring dashboard: a live view of the DB-to-DB sync estate served by `relay/`, read-only towards the databases and buses. The Scheduler tab (`app/scheduler/`) creates and edits schedules on the relay and starts test runs on machines; every such action needs the relay's PIN (`docs/scheduler/`, `docs/dashboard/architecture/frontend/screens/Scheduler.md`). It runs as a static web build (wall display) and as an Android/iOS app. Design notes live in `../docs/dashboard/`; the wire format is `../docs/dashboard-data-contract.md`.

The app talks to one relay: one WebSocket for the machine inventory, one WebSocket per online machine, and a few HTTP calls for drill-down. There are no user accounts. A kiosk access-code gate and relay client credentials exist (below); neither is a user system.

## Stack

- Node >=24.12 (`engines`; the `@firebase/*` packages require it). Install with `nvm install 24.21.0` and `nvm use 24.21.0` from an elevated terminal. Tested on 24.21.0. The package manager is yarn classic (1.22.x, `yarn.lock`; there is no `package-lock.json`, and EAS picks yarn from the lockfile). Yarn 1 runs dependency install scripts by default, so no script approval is needed (`@firebase/util`'s postinstall only reads the optional `FIREBASE_WEBAPP_CONFIG` env var).
- Expo SDK 57 (expo 57.0.28, react 19.2.3, react-native 0.86.3, expo-router 57.0.26, expo-notifications 57.0.22, @react-native-firebase 26.4.0, firebase 13.0.0), TypeScript, `expo-router` (file-based routes; a custom top nav bar, `src/components/AppNav.tsx`, instead of a Drawer or tab navigator)
- `react-native-svg` + `react-native-reanimated` + `react-native-gesture-handler` for the topology graph
- `zustand` for state (`src/stores/`)
- `@react-native-async-storage/async-storage` for the per-device `clientId`, device name and the unlock flag

## Getting started

```bash
yarn install
cp .env.example .env     # then edit the EXPO_PUBLIC_* values
yarn web              # or: yarn android / yarn ios
```

### Environment variables (`.env`, optional `.env.local` overrides it)

| Variable | Default | Notes |
|---|---|---|
| `EXPO_PUBLIC_RELAY_WS_URL` | unset | The relay's root socket, e.g. `ws://<relay-host>:8085/ws`. Must match `settings.port` in `relay/config/relay_config.json`. When unset, web derives it from the page origin and every other platform falls back to `ws://localhost:8085/ws`; a console warning names the URL used (`src/config/env.ts`). Child sockets and HTTP calls derive from this value. On the Android emulator the host machine is `10.0.2.2`; on a physical device use the relay host's LAN IP. |
| `EXPO_PUBLIC_FIXTURE` | `0` | `1` renders the bundled fixture (`src/fixtures/snapshot.json`) with no relay; `3` loads the 3-server variant (`src/fixtures/snapshot.3.json`). Diagnostics calls also resolve from `src/fixtures/diagnostics.ts` in fixture mode. |
| `EXPO_PUBLIC_LANE_NODE_MIN` | `70` | Min length (world units at 1x) of a topology lane's node/machine-side segments (card or node edge out to the bus glyph). |
| `EXPO_PUBLIC_LANE_BUS_MIN` | `150` | Min length (world units at 1x) of a lane's service-bus segment (between the two glyphs). Service cards are spaced so this is met. |

`EXPO_PUBLIC_*` values are inlined into the bundle at build time. `.env.example` is the tracked template and carries placeholders only; `.env` and `.env.local` are git-ignored.

## Sign-in and session

Every platform shows `AccessGateScreen` (account secret) before anything else. The secret is exchanged for a bearer token with `POST /api/auth/login`; the token and account are stored in `expo-secure-store` (native) or `localStorage` (web). `relayFetch()` (`src/api/relayHttp.ts`) sends `Authorization: Bearer <token>` and sockets use `?token=` (`withSessionToken()`). A 401 `session_invalid` or WebSocket close `4401` clears the token and returns to the sign-in screen with "Session expired, sign in again". LOCK in the nav logs out (revokes the token and unregisters this device from push). The relay must be restarted on a build that has `/api/auth/*` and `/api/push/*`; otherwise sign-in says "Relay needs restarting to enable login/push."

## Firebase files (push notifications)

The dashboard is self-contained: its Firebase client configs live in `dashboard/firebase/` (git-ignored, copy them onto the dashboard server; the relay keeps its own copies under `relay/config/`).

| File | Used by |
|---|---|
| `firebase/google-services-android.json` | Android build (`android.googleServicesFile`) |
| `firebase/google-services-ios.plist` | iOS build (`ios.googleServicesFile`) |
| `firebase/google-services-web.json` | Web push: `apiKey`, `authDomain`, `projectId`, `storageBucket`, `messagingSenderId`, `appId` and `vapidKey` (Web Push certificate key pair). `app.config.js` injects it into the page (`extra.firebaseWeb`) and passes it to `public/firebase-messaging-sw.js` on the service-worker URL. Restart `expo start` after editing it. |

`EXPO_PUBLIC_FIREBASE_VAPID_KEY` is a fallback when `vapidKey` is missing from the web file. For EAS cloud builds (which do not upload git-ignored files) the Android/iOS files are EAS file variables `GOOGLE_SERVICES_JSON` / `GOOGLE_SERVICES_PLIST` in the `development` environment. Web push works on `http://localhost` or HTTPS only.

## Scripts

```bash
yarn web          # expo start --web
yarn android      # expo start --android
yarn ios          # expo start --ios
yarn build:web    # expo export --platform web -> dist/
yarn typecheck    # tsc --noEmit
```

`android.usesCleartextTraffic` is enabled through `expo-build-properties` (`app.json`) so the app can reach a plain `ws://` relay on the LAN.

### Android over the LAN

Run `npx expo start --android` (or open it in Expo Go on a device on the same network) with `EXPO_PUBLIC_RELAY_WS_URL` pointing at the relay host's LAN IP, not `localhost`. Expo Go must match SDK 57. The relay's `settings.ws_allowed_origins` must allow the page origin if it is not loopback or a private LAN address.

### Wall-display build

```bash
npx expo export --platform web --output-dir dist --clear
```

- Use `--clear` whenever an `EXPO_PUBLIC_*` value changed; Metro's cache is not keyed on env values.
- `.env.local` overrides `.env` for exports as well. A leftover `EXPO_PUBLIC_FIXTURE=1` freezes the exported screen on fixture data.
- `EXPO_PUBLIC_FIXTURE` must be `0` for a live build.

The current relay (`relay/src`) has no static-file route, so `dist/` has to be served by a separate static host. Because the page origin then differs from the relay, set `EXPO_PUBLIC_RELAY_WS_URL` explicitly and make sure the relay allows the page origin (`settings.ws_allowed_origins`). A WebSocket `403` on the handshake means the origin is not allowed (close code `4403`).

Kiosk launch example (audio needs the autoplay flag so `useAlarm` can start without a click):

```bash
chrome --kiosk --autoplay-policy=no-user-gesture-required http://<static-host>/
```

## Folder structure

```
app/                          expo-router routes only
  _layout.tsx                 GatedApp -> AppShell: useRelaySocket + useAlarm, nav bar or RedAlertBanner,
                              AppContent (IssuesSidebar on every route + content-only ReconnectingOverlay),
                              toasts, full-screen UnauthorizedOverlay
  index.tsx                   Overview: topology graph
  pipelines.tsx               Pipelines grid (ObjectStrip, BusesStrip, PipelineCard)
  alerts.tsx                  Alerts list
  pipeline/[id].tsx           Pipeline detail
  scheduler/index.tsx         Scheduler list; new.tsx editor; [id].tsx detail + run history
src/
  api/relayHttp.ts            relayFetch(): credential headers, 401 -> unauthorized state
  components/                 AlertCard, PipelineCard, IssuesSidebar, RedAlertBanner, overlays,
                              AccessGateScreen, ObjectStrip, BusesStrip, HealthPill, Sparkline, DevPanel, ...
    topology/                 TopologyGraph, PairLane, BundledLane, PlatformEntity, GridTopologyView,
                              ZoomPanCanvas, Legend, ExternalInfraStrip and the pure layout modules
    diagnostics/              DiagnosticsModal, BusModal, PlatformModal, MachineModal, AlertDetailModal,
                              EntityDiagnosticRow
    scheduler/                ScheduleRow, ScheduleEditor, TriggerEditor, RunHistory, TestRunButton, StopRunButton, ui.tsx
  config/                     env.ts, relayCredentials.{web,native}.ts
  constants/queueThresholds.ts
  fixtures/                   snapshot.json, snapshot.3.json, diagnostics.ts, schedules.ts (fixture-mode schedules)
  hooks/                      useRelaySocket, useAlarm, useConnectionLost, useUnauthorized,
                              useIssueSummary, useRedBannerAlert, useDiagnosticsPoll, useResponsive, useRollingBuffer,
                              useSidebarVisible, useOverallStatus, usePinSession, useScheduleDetail, useScheduleRuns, useTriggerPreview
  services/                   relay.service.ts (dev-panel scenarios), diagnostics.ts (entity drill-down),
                              schedules.ts (schedule REST client, via relayFetch)
  stores/                     connectionStore, machinePayload, authStore, deviceStore, uiStore
  theme/                      dark theme colours, createThemedStyles
  types/                      relay.ts (wire types), diagnostics.ts, schedules.ts
  utils/                      health, machineLabel, platformGrouping, pairTotals, relaySummary, ...
```

## Topology

The Overview graph draws one box per machine containing that machine's services, and one lane per directed pair. A lane is a line of up to three segments: sender, bus, receiver. Machine labels read `Name (machine_label_id)`. Layout, lane rules, shuffle/relocate and the dense grid mode are described in `../docs/dashboard/topology.md`. Per-file notes are in `../docs/dashboard/architecture/`.

## Testing without a relay

`EXPO_PUBLIC_FIXTURE=1` renders everything from `src/fixtures/snapshot.json`: a healthy pair, a degraded pair, a down pipeline and two active alerts, so banner states, lane colours, piles and alert actions can be exercised with no relay or databases.

## Known gaps

- Pipeline detail's "Recent outbox / inbox errors" section is a placeholder; the app does not call the relay's pipeline-errors route.
- `relay.service.ts` and `DevPanel` call `POST /scenario/...`, which the current relay does not expose. The panel opens only when `source === 'mock'`.
- Android has been run through Expo Go only; no EAS build exists and iOS is untried.
- `dist/` is a point-in-time export and must be rebuilt after changes to `src/` or `app/`.

## Notification tones, downtime, push hours

- Tones: ten WAVs in `assets/sounds/sevarity-1..5/` (`sev{n}_first.wav`, `sev{n}_repeat.wav`), each severity folder also holds its source mp3; mapping and swap steps are in `docs/dashboard/push-notifications.md`. Bundled through the `expo-notifications` `sounds` plugin option in `app.json`; Android gets 10 channels `sev{n}_first|repeat` (`src/push/channels.native.ts`). Changing sounds or channels needs a new Android/iOS dev build. Web plays the WAV in an open tab.
- Relay push contract: FCM `android.notification.channel_id = sev{n}_{first|repeat}`, APNs `aps.sound = sev{n}_{kind}.wav`, data `severity`, `kind`, `sound_id`.
- `/downtime` screen (nav "Downtime"): recurring windows per machine or service, shared by all accounts, stops push only. Notifications screen has "My push hours" (own timezone/days/times, clear = always on). See `docs/dashboard/push-notifications.md`.

## Dependency audit

`yarn audit` reports 21 findings (3 low, 5 moderate, 13 high); the advisories are in Expo/Firebase transitive dependencies with no safe fix inside SDK 57 (`npm audit` on the old lockfile reported the same root causes and offered only an Expo 44 downgrade). All are in Expo/Firebase transitive dependencies, so they are accepted until Expo moves them: `braces` and `node-forge` (Metro / Expo CLI build tooling, not shipped in the app), `uuid` 7 (`xcode`, prebuild only), `decode-uri-component` (`query-string` in expo-router; fix needs SDK 58), `@grpc/grpc-js` (Firestore, pinned `~1.9` by firebase; the app imports only firebase/app and messaging). The notification icon is `assets/android-icon-monochrome.png`.
