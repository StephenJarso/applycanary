import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

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

// basename matches the /ui mount in app/main.py.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter basename="/ui">
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
