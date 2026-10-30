import { getMessaging, setBackgroundMessageHandler } from '@react-native-firebase/messaging';
import { ensureAlertChannels } from './channels.native';

// Must be registered at module load (see index.js), before the root component, so a killed app can
// handle a push headlessly. Channels are also created here, so they exist from the first launch, before any sign-in. Notification messages are drawn by the system into the sev{n}_{kind} channel the relay names.
void ensureAlertChannels().catch(() => {});
setBackgroundMessageHandler(getMessaging(), async () => {
  await ensureAlertChannels();
});
export {};
