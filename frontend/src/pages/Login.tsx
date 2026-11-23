import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import Field from "xedonium/Field";
import PasswordInput from "xedonium/PasswordInput";
import ThemeToggle from "xedonium/ThemeToggle";
import { useAuthStatus, useLogin, useMe } from "@/api/auth";
import { FullPageSpinner } from "@/components/Spinner";

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
      <Card
        className="w-full max-w-sm"
        title="Sign in"
        headingLevel={2}
        subtitle={state.notice ?? "Sign in to DBBackup."}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field
            label="Username"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={setUsername}
          />
          <PasswordInput
            label="Password"
            autoComplete="current-password"
            required
            value={password}
            onChange={setPassword}
          />
          {login.error && <Alert tone="danger">{login.error.message}</Alert>}
          <Button type="submit" className="w-full" disabled={login.isPending}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
