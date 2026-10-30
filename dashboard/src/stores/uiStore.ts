import { create } from 'zustand';

export type ToastKind = 'success' | 'error' | 'warning' | 'info';

export interface Toast {
  id: string;
  kind: ToastKind;
  message: string;
}

interface UiState {
  toasts: Toast[];
  showToast: (kind: ToastKind, message: string) => void;
  showSuccess: (message: string) => void;
  showError: (message: string) => void;
  showWarning: (message: string) => void;
  showInfo: (message: string) => void;
  dismissToast: (id: string) => void;
}

let toastCounter = 0;

export const useUIStore = create<UiState>((set, get) => ({
  toasts: [],
  showToast: (kind, message) => {
    const id = `toast_${Date.now()}_${toastCounter++}`;
    set({ toasts: [...get().toasts, { id, kind, message }] });
    setTimeout(() => get().dismissToast(id), 4000);
  },
  showSuccess: (message) => get().showToast('success', message),
  showError: (message) => get().showToast('error', message),
  showWarning: (message) => get().showToast('warning', message),
  showInfo: (message) => get().showToast('info', message),
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));
