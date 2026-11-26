"use client";

import { Contact, ScanLine, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog, Sheet } from "@/components/ui/overlays";
import { Badge, Button } from "@/components/ui/primitives";
import { Api } from "@/lib/api";
import {
  applyScanResult,
  canAdd,
  clearFlag,
  countVCards,
  emptyAlternates,
  emptyForm,
  FIELD_KEYS,
  formFromClient,
  formsEqual,
  fromPickerContact,
  mergeScan,
  parseVCard,
  swapAlternate,
  toPayload,
  validateForm,
  type Alternates,
  type ClientForm,
  type FieldKey,
  type Flags,
  type ImportedContact,
  type PickerContact,
} from "@/lib/cardform";
import type { CardScanResult, Client, ClientSource } from "@/lib/types";
import { cn } from "@/lib/utils";

import { CardScanner } from "./CardScanner";

type ContactsApi = { select: (props: string[], opts?: { multiple?: boolean }) => Promise<PickerContact[]> };
const getContactsApi = (): ContactsApi | null => {
  if (typeof navigator === "undefined" || typeof window === "undefined") return null;
  if (!("contacts" in navigator) || !("ContactsManager" in window)) return null;
  return (navigator as unknown as { contacts: ContactsApi }).contacts;
};

type RowDef = { key: FieldKey; label: string; placeholder: string; type?: string; inputMode?: "text" | "email" | "tel" | "url"; autoComplete?: string; required?: boolean };

const PERSON_ROWS: RowDef[] = [
  { key: "name", label: "Name", placeholder: "Required", required: true, autoComplete: "off" },
  { key: "company", label: "Company", placeholder: "Optional", autoComplete: "off" },
  { key: "job_title", label: "Job title", placeholder: "Optional", autoComplete: "off" },
  { key: "website", label: "Website", placeholder: "example.com", inputMode: "url", autoComplete: "off" },
  { key: "email", label: "Email", placeholder: "name@company.com", type: "email", inputMode: "email", autoComplete: "off" },
  { key: "phone", label: "Phone", placeholder: "10-digit phone number", type: "tel", inputMode: "tel", autoComplete: "off" },
];
const ADDRESS_ROWS: RowDef[] = [
  { key: "street", label: "Street", placeholder: "Street", autoComplete: "off" },
  { key: "city", label: "City", placeholder: "City", autoComplete: "off" },
  { key: "state", label: "State", placeholder: "State", autoComplete: "off" },
  { key: "zip_code", label: "ZIP code", placeholder: "ZIP code", autoComplete: "off" },
  { key: "country", label: "Country", placeholder: "Optional", autoComplete: "off" },
];
const TAX_ROW: RowDef = { key: "tax_id", label: "Tax ID", placeholder: "Optional", autoComplete: "off" };

