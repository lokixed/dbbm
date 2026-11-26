import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Alert from "xedonium/Alert";
import Card from "xedonium/Card";
import ThemeToggle from "xedonium/ThemeToggle";
import { AuthBackdrop } from "@/components/AuthBackdrop";
import { AuthHeader } from "@/components/AuthHeader";
import { CONNECT_CHANNEL, connectErrorMessage, type ConnectMessage } from "@/lib/gdriveConnect";

/**
 * Where "Connect Google Drive" ends, in the tab Google sign-in ran in. It tells the form tab the
 * result over a BroadcastChannel (the form also polls) and asks the user to close this tab.
 * The token itself stays on the server.
 */
export default function GdriveConnectDonePage() {
  const [params] = useSearchParams();
  const flow = params.get("flow");
  const error = params.get("error");

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(CONNECT_CHANNEL);
    const message: ConnectMessage = { flow, error };
    channel.postMessage(message);
    channel.close();
    if (!error) {
      // Only closes a tab the app opened; otherwise the message below stays.
      const timer = window.setTimeout(() => window.close(), 1200);
      return () => window.clearTimeout(timer);
    }
  }, [flow, error]);

  return (
    <main className="relative isolate flex min-h-screen items-center justify-center overflow-hidden bg-app-bg p-4">
      <AuthBackdrop />
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-md">
        <AuthHeader
          title={error ? "Google Drive not connected" : "Google Drive connected"}
          subtitle={error ? "Close this tab and try again from the destination form." : "Close this tab and save the destination."}
        />
        {error ? (
          <Alert tone="danger">{connectErrorMessage(error)}</Alert>
        ) : (
          <Alert tone="info">The destination form in your other tab is ready to save.</Alert>
        )}
        <p className="mt-4 text-sm text-app-muted">
          No other tab? <Link to="/destinations" className="underline">Go to Destinations</Link> and connect again.
        </p>
      </Card>
    </main>
  );
}
