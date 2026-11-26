import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import Button from "xedonium/Button";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import ArrowLeftIcon from "xedonium/icons/ArrowLeft";
import PageHeader from "xedonium/PageHeader";

interface EditorPageProps {
  title: string;
  subtitle: string;
  /** Where the back button goes, and its label. */
  backTo: string;
  backLabel: string;
  /** A short note above the form. */
  note?: string;
  children: ReactNode;
}

/** The frame of an add/edit page (sources, destinations): header with a back button, form in a card. */
export function EditorPage({ title, subtitle, backTo, backLabel, note, children }: EditorPageProps) {
  const navigate = useNavigate();
  return (
    <div className="space-y-6">
      <PageHeader title={title} subtitle={subtitle}>
        <Button variant="secondary" onClick={() => navigate(backTo)}>
          <ArrowLeftIcon className="h-4 w-4" /> {backLabel}
        </Button>
      </PageHeader>
      <Card className="max-w-3xl">
        {note && <HelperText className="mb-4">{note}</HelperText>}
        {children}
      </Card>
    </div>
  );
}
