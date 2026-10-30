export interface AccountWindow {
  timezone: string;
  days: number[];
  start: string;
  end: string;
}

export interface DowntimeWindow extends AccountWindow {
  id: string | number;
  machine: string;
  service: string | null;
  note: string;
}

export type DowntimeInput = Omit<DowntimeWindow, 'id'>;
