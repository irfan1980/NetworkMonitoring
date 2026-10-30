// Run: npm run check:firebase -- resolves the web push config exactly as the app sees it and validates it.
const factory = require('../app.config.js');

const resolved = factory({ config: { android: {}, ios: {}, extra: {} } });
const fw = resolved.extra.firebaseWeb;
const problems = [];

if (!fw) problems.push('dashboard/firebase/google-services-web.json is missing or not valid JSON');
else {
  for (const k of ['apiKey', 'projectId', 'messagingSenderId', 'appId']) {
    if (typeof fw.config[k] !== 'string' || !fw.config[k]) problems.push(`${k} missing`);
  }
  const v = fw.vapidKey;
  if (typeof v !== 'string' || !/^B[A-Za-z0-9_-]{85,87}$/.test(v.trim())) {
    problems.push(`vapidKey invalid (${typeof v}${typeof v === 'string' ? `, ${v.length} chars, starts "${v.charAt(0)}"` : ''})`);
  }
}

if (problems.length) {
  console.error('Web push config NOT ok:\n - ' + problems.join('\n - '));
  process.exit(1);
}
console.log('Web push config ok (vapidKey is an 87-char base64url string; config keys: ' + Object.keys(fw.config).join(', ') + ')');
