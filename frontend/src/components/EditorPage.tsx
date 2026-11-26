import type { ReactNode } from "react";
import Card from "xedonium/Card";
import HelperText from "xedonium/HelperText";
import PageHeader from "xedonium/PageHeader";
import { BackLink } from "@/components/BackLink";

interface EditorPageProps {
  title: string;
  subtitle: string;
  /** Where the back link above the title goes, and its label, e.g. "All jobs". */
  backTo: string;
  backLabel: string;
  /** A short note above the form. */
  note?: string;
  children: ReactNode;
  /** Page actions on the right of the header, e.g. "View runs". */
  actions?: ReactNode;
}

/** The frame of an add/edit page: back link above the title, header with page actions, form in a card. */
export function EditorPage({ title, subtitle, backTo, backLabel, note, children, actions }: EditorPageProps) {
  return (
    <div className="space-y-6">
      <BackLink to={backTo} label={backLabel} />
      <PageHeader title={title} subtitle={subtitle}>
        {actions}
      </PageHeader>
      <Card className="max-w-3xl">
        {note && <HelperText className="mb-4">{note}</HelperText>}
        {children}
      </Card>
    </div>
  );
}
