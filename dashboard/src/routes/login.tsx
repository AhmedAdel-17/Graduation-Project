import { createFileRoute, redirect } from "@tanstack/react-router";
import { useAuthStore } from "@/lib/store";
import { AuthShell } from "@/components/auth/AuthShell";
import { LoginForm } from "@/components/auth/LoginForm";

export const Route = createFileRoute("/login")({
  ssr: false,
  beforeLoad: () => {
    if (typeof window !== "undefined" && useAuthStore.getState().token) {
      throw redirect({ to: "/" });
    }
  },
  component: LoginPage,
});

function LoginPage() {
  return (
    <AuthShell mode="login">
      <LoginForm />
    </AuthShell>
  );
}
