import type { ReactNode } from "react";
import { AccordionSection } from "xedonium/Accordion";
import { CopyText } from "@/components/CopyText";

// Google Cloud Console pages (Google Auth Platform). They open in the project picked in the console.
const LINKS = {
  newProject: "https://console.cloud.google.com/projectcreate",
  driveApi: "https://console.cloud.google.com/apis/library/drive.googleapis.com",
  branding: "https://console.cloud.google.com/auth/branding",
  audience: "https://console.cloud.google.com/auth/audience",
  clients: "https://console.cloud.google.com/auth/clients",
  connections: "https://myaccount.google.com/connections",
  unverified: "https://support.google.com/cloud/answer/7454865",
};

function Ext({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a className="text-app-text underline" href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

function Step({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <li className="space-y-1">
      <div className="font-medium text-app-text">{title}</div>
      <div className="space-y-1">{children}</div>
    </li>
  );
}

/** Step-by-step setup of the user's own OAuth client for "Connect Google Drive". */
export function GdriveClientSetup({ redirectUrl, open }: { redirectUrl: string | null; open: boolean }) {
  return (
    <AccordionSection title="Set up a Google OAuth client" defaultOpen={open}>
      <div className="space-y-4 text-sm text-app-muted">
        <p>
          One-time setup, about 5 minutes. DBBM uses your own OAuth client, so the client secret stays with you
          and your quota is not shared with anyone. Sign in to the{" "}
          <Ext href="https://console.cloud.google.com/">Google Cloud Console</Ext> with any Google account (it
          does not have to be the account that owns the Drive).
        </p>
        <ol className="list-decimal space-y-4 pl-5">
          <Step title="Create or pick a project">
            <p>
              <Ext href={LINKS.newProject}>Create a project</Ext> (for example &quot;dbbm-backups&quot;), or pick an
              existing one in the project selector at the top of the console. No billing account is needed.
            </p>
          </Step>
          <Step title="Enable the Google Drive API">
            <p>
              Open the <Ext href={LINKS.driveApi}>Google Drive API</Ext> page and click <strong>Enable</strong>.
            </p>
          </Step>
          <Step title="Configure the consent screen">
            <p>
              Open <Ext href={LINKS.branding}>Google Auth Platform → Branding</Ext>. If asked, click{" "}
              <strong>Get started</strong>. Enter an app name (for example &quot;DBBM&quot;) and your email as the
              support and developer contact, then save.
            </p>
            <p>
              Choose <strong>External</strong> as the audience (<strong>Internal</strong> only works for accounts of
              a Google Workspace organization).
            </p>
          </Step>
          <Step title="Publish the app">
            <p>
              Open <Ext href={LINKS.audience}>Audience</Ext> and click <strong>Publish app</strong>, so the status
              reads <strong>In production</strong>. Apps left in <strong>Testing</strong> get refresh tokens that
              expire after 7 days, and backups then fail with &quot;authorization expired&quot;.
            </p>
            <p>
              Google does not need to verify the app for personal use. When you sign in, Google shows{" "}
              <em>&quot;Google hasn&apos;t verified this app&quot;</em>: click <strong>Advanced</strong>, then{" "}
              <strong>Go to DBBM (unsafe)</strong>. <Ext href={LINKS.unverified}>Why Google shows this</Ext>.
            </p>
          </Step>
          <Step title="Create the OAuth client">
            <p>
              Open <Ext href={LINKS.clients}>Clients</Ext>, click <strong>Create client</strong> and choose the
              application type <strong>Web application</strong>. Name it, for example &quot;DBBM&quot;.
            </p>
            <p>
              Under <strong>Authorized redirect URIs</strong> click <strong>Add URI</strong> and paste exactly (with
              the trailing slash):
            </p>
            <p>
              {redirectUrl ? (
                <CopyText value={redirectUrl} head={80} label="Copy redirect URI" />
              ) : (
                <span>Loading…</span>
              )}
            </p>
            <p>
              Leave <strong>Authorized JavaScript origins</strong> empty. This page only sends your browser back to
              this DBBM; sign-in itself happens at Google, and your DBBM does not need to be reachable from the
              internet.
            </p>
            <p>
              Click <strong>Create</strong>, then copy the <strong>Client ID</strong> and the{" "}
              <strong>Client secret</strong> right away (or download the JSON). Google may not show the secret
              again.
            </p>
          </Step>
          <Step title="Connect">
            <p>
              Paste the client ID and client secret into the fields above, pick the access scope, and click{" "}
              <strong>Connect Google Drive</strong>. Sign in with the account whose Drive should hold the backups and
              allow access. The tab then returns to DBBM; come back here and click <strong>Save</strong>.
            </p>
            <p>Changes in the console can take a few minutes to apply.</p>
          </Step>
        </ol>

        <div className="space-y-1">
          <div className="font-medium text-app-text">If it does not work</div>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              <strong>redirect_uri_mismatch</strong>: the redirect URI in the client must match the one above
              exactly, including <code>https</code> and the trailing slash. Check that you edited the client whose ID
              is in the form.
            </li>
            <li>
              <strong>Access blocked</strong> or <strong>403 access_denied</strong>: the app is still in Testing and
              your account is not a test user. Publish the app (step 4).
            </li>
            <li>
              <strong>No refresh token</strong>: remove DBBM under{" "}
              <Ext href={LINKS.connections}>your Google account&apos;s third-party connections</Ext>, then connect
              again.
            </li>
            <li>
              <strong>The new tab was blocked</strong>: allow pop-ups for this DBBM in your browser, then click Connect
              again.
            </li>
          </ul>
        </div>
      </div>
    </AccordionSection>
  );
}
