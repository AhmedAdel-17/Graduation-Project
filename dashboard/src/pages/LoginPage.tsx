import { useState, type FormEvent } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { toast } from "sonner";
import { Eye, EyeOff, Mail, Lock, ArrowRight } from "lucide-react";
import { useAuth } from "../components/auth/AuthProvider";
import { BrandLogo } from "../components/ui/BrandLogo";
import { LocaleToggle } from "../components/ui/LocaleToggle";
import { useTheme } from "../hooks/useTheme";
import { useT } from "../lib/i18n";
import { Sun, Moon } from "lucide-react";

/** Google "G" icon — inline SVG for the sign-in button */
function GoogleIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden>
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  );
}

export function LoginPage() {
  const { signIn, googleSignIn, resetPassword } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { theme, toggle } = useTheme();
  const t = useT();

  const returnTo = (location.state as { returnTo?: string })?.returnTo || "/predict";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [resetSent, setResetSent] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      toast.error(t("auth.toast.enterCreds"));
      return;
    }
    setSubmitting(true);
    try {
      await signIn(email, password);
      toast.success(t("auth.toast.welcomeBack"));
      navigate(returnTo, { replace: true });
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message.replace("Firebase: ", "") : t("auth.toast.signInFailed");
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleGoogleSignIn = async () => {
    try {
      await googleSignIn();
      toast.success(t("auth.toast.googleSuccess"));
      navigate(returnTo, { replace: true });
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message.replace("Firebase: ", "") : t("auth.toast.googleFailed");
      if (!msg.includes("popup-closed")) toast.error(msg);
    }
  };

  const handleForgotPassword = async () => {
    if (!email) {
      toast.error(t("auth.toast.enterEmailFirst"));
      return;
    }
    try {
      await resetPassword(email);
      setResetSent(true);
      toast.success(t("auth.toast.resetSent"));
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message.replace("Firebase: ", "") : t("auth.toast.resetFailed");
      toast.error(msg);
    }
  };

  return (
    <div className="min-h-screen flex bg-[var(--bg)] relative">
      {/* Floating controls — language + theme */}
      <div className="absolute top-5 end-5 z-10 flex items-center gap-2">
        <LocaleToggle />
        <button
          type="button"
          onClick={toggle}
          aria-label={theme === "dark" ? t("shell.toLight") : t("shell.toDark")}
          className="inline-flex items-center justify-center h-9 w-9 rounded-full
            border border-stone-200 bg-white hover:bg-stone-50 text-stone-600 transition-colors
            dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:hover:bg-[var(--hairline)] dark:text-[var(--ink-2)]"
        >
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      {/* ─── Left hero panel ──────────────────────────────────────── */}
      <div className="hidden lg:flex lg:w-[480px] xl:w-[540px] shrink-0 relative overflow-hidden">
        {/* Navy gradient background */}
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(160deg, #0E1D38 0%, #14284A 40%, #1E3A66 100%)",
          }}
        />
        {/* Subtle grid pattern */}
        <div
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }}
        />
        {/* Green accent bar */}
        <div
          className="absolute bottom-0 inset-x-0 h-1"
          style={{ background: "linear-gradient(90deg, #2FA35B, #43C07A)" }}
        />

        {/* Hero content */}
        <div className="relative z-10 flex flex-col justify-center px-12 xl:px-16">
          <div className="flex items-center gap-3 mb-8">
            <BrandLogo className="h-12 w-12" />
            <span className="display text-[28px] font-semibold tracking-tight text-white">
              StockHive
            </span>
          </div>
          <h1 className="display text-[36px] xl:text-[40px] font-bold leading-[1.15] text-white mb-4 whitespace-pre-line">
            {t("auth.hero.loginTitle")}
          </h1>
          <p className="text-[15px] leading-relaxed text-white/60 max-w-[360px]">
            {t("auth.hero.loginDesc")}
          </p>


        </div>
      </div>

      {/* ─── Right form panel ─────────────────────────────────────── */}
      <div className="flex-1 flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-[420px]">
          {/* Mobile brand header */}
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <BrandLogo className="h-9 w-9" />
            <span className="display text-[20px] font-semibold tracking-tight text-[var(--brand-navy)]">
              StockHive
            </span>
          </div>

          <h2 className="display text-[26px] font-bold tracking-tight text-ink mb-1">
            {t("auth.login.title")}
          </h2>
          <p className="text-[14px] text-stone-500 dark:text-[var(--ink-3)] mb-8">
            {t("auth.login.subtitle")}
          </p>

          {/* Google sign-in */}
          <button
            type="button"
            onClick={handleGoogleSignIn}
            className="w-full flex items-center justify-center gap-2.5 h-11 rounded-xl
              border border-stone-200 bg-white hover:bg-stone-50 text-[14px] font-medium text-stone-700
              transition-all duration-150
              dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:text-[var(--ink)] dark:hover:bg-[var(--hairline)]"
          >
            <GoogleIcon className="h-[18px] w-[18px]" />
            {t("auth.google")}
          </button>

          {/* Divider */}
          <div className="flex items-center gap-3 my-6">
            <div className="flex-1 h-px bg-stone-200 dark:bg-[var(--hairline)]" />
            <span className="text-[12px] text-stone-400 dark:text-[var(--ink-3)] uppercase tracking-wider font-medium">
              {t("auth.or")}
            </span>
            <div className="flex-1 h-px bg-stone-200 dark:bg-[var(--hairline)]" />
          </div>

          {/* Email/Password form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Email */}
            <div>
              <label
                htmlFor="login-email"
                className="block text-[12.5px] font-medium text-stone-600 dark:text-[var(--ink-2)] mb-1.5"
              >
                {t("auth.email")}
              </label>
              <div className="relative" dir="ltr">
                <Mail className="absolute start-3 top-1/2 -translate-y-1/2 h-4 w-4 text-stone-400 dark:text-[var(--ink-3)] pointer-events-none" />
                <input
                  id="login-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="w-full h-11 ps-10 pe-3 rounded-xl border border-stone-200 bg-white text-[14px] text-ink
                    placeholder:text-stone-400
                    focus:outline-none focus:ring-2 focus:ring-[var(--brand-green)]/30 focus:border-[var(--brand-green)]
                    transition-colors
                    dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:placeholder:text-[var(--ink-3)]"
                />
              </div>
            </div>

            {/* Password */}
            <div>
              <label
                htmlFor="login-password"
                className="block text-[12.5px] font-medium text-stone-600 dark:text-[var(--ink-2)] mb-1.5"
              >
                {t("auth.password")}
              </label>
              <div className="relative" dir="ltr">
                <Lock className="absolute start-3 top-1/2 -translate-y-1/2 h-4 w-4 text-stone-400 dark:text-[var(--ink-3)] pointer-events-none" />
                <input
                  id="login-password"
                  type={showPw ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full h-11 ps-10 pe-10 rounded-xl border border-stone-200 bg-white text-[14px] text-ink
                    placeholder:text-stone-400
                    focus:outline-none focus:ring-2 focus:ring-[var(--brand-green)]/30 focus:border-[var(--brand-green)]
                    transition-colors
                    dark:bg-[var(--paper)] dark:border-[var(--hairline)] dark:placeholder:text-[var(--ink-3)]"
                />
                <button
                  type="button"
                  onClick={() => setShowPw(!showPw)}
                  tabIndex={-1}
                  aria-label={showPw ? t("auth.hidePw") : t("auth.showPw")}
                  className="absolute end-3 top-1/2 -translate-y-1/2 text-stone-400 hover:text-stone-600
                    dark:text-[var(--ink-3)] dark:hover:text-[var(--ink-2)] transition-colors"
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {/* Forgot password */}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleForgotPassword}
                className="text-[12.5px] font-medium text-[var(--brand-green)] hover:text-[var(--brand-green-2)] transition-colors"
              >
                {resetSent ? t("auth.resetSentShort") : t("auth.forgot")}
              </button>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={submitting}
              className="w-full h-11 rounded-xl font-medium text-[14px] text-white
                bg-[var(--brand-green)] hover:bg-[var(--brand-green-2)]
                disabled:opacity-50 disabled:cursor-not-allowed
                transition-all duration-150 flex items-center justify-center gap-2"
            >
              {submitting ? (
                <span className="h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
              ) : (
                <>
                  {t("auth.signIn")}
                  <ArrowRight className="h-4 w-4 rtl:rotate-180" />
                </>
              )}
            </button>
          </form>

          {/* Register link */}
          <p className="text-center text-[13px] text-stone-500 dark:text-[var(--ink-3)] mt-8">
            {t("auth.noAccount")}{" "}
            <Link
              to="/register"
              className="font-medium text-[var(--brand-navy)] hover:underline dark:text-[var(--brand-navy)]"
            >
              {t("auth.createAccount")}
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
