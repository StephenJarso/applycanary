# Running ApplyCanary on Mobile

Three ways to test and run ApplyCanary on a physical mobile device or mobile emulator.

---

## Option 1 — Wi-Fi Browser (quickest, no extra tooling)

This is the fastest way to test on a real phone during development.

### Prerequisites
- Your dev machine and phone must be on the **same Wi-Fi network**.
- Find your machine's local IP address:
  ```bash
  ip route get 1 | awk '{print $7; exit}'
  # example output: 192.168.89.27
  ```

### Steps

1. **Start the backend** bound to all interfaces:
   ```bash
   cd /home/sjarso/applycanary
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

2. **Start the frontend** bound to all interfaces:
   ```bash
   cd frontend
   npm run dev -- --host 0.0.0.0
   # Vite will print:  ➜  Network: http://192.168.89.27:5173/
   ```

3. **Open on your phone:**
   - Open your phone's browser and navigate to `http://<YOUR_LAN_IP>:5173`
   - Replace `<YOUR_LAN_IP>` with the IP printed by Vite (e.g. `192.168.89.27`)

4. **Optional — Add to Home Screen (PWA):**
   - **iOS Safari:** Share → "Add to Home Screen"
   - **Android Chrome:** menu → "Add to Home screen"
   - The app will launch full-screen and behave like a native app.

> [!NOTE]
> The local IP (`192.168.89.27` above) is just an example — yours will differ.
> It changes when your machine reconnects. Check it each session.

---

## Option 2 — Browser DevTools Mobile Emulation

Good for UI/layout checks without needing a real device.

1. Open `http://localhost:5173` in Chrome or Edge.
2. Press **F12** to open DevTools.
3. Click the **Toggle device toolbar** icon (📱) or press `Ctrl+Shift+M`.
4. Select a preset device from the dropdown (e.g. **iPhone 14 Pro**, **Pixel 7**, **Galaxy S23**).
5. Refresh the page — the viewport will simulate that device's screen size and pixel ratio.

> [!TIP]
> Use **"Responsive"** mode and drag the viewport edge to test breakpoints at arbitrary widths.

---

## Option 3 — Native App via Capacitor (Android / iOS)

Capacitor wraps the Vite frontend in a native WebView and deploys it to a real device or emulator. The project already has Capacitor configured.

### Prerequisites
- **Android:** Android Studio installed, with an emulator or a physical device connected via USB with USB debugging enabled.
- **iOS:** macOS with Xcode installed (iOS builds can only be done on macOS).

### Steps

1. **Set the API base URL** (the app needs to reach the backend from inside the native WebView):
   ```bash
   export VITE_API_BASE=http://192.168.89.27:8000
   # or your deployed backend URL, e.g. https://api.applycanary.com
   ```

2. **Build the frontend and sync to native projects:**
   ```bash
   cd frontend
   npm run build
   npx cap sync
   ```

3. **Open in Android Studio:**
   ```bash
   npx cap open android
   ```
   Then press ▶ **Run** in Android Studio to deploy to your emulator or connected device.

4. **Open in Xcode (macOS only):**
   ```bash
   npx cap open ios
   ```
   Then press ▶ **Run** in Xcode.

> [!IMPORTANT]
> The backend **must** be reachable from the device. Running `--host 0.0.0.0` on your dev machine works for LAN testing. For a Capacitor build pointed at `localhost`, the backend won't be reachable from the device — always use a LAN IP or deployed URL.

### Runtime API Base Override (dev convenience)

You can override the API base at runtime without a rebuild by running this in the native WebView console (Chrome DevTools remote debugging):

```js
localStorage.setItem("ac.api-base", "http://192.168.89.27:8000")
location.reload()
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Phone can't reach the dev server | Ensure both are on the same Wi-Fi; check firewall (`sudo ufw allow 5173` and `sudo ufw allow 8000`) |
| CORS errors in browser | Backend is already configured for CORS; if adding a new origin, update `allow_origins` in `app/main.py` |
| iOS keyboard pushes layout up | The app uses `env(safe-area-inset-*)` and disables scroll on `<body>`; test in Safari on device |
| Capacitor sync fails | Run `npm install` inside `frontend/` first, then `npx cap sync` |
| API requests fail in Capacitor | Confirm `VITE_API_BASE` is set to a reachable host (not `localhost`) before `npm run build` |
