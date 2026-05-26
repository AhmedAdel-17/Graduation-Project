import { useState } from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate, Link } from "@tanstack/react-router";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/store";
import { SECTORS } from "@/lib/constants";
import type { Sector } from "@/types/api";
import { cn } from "@/lib/utils";

const schema = z
  .object({
    name: z.string().trim().min(1, "Name is required").max(100),
    email: z.string().email("Enter a valid email"),
    password: z
      .string()
      .min(8, "At least 8 characters")
      .regex(/\d/, "Must contain a number")
      .regex(/[!@#$%^&*(),.?":{}|<>_\-+=/\\[\]`~]/, "Must contain a special character"),
    confirm: z.string(),
    alerts_enabled: z.boolean(),
    sectors_of_interest: z.array(z.string()).min(1, "Pick at least one sector"),
  })
  .refine((d) => d.password === d.confirm, {
    message: "Passwords do not match",
    path: ["confirm"],
  });
type Form = z.infer<typeof schema>;

export function SignupForm() {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [submitting, setSubmitting] = useState(false);

  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
  } = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: { sectors_of_interest: [], alerts_enabled: true },
  });

  const onSubmit = async (data: Form) => {
    setSubmitting(true);
    try {
      const { token, user } = await api.auth.signup({
        name: data.name,
        email: data.email,
        password: data.password,
        sectors_of_interest: data.sectors_of_interest as Sector[],
        alerts_enabled: data.alerts_enabled,
      });
      setAuth(token, user);
      toast.success("Account created. Welcome to TradingAgents.");
      navigate({ to: "/" });
    } catch (e: any) {
      toast.error(e?.message || "Sign up failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Create your account</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Track EGX with multi-agent AI research.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="name">Full name</Label>
        <Input id="name" {...register("name")} />
        {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="email">Email</Label>
        <Input id="email" type="email" autoComplete="email" {...register("email")} />
        {errors.email && <p className="text-xs text-destructive">{errors.email.message}</p>}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" autoComplete="new-password" {...register("password")} />
          {errors.password && <p className="text-xs text-destructive">{errors.password.message}</p>}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm">Confirm</Label>
          <Input id="confirm" type="password" autoComplete="new-password" {...register("confirm")} />
          {errors.confirm && <p className="text-xs text-destructive">{errors.confirm.message}</p>}
        </div>
      </div>

      <div className="space-y-2">
        <Label>Sectors of interest</Label>
        <Controller
          control={control}
          name="sectors_of_interest"
          render={({ field }) => (
            <div className="flex flex-wrap gap-2">
              {SECTORS.map((s) => {
                const active = field.value.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() =>
                      field.onChange(active ? field.value.filter((x) => x !== s) : [...field.value, s])
                    }
                    className={cn(
                      "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                      active
                        ? "border-primary bg-primary/15 text-primary"
                        : "border-border bg-secondary/40 text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {s}
                  </button>
                );
              })}
            </div>
          )}
        />
        {errors.sectors_of_interest && (
          <p className="text-xs text-destructive">{errors.sectors_of_interest.message as string}</p>
        )}
      </div>

      <Controller
        control={control}
        name="alerts_enabled"
        render={({ field }) => (
          <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
            <Checkbox checked={field.value} onCheckedChange={(v) => field.onChange(!!v)} />
            I want EGX trading alerts
          </label>
        )}
      />

      <Button type="submit" className="w-full" disabled={submitting}>
        {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        Create account
      </Button>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link to="/login" className="text-primary hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
