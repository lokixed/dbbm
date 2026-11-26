import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AccordionSection } from "xedonium/Accordion";
import Alert from "xedonium/Alert";
import Button from "xedonium/Button";
import Field from "xedonium/Field";
import { useChangePassword } from "@/api/auth";
import { useToast } from "@/components/toast";

const MIN_LENGTH = 12;

/** Change the signed-in user's password. Other sessions are signed out. */
export function ChangePassword() {
  const { toast } = useToast();
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Open while the URL ends in #change-password (the sidebar's username links there) or after the
  // user opens it; closing it also drops the hash, so the link opens it again next time.
  const { hash } = useLocation();
  const navigate = useNavigate();
  const linked = hash === "#change-password";
  const [userOpen, setUserOpen] = useState(false);
  const open = linked || userOpen;
  const setOpen = (next: boolean) => {
    setUserOpen(next);
    if (!next && linked) navigate({ hash: "" }, { replace: true });
  };
  const anchor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (linked) anchor.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [linked]);

  const tooShort = next !== "" && next.length < MIN_LENGTH;
  const mismatch = repeat !== "" && repeat !== next;
  const canSave = current !== "" && next.length >= MIN_LENGTH && repeat === next && !change.isPending;

  const submit = () => {
    setError(null);
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          toast({ title: "Password changed", description: "Other sessions were signed out.", variant: "success" });
          setCurrent("");
          setNext("");
          setRepeat("");
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  return (
    // Collapsed by default: it is rarely needed and the form sat open at the bottom of Settings.
    // The username in the sidebar links to #change-password, which opens it and scrolls to it.
    <div id="change-password" ref={anchor}>
    <AccordionSection
      title="Change password"
      headingLevel={2}
      className="bg-app-card"
      open={open}
      onChange={setOpen}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSave) submit();
        }}
      >
        <Field label="Current password" type="password" autoComplete="current-password" value={current} onChange={setCurrent} />
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={setNext}
          error={tooShort ? `Use at least ${MIN_LENGTH} characters.` : undefined}
        />
        <Field
          label="Repeat new password"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={setRepeat}
          error={mismatch ? "The passwords do not match." : undefined}
        />
        {error && <Alert tone="danger">{error}</Alert>}
        <div className="flex justify-end">
          <Button type="submit" disabled={!canSave}>
            Change password
          </Button>
        </div>
      </form>
    </AccordionSection>
    </div>
  );
}
