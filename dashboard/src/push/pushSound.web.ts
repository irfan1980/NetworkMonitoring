import { Asset } from 'expo-asset';
import { parsePushSound, soundId } from './sounds';

const FILES: Record<string, number> = {
  sev1_first: require('../../assets/sounds/sevarity-1/sev1_first.wav'),
  sev1_repeat: require('../../assets/sounds/sevarity-1/sev1_repeat.wav'),
  sev2_first: require('../../assets/sounds/sevarity-2/sev2_first.wav'),
  sev2_repeat: require('../../assets/sounds/sevarity-2/sev2_repeat.wav'),
  sev3_first: require('../../assets/sounds/sevarity-3/sev3_first.wav'),
  sev3_repeat: require('../../assets/sounds/sevarity-3/sev3_repeat.wav'),
  sev4_first: require('../../assets/sounds/sevarity-4/sev4_first.wav'),
  sev4_repeat: require('../../assets/sounds/sevarity-4/sev4_repeat.wav'),
  sev5_first: require('../../assets/sounds/sevarity-5/sev5_first.wav'),
  sev5_repeat: require('../../assets/sounds/sevarity-5/sev5_repeat.wav'),
};

let current: HTMLAudioElement | null = null;

export function playPushSound(data: unknown): void {
  const ref = parsePushSound(data);
  const module = ref ? FILES[soundId(ref)] : undefined;
  if (module === undefined || typeof Audio === 'undefined') return;
  current?.pause();
  current = new Audio(Asset.fromModule(module).uri);
  // Autoplay is refused until the user has interacted with the page; the default notification sound still applies.
  void current.play().catch(() => {});
}
