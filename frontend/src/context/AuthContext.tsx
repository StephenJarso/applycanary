import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type AuthUser } from "../api";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** Resolves to the created account. Check `session_started` before navigating
   *  into the app — when email verification is enforced there is no session yet. */
  register: (email: string, password: string, inviteCode: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
  /** Re-pull /auth/me. Used after confirming an address so the nudge banner clears. */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void api.auth.me().then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);

  const login = async (email: string, password: string) => {
    setUser(await api.auth.login(email, password));
  };

  const register = async (email: string, password: string, inviteCode: string) => {
    const created = await api.auth.register(email, password, inviteCode);
    // Only adopt the account as the current user if the backend actually issued
    // a session; otherwise the app would render as logged in and 401 on load.
    if (created.session_started) setUser(created);
    return created;
  };

  const logout = async () => {
    try {
      await api.auth.logout();
    } finally {
      setUser(null);
    }
  };

  const refresh = async () => {
    setUser(await api.auth.me().catch(() => null));
  };

  return <AuthContext.Provider value={{ user, loading, login, register, logout, refresh }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
