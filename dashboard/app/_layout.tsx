import 'react-native-gesture-handler';
import React, { useEffect } from 'react';
import { View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { ThemeProvider } from '../src/theme/ThemeContext';
import { colors } from '../src/theme/colors';
import { useRelaySocket } from '../src/hooks/useRelaySocket';
import { useAlarm } from '../src/hooks/useAlarm';
import { useRedBannerAlert } from '../src/hooks/useRedBannerAlert';
import { AppNav } from '../src/components/AppNav';
import { AppContent } from '../src/components/AppContent';
import { ToastHost } from '../src/components/ToastHost';
import { RedAlertBanner } from '../src/components/RedAlertBanner';
import { ReconnectingOverlay } from '../src/components/ReconnectingOverlay';
import { useConnectionLost } from '../src/hooks/useConnectionLost';
import { usePushBootstrap } from '../src/hooks/usePushBootstrap';
import { useAuthStore } from '../src/stores/authStore';
import { AccessGateScreen } from '../src/components/AccessGateScreen';

function AppShell() {
  const { sendSilence, sendAck } = useRelaySocket();
  useAlarm();
  usePushBootstrap();
  const { top, otherActiveCount } = useRedBannerAlert();
  const connectionLoss = useConnectionLost();

  return (
		<View
			style={[
				{ flex: 1 },
				top ? { borderWidth: 3, borderColor: colors.bad } : null,
			]}
		>
			{top ? (
				<RedAlertBanner
					alert={top}
					otherActiveCount={otherActiveCount}
					onSilence={sendSilence}
					onAck={sendAck}
				/>
			) : (
				<AppNav />
			)}
			<AppContent
				overlay={
					connectionLoss.visible ? (
						<ReconnectingOverlay
							downSinceMs={connectionLoss.downSinceMs}
							attempts={connectionLoss.attempts}
							nextRetryAtMs={connectionLoss.nextRetryAtMs}
							everConnected={connectionLoss.everConnected}
						/>
					) : null
				}
			>
				<Stack screenOptions={{ headerShown: false, animation: "fade" }} />
			</AppContent>
			<ToastHost />
		</View>
  );
}

function GatedApp() {
  const ready = useAuthStore((s) => s.ready);
  const unlocked = useAuthStore((s) => s.token !== null);
  const init = useAuthStore((s) => s.init);

  useEffect(() => {
    init();
  }, [init]);

  if (!ready) return <View style={{ flex: 1, backgroundColor: colors.background }} />;
  if (!unlocked) return <AccessGateScreen />;
  return <AppShell />;
}

export default function RootLayout() {
  return (
		<GestureHandlerRootView
			style={{ flex: 1, backgroundColor: colors.background }}
		>
			<SafeAreaProvider>
        <ThemeProvider>
          <StatusBar style="light" />
          <GatedApp />
        </ThemeProvider>
			</SafeAreaProvider>
		</GestureHandlerRootView>
  );
}
