import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import Field from "xedonium/Field";
import PasswordInput from "xedonium/PasswordInput";
import ThemeToggle from "xedonium/ThemeToggle";
import { useAuthStatus, useSetup } from "@/api/auth";
import { FullPageSpinner } from "@/components/Spinner";
import { AuthBackdrop } from "@/components/AuthBackdrop";
import { AuthHeader } from "@/components/AuthHeader";

const MIN_PASSWORD_LENGTH = 12;

export default function SetupPage() {
  const navigate = useNavigate();
  const status = useAuthStatus();
  const setup = useSetup();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  if (status.isPending) return <FullPageSpinner />;
  if (status.data && !status.data.setup_required && !setup.isSuccess) {
    return <Navigate to="/login" replace />;
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setLocalError(null);
    if (password.length < MIN_PASSWORD_LENGTH) {
      setLocalError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirm) {
      setLocalError("Passwords do not match.");
      return;
    }
    setup.mutate(
      { username, password },
      {
        onSuccess: () =>
          navigate("/login", {
            replace: true,
            state: { notice: "Admin account created. Sign in to continue." },
          }),
      },
    );
  };

  const error = localError ?? setup.error?.message;

  return (
    <main className="relative isolate flex min-h-screen items-center justify-center overflow-hidden bg-app-bg p-4">
      <AuthBackdrop />
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <Card
        className="w-full max-w-sm"
      >
        <AuthHeader title="Welcome to DBBM" subtitle={"Create the administrator account to get started."} />
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field
            label="Username"
            autoComplete="username"
            autoFocus
            required
            minLength={3}
            value={username}
            onChange={setUsername}
          />
          <PasswordInput
            label="Password"
            autoComplete="new-password"
            required
            value={password}
            onChange={setPassword}
            helperText={`At least ${MIN_PASSWORD_LENGTH} characters.`}
          />
          <PasswordInput
            label="Confirm password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={setConfirm}
          />
          {error && <Alert tone="danger">{error}</Alert>}
          <Button type="submit" className="w-full" disabled={setup.isPending}>
            {setup.isPending ? "Creating…" : "Create admin"}
          </Button>
        </form>
      </Card>
    </main>
  );
}
