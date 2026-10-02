import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Analytics } from "@vercel/analytics/react";
import { initNative } from "./native";
import App from "./App";
import { AuthProvider } from "./context/AuthContext";
import "./styles.css";

// Patches fetch for the Capacitor WebView (native HTTP, no CORS) before any
// component can fire a request. Resolves immediately in a plain browser.
void initNative();

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // The backend polls job boards on its own schedule, so data goes stale
      // on its own clock. Refetch on focus rather than on an interval.
      staleTime: 20_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AuthProvider><App /></AuthProvider>
        {/* Injects Vercel's insights script, which patches the history API
            itself — so client-side route changes are counted without wiring
            anything into the router. Inert outside a Vercel deployment. */}
        <Analytics />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
