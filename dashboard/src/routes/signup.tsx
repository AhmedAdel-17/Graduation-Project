import { createFileRoute, redirect } from "@tanstack/react-router";
import { useAuthStore } from "@/lib/store";
import { AuthShell } from "@/components/auth/AuthShell";
import { SignupForm } from "@/components/auth/SignupForm";

export const Route = createFileRoute("/signup")({
  ssr: false,
  beforeLoad: () => {
    if (typeof window !== "undefined" && useAuthStore.getState().token) {
      throw redirect({ to: "/" });
    }
  },
  component: SignupPage,
});

function SignupPage() {
  return (
    <AuthShell mode="signup">
      <SignupForm />
    </AuthShell>
  );
}
