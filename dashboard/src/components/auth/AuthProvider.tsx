import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useMemo,
  type ReactNode,
} from "react";
import {
  auth,
  onAuthStateChanged,
  doSignIn,
  doSignUp,
  doGoogleSignIn,
  doSignOut,
  doPasswordReset,
  type User,
} from "../../lib/firebase";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export interface AuthContextValue {
  /** Current Firebase user, or null if not signed in */
  user: User | null;
  /** True while Firebase is resolving the initial auth state */
  loading: boolean;
  /** Sign in with email/password */
  signIn: (email: string, password: string) => Promise<void>;
  /** Create account with email/password + optional display name */
  signUp: (email: string, password: string, displayName?: string) => Promise<void>;
  /** Sign in with Google popup */
  googleSignIn: () => Promise<void>;
  /** Sign out */
  logout: () => Promise<void>;
  /** Send password reset email */
  resetPassword: (email: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (firebaseUser) => {
      setUser(firebaseUser);
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    await doSignIn(email, password);
  }, []);

  const signUp = useCallback(
    async (email: string, password: string, displayName?: string) => {
      await doSignUp(email, password, displayName);
    },
    []
  );

  const googleSignIn = useCallback(async () => {
    await doGoogleSignIn();
  }, []);

  const logout = useCallback(async () => {
    await doSignOut();
  }, []);

  const resetPassword = useCallback(async (email: string) => {
    await doPasswordReset(email);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, signIn, signUp, googleSignIn, logout, resetPassword }),
    [user, loading, signIn, signUp, googleSignIn, logout, resetPassword]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth() must be used within <AuthProvider>");
  }
  return ctx;
}
