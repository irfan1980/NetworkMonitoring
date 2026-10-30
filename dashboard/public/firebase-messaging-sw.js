/* eslint-env serviceworker */
importScripts('https://www.gstatic.com/firebasejs/12.0.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/12.0.0/firebase-messaging-compat.js');

// Config arrives on the registration URL (?c=<json>); web config is not secret.
var config = null;
try {
  config = JSON.parse(new URL(self.location.href).searchParams.get('c') || 'null');
} catch (e) {}

if (config && config.apiKey) {
  firebase.initializeApp(config);
  var messaging = firebase.messaging();
  // The browser draws the notification; tell an open (unfocused) tab to play the matching tone.
  messaging.onBackgroundMessage(function (payload) {
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      list.forEach(function (c) {
        c.postMessage({ type: 'sb-sound', data: payload.data || {} });
      });
    });
  });
}

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var n = event.notification.data || {};
  var data = (n.FCM_MSG && n.FCM_MSG.data) || n;
  var alertId = data.alertId || null;
  var isEnabled = data.type === 'push_enabled';
  var target = isEnabled ? '/notifications' : data.url || (alertId ? '/alerts?alertId=' + encodeURIComponent(alertId) : '/alerts');
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        if ('focus' in list[i]) {
          list[i].postMessage({ type: 'sb-open', alertId: alertId, pushType: data.type || null });
          return list[i].focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
