const fs = require('fs');
const path = require('path');

// Web Firebase config lives in the gitignored dashboard/firebase/google-services-web.json.
function readWebConfig() {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, 'firebase', 'google-services-web.json'), 'utf8'));
  } catch (e) {
    return null;
  }
}

// Accept the key as a plain string; tolerate a console-style object by picking its string field.
function pickVapid(v) {
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object') {
    for (const k of ['publicKey', 'vapidKey', 'key', 'value']) {
      if (typeof v[k] === 'string') return v[k];
    }
  }
  return null;
}

// EAS cloud builds do not upload gitignored files; the Firebase native configs come in as EAS file env vars there.
module.exports = ({ config }) => {
  const web = readWebConfig();
  const { vapidKey, ...firebaseWeb } = web || {};
  const vapid = pickVapid(vapidKey);
  if (vapidKey !== undefined && vapid === null) {
    console.warn(`[config] firebase/google-services-web.json: "vapidKey" must be a string, got ${typeof vapidKey}`);
  }
  return {
    ...config,
    android: {
      ...config.android,
      googleServicesFile: process.env.GOOGLE_SERVICES_JSON || config.android.googleServicesFile,
    },
    ios: {
      ...config.ios,
      googleServicesFile: process.env.GOOGLE_SERVICES_PLIST || config.ios.googleServicesFile,
    },
    extra: {
      ...config.extra,
      firebaseWeb: web ? { config: firebaseWeb, vapidKey: vapid } : null,
    },
  };
};
