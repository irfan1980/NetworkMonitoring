export type PushPlatform = 'android' | 'ios' | 'web';

export interface PushRegistration {
  account: string;
  minSeverity: number | null;
  confirmationSent: boolean | null;
  confirmationError: string | null;
}

export interface PushPermissionState {
  status: 'granted' | 'denied' | 'undetermined';
  canAskAgain: boolean;
}

export interface PushChannelInfo {
  ok: boolean;
  detail: string;
}

export type PushOpenTarget = { alertId: string | null; type: string | null };

export interface PushStatus {
  registered: boolean;
  account: string | null;
  minSeverity: number | null;
  pushEnabled: boolean;
}

export type PushSupport = { supported: true } | { supported: false; reason: string };