export function NewClientDialog({
  open,
  onOpenChange,
  client,
  onSaved,
  startWith = "form",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  client: Client | null;
  onSaved: (c: Client) => void;
  /** "scan" opens straight into the business-card scanner; "form" opens the manual New Client form. */
  startWith?: "form" | "scan";
}) {
  const editing = !!client;
  const [step, setStep] = React.useState<"form" | "scan">("form");
  const [form, setForm] = React.useState<ClientForm>(emptyForm());
  const [flags, setFlags] = React.useState<Flags>({});
  const [alts, setAlts] = React.useState<Alternates>(emptyAlternates());
  const [source, setSource] = React.useState<ClientSource>("manual");
  const [banner, setBanner] = React.useState<{ warnings: string[] } | null>(null);
  const [baseline, setBaseline] = React.useState<ClientForm | null>(null); // the form exactly as the scan filled it
  const [touched, setTouched] = React.useState<Set<FieldKey>>(new Set());
  const [saving, setSaving] = React.useState(false);
  const [confirmRescan, setConfirmRescan] = React.useState(false);
  const [pickerOk, setPickerOk] = React.useState(false);
  const freshScan = React.useRef(false);
  const vcfRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => setPickerOk(!!getContactsApi()), []);

  React.useEffect(() => {
    if (!open) return;
    setStep(!client && startWith === "scan" ? "scan" : "form");
    setForm(client ? formFromClient(client) : emptyForm());
    setFlags({});
    setAlts(emptyAlternates());
    setSource(client?.source ?? "manual");
    setBanner(null);
    setBaseline(null);
    setTouched(new Set());
    setSaving(false);
    setConfirmRescan(false);
    freshScan.current = false;
  }, [open, client, startWith]);

  const errors = validateForm(form);
  const ready = canAdd(form);

  const setField = (key: FieldKey, value: string) => {
    setForm((f) => ({ ...f, [key]: value }));
    setFlags((fl) => clearFlag(fl, key));
  };

  // --- contacts -----------------------------------------------------------------
  const applyImported = (imp: ImportedContact) => {
    if (!imp.form.name) {
      toast.error("That contact has no name. Pick another, or enter the details by hand.");
      return;
    }
    setForm((f) => ({ ...f, ...imp.form }));
    setFlags({});
    setAlts({ email: imp.otherEmails, phone: imp.otherPhones });
    setBanner(null);
    setBaseline(null);
    if (!editing) setSource("contacts");
    toast.success("Contact imported — check the details, then add.");
  };

  const chooseFromContacts = async () => {
    const api = getContactsApi();
    if (!api) {
      vcfRef.current?.click();
      return;
    }
    try {
      const picked = await api.select(["name", "email", "tel", "address"], { multiple: false });
      if (picked?.[0]) applyImported(fromPickerContact(picked[0]));
    } catch (e) {
      if ((e as { name?: string })?.name === "AbortError") return; // user dismissed the picker
      toast.error("The contact picker could not be opened. You can import a .vcf file instead.");
      vcfRef.current?.click();
    }
  };

  const onVcf = async (file: File | undefined) => {
    if (!file) return;
    try {
      const text = await file.text();
      const n = countVCards(text);
      if (n === 0) {
        toast.error("That file doesn't contain a contact (expected a .vcf vCard).");
        return;
      }
      applyImported(parseVCard(text));
      if (n > 1) toast.message(`The file has ${n} contacts — imported the first one.`);
    } catch {
      toast.error("That file could not be read.");
    }
  };

  // --- business card ------------------------------------------------------------
  const onScanned = (result: CardScanResult) => {
    const applied = applyScanResult(result);
    const merged = mergeScan(freshScan.current ? emptyForm() : form, applied);
    freshScan.current = false;
    setForm(merged.form);
    setFlags(merged.flags);
    setAlts(merged.alternates);
    setBaseline(merged.form);
    setBanner({ warnings: result.warnings ?? [] });
    if (!editing) setSource("business_card");
    setTouched(new Set());
    setStep("form");
    if (!merged.form.name.trim()) toast.message("No name was found on the card — type one to enable Add.");
  };

  const rescan = () => {
    freshScan.current = true;
    setStep("scan");
  };
  const askRescan = () => {
    if (baseline && !formsEqual(form, baseline)) setConfirmRescan(true);
    else rescan();
  };

  // --- save ---------------------------------------------------------------------
  const submit = async () => {
    const errs = validateForm(form);
    if (Object.keys(errs).length) {
      setTouched(new Set(FIELD_KEYS));
      toast.error(Object.values(errs)[0] as string);
      return;
    }
    setSaving(true);
    try {
      const saved = client ? await Api.updateClient(client.id, toPayload(form)) : await Api.createClient(toPayload(form, source) as Parameters<typeof Api.createClient>[0]);
      toast.success(client ? "Client updated" : "Client added");
      onSaved(saved);
      onOpenChange(false);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const row = (def: RowDef) => (
    <FieldRow
      key={def.key}
      def={def}
      value={form[def.key]}
      flag={flags[def.key]}
      error={touched.has(def.key) && def.key !== "name" ? errors[def.key] : undefined}
      alternates={def.key === "email" ? alts.email : def.key === "phone" ? alts.phone : undefined}
      onChange={(v) => setField(def.key, v)}
      onBlur={() => setTouched((t) => new Set(t).add(def.key))}
      onPickAlternate={(v) => {
        const k = def.key === "email" ? "email" : "phone";
        const r = swapAlternate(form[k], alts[k], v);
        setField(k, r.value);
        setAlts((a) => ({ ...a, [k]: r.alternates }));
      }}
    />
  );

  const scanning = step === "scan";
  // Opened straight into the scanner and nothing scanned yet: backing out closes the sheet instead of
  // dropping the user into a blank form they did not ask for.
  const leaveScanner = () => (startWith === "scan" && !client && !baseline ? onOpenChange(false) : setStep("form"));
  return (
    <>
      <Sheet
        open={open}
        onOpenChange={onOpenChange}
        title={scanning ? "Scan Business Card" : editing ? "Edit Client" : "New Client"}
        hideClose
        headerLeft={
          scanning ? (
            <Button variant="outline" size="sm" className="rounded-full" onClick={leaveScanner}>
              Back
            </Button>
          ) : (
            <Button variant="outline" size="sm" className="rounded-full text-accent" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
          )
        }
        headerRight={
          scanning ? (
            <span className="inline-block w-14" />
          ) : (
            <Button variant="outline" size="sm" className="rounded-full text-accent" disabled={!ready} loading={saving} onClick={submit}>
              {editing ? "Save" : "Add"}
            </Button>
          )
        }
        footer={
          scanning ? undefined : (
            <Button variant="primary" size="lg" className="w-full" disabled={!ready} loading={saving} onClick={submit}>
              {ready ? (editing ? "Save Changes" : "Add Client") : "Enter a Name to Add"}
            </Button>
          )
        }
      >
        {scanning ? (
          <CardScanner onResult={onScanned} onCancel={leaveScanner} />
        ) : (
          <div className="space-y-4 bg-surface-2/60 px-4 py-4">
            {banner ? (
              <div className="flex items-start gap-3 rounded-2xl bg-accent-soft px-4 py-3 text-[13.5px] text-accent" role="status">
                <ScanLine className="mt-0.5 size-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">Scanned from business card — please review before adding.</div>
                  {banner.warnings.length ? (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[12.5px] opacity-90">
                      {banner.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  ) : null}
                  <button type="button" onClick={askRescan} className="mt-1.5 text-[13px] font-medium underline underline-offset-4">
                    Rescan
                  </button>
                </div>
                <button type="button" onClick={() => setBanner(null)} className="grid size-6 shrink-0 place-items-center rounded-full hover:bg-accent/10" aria-label="Dismiss">
                  <X className="size-3.5" />
                </button>
              </div>
            ) : null}

            {!editing ? (
              <div className="space-y-1.5">
                <ActionRow icon={<Contact className="size-5" />} onClick={chooseFromContacts}>
                  Choose from Contacts
                </ActionRow>
                <p className="px-2 text-[12.5px] leading-snug text-muted">
                  {pickerOk
                    ? "Opens your device's contact picker. ScanWise only receives the contact you choose."
                    : "Your browser has no contact picker, so you can import a contact file (.vcf) exported from your phone or address book."}
                </p>
                <ActionRow icon={<ScanLine className="size-5" />} onClick={() => setStep("scan")}>
                  Scan Business Card
                </ActionRow>
              </div>
            ) : null}

            <div className="overflow-hidden rounded-2xl bg-surface shadow-card">{PERSON_ROWS.map(row)}</div>

            <div>
              <div className="px-3 pb-1.5 text-[12.5px] font-medium uppercase tracking-wide text-muted">Billing address</div>
              <div className="overflow-hidden rounded-2xl bg-surface shadow-card">{ADDRESS_ROWS.map(row)}</div>
            </div>

            <div>
              <div className="overflow-hidden rounded-2xl bg-surface shadow-card">{row(TAX_ROW)}</div>
              <p className="px-3 pt-1.5 text-[12.5px] leading-snug text-muted">Shown on invoices when your client needs it for their records.</p>
            </div>
          </div>
        )}
        <input ref={vcfRef} type="file" accept=".vcf,text/vcard,text/x-vcard" className="hidden" onChange={(e) => { void onVcf(e.target.files?.[0]); e.target.value = ""; }} />
      </Sheet>

      <ConfirmDialog
        open={confirmRescan}
        onOpenChange={setConfirmRescan}
        title="Rescan the card?"
        description="The values you edited will be replaced by the new scan."
        confirmLabel="Rescan"
        tone="primary"
        onConfirm={() => {
          setConfirmRescan(false);
          rescan();
        }}
      />
    </>
  );
}

function ActionRow({ icon, children, onClick }: { icon: React.ReactNode; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-2xl bg-surface px-4 py-3.5 text-left text-[16px] font-medium text-accent shadow-card transition hover:bg-accent-soft active:scale-[0.99]"
    >
      {icon}
      {children}
    </button>
  );
}

function FieldRow({
  def,
  value,
  flag,
  error,
  alternates,
  onChange,
  onBlur,
  onPickAlternate,
}: {
  def: RowDef;
  value: string;
  flag?: "uncertain" | "missing";
  error?: string;
  alternates?: string[];
  onChange: (v: string) => void;
  onBlur: () => void;
  onPickAlternate: (v: string) => void;
}) {
  const id = React.useId();
  const uncertain = flag === "uncertain";
  const notFound = flag === "missing" && !def.required && !value.trim();
  return (
    <div className={cn("border-b border-border px-4 last:border-b-0", uncertain && "bg-warn-soft/60 ring-1 ring-inset ring-warn/50")}>
      <div className="flex min-h-12 items-center gap-3">
        <label htmlFor={id} className="w-[88px] shrink-0 text-[15px] text-fg">
          {def.label}
        </label>
        <input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          type={def.type ?? "text"}
          inputMode={def.inputMode}
          autoComplete={def.autoComplete}
          placeholder={def.placeholder}
          aria-invalid={error ? true : undefined}
          className="h-11 min-w-0 flex-1 bg-transparent text-[16px] text-fg outline-none placeholder:text-faint"
        />
        {uncertain ? <Badge tone="warn">Check</Badge> : null}
        {notFound ? <span className="shrink-0 text-[12px] text-faint">Not found</span> : null}
      </div>
      {alternates && alternates.length ? (
        <div className="flex flex-wrap items-center gap-1.5 pb-2.5 pl-[100px]">
          <span className="text-[12px] text-muted">Also on card:</span>
          {alternates.map((a) => (
            <button key={a} type="button" onClick={() => onPickAlternate(a)} className="max-w-full truncate rounded-full border border-border-strong bg-surface px-2.5 py-0.5 text-[12.5px] text-fg hover:border-accent/60" title={`Use ${a}`}>
              {a}
            </button>
          ))}
        </div>
      ) : null}
      {error ? (
        <p className="pb-2 pl-[100px] text-[12.5px] text-bad" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
