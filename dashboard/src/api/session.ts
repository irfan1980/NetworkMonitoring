// Leaf module: holds the current bearer token so the HTTP layer never imports a store.
let token: string | null = null;
let invalidHandler: (() => void) | null = null;

export function getSessionToken(): string | null {
  return token;
}

export function setSessionToken(next: string | null): void {
  token = next;
}

export function onSessionInvalid(handler: () => void): void {
  invalidHandler = handler;
}

export function notifySessionInvalid(): void {
  invalidHandler?.();
}
