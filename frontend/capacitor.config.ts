import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Capacitor configuration for the ApplyCanary mobile app.
 *
 * The native shell serves the built frontend from `capacitor://localhost`
 * (iOS) / `http://localhost` (Android), so the frontend must talk to the
 * backend over an absolute URL — see `src/api.ts` (VITE_API_BASE) and the
 * CORS middleware in `app/main.py`.
 */
const config: CapacitorConfig = {
  appId: 'app.applycanary.mobile',
  appName: 'ApplyCanary',
  webDir: 'dist',
  // Session cookies survive inside the WebView (capacitor://localhost origin).
  server: {
    androidScheme: 'https',
  },
  ios: {
    contentInset: 'always',
  },
  plugins: {
    SplashScreen: {
      // The generated splash art is indigo (#5000E1) — see
      // scripts/generate_assets.py, which paints every res/drawable* and the
      // iOS Splash.imageset from the same mark.
      backgroundColor: '#5000E1',
      androidScaleType: 'CENTER_CROP',
      launchAutoHide: true,
      launchShowDuration: 800,
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: true,
    },
  },
};

export default config;
