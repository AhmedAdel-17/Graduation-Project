/**
 * Firebase SDK initialization — reads config from VITE_FIREBASE_* env vars.
 *
 * Setup instructions:
 * 1. Go to https://console.firebase.google.com and create a new project
 * 2. Enable Authentication → Sign-in providers → Email/Password + Google
 * 3. Go to Project Settings → General → Your apps → Web app → Register
 * 4. Copy the firebaseConfig values into dashboard/.env (see .env.example)
 */
import { initializeApp, type FirebaseApp } from "firebase/app";
import {
  getAuth,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signInWithPopup,
  GoogleAuthProvider,
  signOut,
  sendPasswordResetEmail,
  onAuthStateChanged,
  updateProfile,
  type Auth,
  type User,
} from "firebase/auth";

// ---------------------------------------------------------------------------
// Config — all values sourced from VITE_FIREBASE_* env vars
// ---------------------------------------------------------------------------
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY ?? "",
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN ?? "",
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID ?? "",
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET ?? "",
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID ?? "",
  appId: import.meta.env.VITE_FIREBASE_APP_ID ?? "",
};

// Validate that at minimum apiKey + projectId are present
if (!firebaseConfig.apiKey || !firebaseConfig.projectId) {
  console.error(
    "[firebase] Missing VITE_FIREBASE_API_KEY or VITE_FIREBASE_PROJECT_ID. " +
      "Copy dashboard/.env.example to dashboard/.env and fill in your Firebase config."
  );
}

// ---------------------------------------------------------------------------
// Singleton instances
// ---------------------------------------------------------------------------
const app: FirebaseApp = initializeApp(firebaseConfig);
const auth: Auth = getAuth(app);
const googleProvider = new GoogleAuthProvider();

// ---------------------------------------------------------------------------
// Auth helpers — thin wrappers for consistent error handling
// ---------------------------------------------------------------------------
async function doSignIn(email: string, password: string) {
  return signInWithEmailAndPassword(auth, email, password);
}

async function doSignUp(email: string, password: string, displayName?: string) {
  const cred = await createUserWithEmailAndPassword(auth, email, password);
  if (displayName && cred.user) {
    await updateProfile(cred.user, { displayName });
  }
  return cred;
}

async function doGoogleSignIn() {
  return signInWithPopup(auth, googleProvider);
}

async function doSignOut() {
  return signOut(auth);
}

async function doPasswordReset(email: string) {
  return sendPasswordResetEmail(auth, email);
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------
export {
  app,
  auth,
  doSignIn,
  doSignUp,
  doGoogleSignIn,
  doSignOut,
  doPasswordReset,
  onAuthStateChanged,
};
export type { User };
