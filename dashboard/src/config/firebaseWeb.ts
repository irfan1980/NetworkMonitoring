import Constants from 'expo-constants';
import { normalizeVapid, vapidProblem } from './vapid';

interface FirebaseWebExtra {
  config: Record<string, string>;
  vapidKey: unknown;
}

// Injected by app.config.js from dashboard/firebase/google-services-web.json (gitignored).
const extra = (Constants.expoConfig?.extra?.firebaseWeb ?? null) as FirebaseWebExtra | null;

export const FIREBASE_WEB_CONFIG: Record<string, string> = extra?.config ?? {};

const rawVapid: unknown = extra?.vapidKey || process.env.EXPO_PUBLIC_FIREBASE_VAPID_KEY || '';

export const FIREBASE_VAPID_KEY = normalizeVapid(rawVapid);

export const VAPID_PROBLEM: string | null = vapidProblem(rawVapid);

export const FIREBASE_MISSING: string[] = ['apiKey', 'projectId', 'messagingSenderId', 'appId'].filter(
  (k) => !FIREBASE_WEB_CONFIG[k]
);

export const FIREBASE_WEB_CONFIGURED = FIREBASE_MISSING.length === 0 && VAPID_PROBLEM === null;
