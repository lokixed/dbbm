import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuthStatus, useLogin, useMe } from "@/api/auth";
import { FullPageSpinner } from "@/components/Spinner";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface LocationState {
  from?: string;
  notice?: string;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const state = (useLocation().state ?? {}) as LocationState;
  const status = useAuthStatus();
  const me = useMe();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  if (status.isPending || me.isPending) return <FullPageSpinner />;
  if (status.data?.setup_required) return <Navigate to="/setup" replace />;
  if (me.data) return <Navigate to="/" replace />;

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(
      { username, password },
      { onSuccess: () => navigate(state.from ?? "/", { replace: true }) },
    );
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center p-4">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>{state.notice ?? "Sign in to DBBackup."}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                autoComplete="username"
                autoFocus
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {login.error && (
              <p role="alert" className="text-sm text-destructive">
                {login.error.message}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={login.isPending}>
              {login.isPending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
