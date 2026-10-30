import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { loadDeviceId, saveDeviceId } from '../api/deviceIdStorage';
import { defaultDeviceName } from '../utils/deviceName';
import { uuidv4 } from '../utils/uuid';

const CLIENT_ID_KEY = 'sb_dashboard_client_id';
const DEVICE_NAME_KEY = 'sb_dashboard_device_name';

interface DeviceState {
  clientId: string | null;
  deviceId: string | null;
  device: string;
  ready: boolean;
  init: () => Promise<void>;
  setDevice: (name: string) => Promise<void>;
}

export const useDeviceStore = create<DeviceState>((set, get) => ({
  clientId: null,
  deviceId: null,
  device: defaultDeviceName(),
  ready: false,
  init: async () => {
    if (get().ready) return;
    let clientId = await AsyncStorage.getItem(CLIENT_ID_KEY);
    if (!clientId) {
      clientId = uuidv4();
      await AsyncStorage.setItem(CLIENT_ID_KEY, clientId);
    }
    let deviceId: string | null = null;
    try {
      deviceId = await loadDeviceId();
      if (!deviceId) {
        deviceId = uuidv4();
        await saveDeviceId(deviceId);
      }
    } catch {
      deviceId = deviceId ?? uuidv4();
    }
    const device = (await AsyncStorage.getItem(DEVICE_NAME_KEY)) || defaultDeviceName();
    set({ clientId, deviceId, device, ready: true });
  },
  setDevice: async (name: string) => {
    await AsyncStorage.setItem(DEVICE_NAME_KEY, name);
    set({ device: name });
  },
}));
