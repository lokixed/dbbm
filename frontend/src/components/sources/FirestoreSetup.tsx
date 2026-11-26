import { AccordionSection } from "xedonium/Accordion";
import { ExtLink, SetupStep, Troubleshooting } from "@/components/SetupSteps";

// Google Cloud Console pages open in the project currently picked in the console.
const LINKS = {
  dashboard: "https://console.cloud.google.com/home/dashboard",
  databases: "https://console.cloud.google.com/firestore/databases",
  billing: "https://console.cloud.google.com/billing/linkedaccount",
  firestoreApi: "https://console.cloud.google.com/apis/library/firestore.googleapis.com",
  createBucket: "https://console.cloud.google.com/storage/create-bucket",
  buckets: "https://console.cloud.google.com/storage/browser",
  locations: "https://cloud.google.com/firestore/docs/manage-data/export-import#before_you_begin",
  lifecycle: "https://cloud.google.com/storage/docs/lifecycle",
  createServiceAccount: "https://console.cloud.google.com/iam-admin/serviceaccounts/create",
  serviceAccounts: "https://console.cloud.google.com/iam-admin/serviceaccounts",
  iam: "https://console.cloud.google.com/iam-admin/iam",
  pricing: "https://cloud.google.com/firestore/pricing",
};

/** Step-by-step Google Cloud setup for a Firestore source (managed export to a staging bucket). */
export function FirestoreSetup({ open }: { open: boolean }) {
  return (
    <AccordionSection title="Set up Google Cloud for Firestore backups" defaultOpen={open}>
      <div className="space-y-4 text-sm text-app-muted">
        <p>
          DBBM backs up Firestore with a managed export: Google writes the export to a Cloud Storage bucket (the
          staging bucket), and DBBM downloads it from there. This one-time setup takes about 10 minutes in the{" "}
          <ExtLink href="https://console.cloud.google.com/">Google Cloud Console</ExtLink>, in the project that
          holds the database.
        </p>
        <ol className="list-decimal space-y-4 pl-5">
          <SetupStep title="Find the project and database">
            <p>
              The <strong>Project ID</strong> is on the project <ExtLink href={LINKS.dashboard}>dashboard</ExtLink>{" "}
              (for a Firebase project it is the same as the Firebase project ID).
            </p>
            <p>
              The <strong>Database ID</strong> and its <strong>location</strong> are listed under{" "}
              <ExtLink href={LINKS.databases}>Firestore → Databases</ExtLink>. The main database is called{" "}
              <code>(default)</code>.
            </p>
          </SetupStep>
          <SetupStep title="Turn on billing">
            <p>
              Managed exports need a billing account linked to the project (the Blaze plan in Firebase). Check it on
              the <ExtLink href={LINKS.billing}>Billing</ExtLink> page. An export is charged as one document read
              per exported document; see <ExtLink href={LINKS.pricing}>Firestore pricing</ExtLink>.
            </p>
          </SetupStep>
          <SetupStep title="Enable the Cloud Firestore API">
            <p>
              Open the <ExtLink href={LINKS.firestoreApi}>Cloud Firestore API</ExtLink> page and click{" "}
              <strong>Enable</strong> (it is usually on already). The Cloud Storage API is on by default.
            </p>
          </SetupStep>
          <SetupStep title="Create the staging bucket">
            <p>
              <ExtLink href={LINKS.createBucket}>Create a bucket</ExtLink>, for example{" "}
              <code>my-project-firestore-staging</code>, in the <strong>same location as the database</strong>:
              the same region for a regional database (such as <code>us-central1</code>), or the matching
              multi-region for a multi-region one (<code>nam5</code> → <code>US</code>, <code>eur3</code> →{" "}
              <code>EU</code>). See <ExtLink href={LINKS.locations}>export location rules</ExtLink>.
            </p>
            <p>
              Keep the defaults otherwise: Standard storage class, uniform access control, and public access
              prevention on.
            </p>
          </SetupStep>
          <SetupStep title="Add a lifecycle rule">
            <p>
              In the bucket&apos;s <strong>Lifecycle</strong> tab click <strong>Add a rule</strong>, choose{" "}
              <strong>Delete object</strong> and the condition <strong>Age</strong> of 1 to 7 days. Exports of
              failed or cancelled runs are then removed and do not keep costing storage.{" "}
              <ExtLink href={LINKS.lifecycle}>About lifecycle rules</ExtLink>.
            </p>
          </SetupStep>
          <SetupStep title="Create a service account">
            <p>
              <ExtLink href={LINKS.createServiceAccount}>Create a service account</ExtLink>, for example{" "}
              <code>dbbm-backup</code>. When asked to grant access to the project, add the role{" "}
              <strong>Cloud Datastore Import Export Admin</strong> (<code>roles/datastore.importExportAdmin</code>
              ). You can also add it later on the <ExtLink href={LINKS.iam}>IAM</ExtLink> page.
            </p>
            <p>
              Then open the staging bucket in <ExtLink href={LINKS.buckets}>Cloud Storage</ExtLink>, go to{" "}
              <strong>Permissions → Grant access</strong>, enter the service account&apos;s email and give it{" "}
              <strong>Storage Object Admin</strong> (<code>roles/storage.objectAdmin</code>) on that bucket only.
              DBBM needs it to read the export and, with the switch above on, delete it.
            </p>
          </SetupStep>
          <SetupStep title="Create a JSON key">
            <p>
              In <ExtLink href={LINKS.serviceAccounts}>Service accounts</ExtLink> open the account, go to{" "}
              <strong>Keys → Add key → Create new key</strong>, choose <strong>JSON</strong> and click{" "}
              <strong>Create</strong>. The key file downloads once. Paste it or upload it into{" "}
              <strong>Service account JSON</strong> below. DBBM encrypts it and only uses it to get short-lived
              access tokens. Delete the downloaded file afterwards.
            </p>
          </SetupStep>
          <SetupStep title="Fill in the form and test">
            <p>
              Enter the project ID, the database ID and the bucket as <code>gs://bucket-name</code>, then click{" "}
              <strong>Test connection</strong>. It checks that the database and the bucket are reachable. Exports of
              large databases take a while, so raise the job&apos;s timeout per step if needed.
            </p>
            <p>
              Restoring into this source needs the same two roles, so the same service account works for restores.
            </p>
          </SetupStep>
        </ol>

        <Troubleshooting>
          <li>
            <strong>PERMISSION_DENIED</strong> on the database: the service account lacks{" "}
            <code>roles/datastore.importExportAdmin</code>, or the project ID is wrong. Role changes can take a few
            minutes to apply.
          </li>
          <li>
            <strong>PERMISSION_DENIED</strong> on the bucket: grant <strong>Storage Object Admin</strong> on the
            bucket. If the bucket is in another project, also grant it to the Firestore service agent (
            <code>service-PROJECT_NUMBER@gcp-sa-firestore.iam.gserviceaccount.com</code>).
          </li>
          <li>
            <strong>FAILED_PRECONDITION</strong> or a billing error: link a billing account to the project.
          </li>
          <li>
            <strong>Location</strong> error on export: the bucket is not in the database&apos;s location. Create a new
            bucket in the right location (a bucket&apos;s location cannot be changed).
          </li>
          <li>
            <strong>Key creation is disabled</strong>: an organization policy (
            <code>iam.disableServiceAccountKeyCreation</code>) blocks JSON keys. Ask your Google Cloud admin for an
            exception for this project.
          </li>
        </Troubleshooting>
      </div>
    </AccordionSection>
  );
}
