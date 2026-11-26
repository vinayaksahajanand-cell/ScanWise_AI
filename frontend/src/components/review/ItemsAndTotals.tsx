"use client";

import { BadgeCheck, BookOpen, Calculator, Ellipsis, Plus, Trash2, TriangleAlert } from "lucide-react";
import * as React from "react";

import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, Tip } from "@/components/ui/overlays";
import { Button } from "@/components/ui/primitives";
import { SUMMARY_LABELS } from "@/lib/fields";
import type { Extraction, LineItem, LineKey, TaxLine } from "@/lib/types";
import { cn, formatMoney, unitShort } from "@/lib/utils";

import { FieldInput } from "./FieldInput";

const STATUS_BAR: Record<string, string> = {
  verified: "bg-ok",
  high_confidence: "bg-accent/50",
  needs_review: "bg-warn",
  unreadable: "bg-violet",
  mismatch: "bg-bad",
  missing: "bg-faint",
};

export interface EditProps {
  currency: string | null;
  selectedPath: string | null;
  saving: Set<string>;
  onSave: (path: string, value: string | null) => void;
  onFocus: (path: string) => void;
}

export function LineItemsTable({
  ex,
  edit,
  categories,
  selectedIds,
  onToggleId,
  onToggleAll,
  onAdd,
  onDelete,
  onVerifyRow,
}: {
  ex: Extraction;
  edit: EditProps;
  categories: string[];
  selectedIds: Set<string>;
  onToggleId: (id: string) => void;
  onToggleAll: () => void;
  onAdd: (afterId?: string) => void;
  onDelete: (id: string) => void;
  onVerifyRow: (item: LineItem) => void;
}) {
  const cur = edit.currency;
  const items = ex.line_items;
  const isPriceList = ex.document_type.value === "price_list";
  const catOptions = Array.from(new Set([...categories, ...items.map((i) => i.category.value).filter(Boolean) as string[]])).map((c) => ({ value: c, label: c }));
  const allSelected = items.length > 0 && items.every((i) => selectedIds.has(i.id));
  const cell = (li: LineItem, key: LineKey, opts: { align?: "left" | "right"; kind?: "money" | "number" | "percent" | "unit" | "text" } = {}) => {
    const path = `line_items.${li.id}.${key}`;
    return (
      <FieldInput
        path={path}
        field={li[key]}
        kind={opts.kind}
        currency={cur}
        align={opts.align}
        onSave={edit.onSave}
        onFocus={edit.onFocus}
        saving={edit.saving.has(path)}
        selected={edit.selectedPath === path}
        listId={key === "unit" ? "sw-units" : undefined}
      />
    );
  };

  return (
    <div>
      <div className="scrollbar-thin overflow-x-auto">
        <table className="w-full min-w-[740px] border-separate border-spacing-0 text-[13px]">
          <thead>
            <tr className="text-left text-[11px] font-semibold uppercase tracking-wider text-muted">
              <th className="w-9 border-b border-border py-2 pl-3">
                <input type="checkbox" checked={allSelected} onChange={onToggleAll} className="size-3.5 accent-[var(--accent)]" aria-label="Select all items" />
              </th>
              <th className="w-7 border-b border-border py-2">#</th>
              <th className="border-b border-border py-2 pl-2">Item</th>
              {!isPriceList ? <th className="w-[58px] border-b border-border py-2 pr-2 text-right">Qty</th> : null}
              <th className="w-[66px] border-b border-border py-2 pl-2">Unit</th>
              <th className="w-[100px] border-b border-border py-2 pr-2 text-right">Rate</th>
              {!isPriceList ? <th className="w-[58px] border-b border-border py-2 pr-2 text-right">Tax %</th> : null}
              {!isPriceList ? <th className="w-[110px] border-b border-border py-2 pr-2 text-right">Amount</th> : null}
              <th className="w-[108px] border-b border-border py-2 pl-2">Category</th>
              <th className="w-9 border-b border-border py-2" />
            </tr>
          </thead>
          <tbody>
            {items.map((li, i) => {
              const computedDiffers = li.computed_amount && li.amount.value && li.computed_amount !== li.amount.value && Number(li.computed_amount) !== Number(li.amount.value);
              const match = li.pricebook_matches[0];
              const rowSelected = edit.selectedPath?.startsWith(`line_items.${li.id}.`);
              return (
                <React.Fragment key={li.id}>
                  <tr className={cn("group align-top transition-colors", rowSelected ? "bg-accent-soft/40" : "hover:bg-surface-2/70")}>
                    <td className="relative border-b border-border py-2 pl-3">
                      <span className={cn("absolute inset-y-1.5 left-0 w-[3px] rounded-r", STATUS_BAR[li.status])} />
                      <input type="checkbox" checked={selectedIds.has(li.id)} onChange={() => onToggleId(li.id)} className="mt-1.5 size-3.5 accent-[var(--accent)]" aria-label={`Select item ${i + 1}`} />
                    </td>
                    <td className="border-b border-border py-2 pt-2.5 text-[12px] tabular text-faint">{i + 1}</td>
                    <td className="border-b border-border py-1.5 pl-1">
                      <FieldInput path={`line_items.${li.id}.name`} field={li.name} onSave={edit.onSave} onFocus={edit.onFocus} saving={edit.saving.has(`line_items.${li.id}.name`)} selected={edit.selectedPath === `line_items.${li.id}.name`} className="font-medium" placeholder="Item name" />
                      <FieldInput path={`line_items.${li.id}.description`} field={li.description} onSave={edit.onSave} onFocus={edit.onFocus} saving={edit.saving.has(`line_items.${li.id}.description`)} selected={edit.selectedPath === `line_items.${li.id}.description`} className="py-0.5 text-[12.5px] text-muted" placeholder="Add description" />
                      {match ? (
                        <div className="ml-2 mt-0.5 flex items-center gap-1 text-[11.5px] text-muted">
                          <BookOpen className="size-3" />
                          In price book: <span className="font-medium text-fg-2">{match.name}</span>
                          {match.rate ? (
                            <span className={cn("tabular", li.rate.value && Number(match.rate) !== Number(li.rate.value) ? "text-warn" : "")}>
                              {formatMoney(match.rate, match.currency)}
                              {match.unit ? ` / ${unitShort(match.unit)}` : ""}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                    </td>
                    {!isPriceList ? <td className="border-b border-border py-1.5">{cell(li, "quantity", { align: "right", kind: "number" })}</td> : null}
                    <td className="border-b border-border py-1.5">{cell(li, "unit", { kind: "unit" })}</td>
                    <td className="border-b border-border py-1.5">{cell(li, "rate", { align: "right", kind: "money" })}</td>
                    {!isPriceList ? <td className="border-b border-border py-1.5">{cell(li, "tax_rate", { align: "right", kind: "percent" })}</td> : null}
                    {!isPriceList ? (
                      <td className="border-b border-border py-1.5">
                        {cell(li, "amount", { align: "right", kind: "money" })}
                        {computedDiffers ? (
                          <Tip content="Quantity × rate (− discount), computed with exact decimals">
                            <button type="button" onClick={() => edit.onSave(`line_items.${li.id}.amount`, li.computed_amount)} className="ml-auto mt-0.5 flex items-center gap-1 pr-2 text-[11px] font-medium text-accent hover:underline">
                              <Calculator className="size-3" /> = {formatMoney(li.computed_amount, cur, { symbol: false })} · use
                            </button>
                          </Tip>
                        ) : null}
                      </td>
                    ) : null}
                    <td className="border-b border-border py-1.5">
                      <FieldInput path={`line_items.${li.id}.category`} field={li.category} kind="category" options={catOptions} onSave={edit.onSave} onFocus={edit.onFocus} saving={edit.saving.has(`line_items.${li.id}.category`)} />
                    </td>
                    <td className="border-b border-border py-1.5 pr-1.5">
                      <Menu>
                        <MenuTrigger asChild>
                          <button type="button" className="grid size-7 place-items-center rounded-lg text-muted opacity-60 transition hover:bg-surface-3 hover:text-fg group-hover:opacity-100" aria-label="Row actions">
                            <Ellipsis className="size-4" />
                          </button>
                        </MenuTrigger>
                        <MenuContent>
                          <MenuItem icon={<BadgeCheck />} onSelect={() => onVerifyRow(li)}>
                            Confirm all values in row
                          </MenuItem>
                          <MenuItem icon={<Plus />} onSelect={() => onAdd(li.id)}>
                            Insert row below
                          </MenuItem>
                          <MenuSeparator />
                          <MenuItem icon={<Trash2 />} tone="danger" onSelect={() => onDelete(li.id)}>
                            Delete row
                          </MenuItem>
                        </MenuContent>
                      </Menu>
                    </td>
                  </tr>
                  {li.issues.length && li.status !== "verified" ? (
                    <tr>
                      <td />
                      <td colSpan={9} className="border-b border-border pb-2 pl-3">
                        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11.5px] text-warn">
                          {li.issues.map((iss) => (
                            <span key={iss} className="flex items-center gap-1">
                              <TriangleAlert className="size-3" /> {iss}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between gap-3 px-3 py-2.5">
        <Button size="sm" variant="ghost" onClick={() => onAdd(items.at(-1)?.id)}>
          <Plus className="size-4" /> Add line item
        </Button>
        {!isPriceList && ex.validation?.computed?.subtotal ? (
          <span className="text-[12.5px] text-muted">
            Sum of amounts <span className="ml-1 font-semibold tabular text-fg">{formatMoney(ex.validation.computed.subtotal, cur)}</span>
          </span>
        ) : null}
      </div>
    </div>
  );
}

function TotalRow({ label, children, computed, status, strong, cur }: { label: React.ReactNode; children: React.ReactNode; computed?: string | null; status?: "ok" | "bad" | null; strong?: boolean; cur: string | null }) {
  return (
    <div className={cn("grid grid-cols-[1fr_150px_118px] items-center gap-2 px-3 py-1", strong && "rounded-xl bg-surface-2 py-2")}>
      <div className={cn("min-w-0 truncate text-[13px]", strong ? "font-semibold" : "text-fg-2")}>{label}</div>
      <div>{children}</div>
      <div className={cn("text-right text-[12.5px] tabular", status === "bad" ? "font-medium text-bad" : status === "ok" ? "text-ok" : "text-faint")}>
        {computed !== undefined ? (computed ? formatMoney(computed, cur) : "—") : ""}
      </div>
    </div>
  );
}

export function TotalsPanel({ ex, edit, onAddTax, onDeleteTax }: { ex: Extraction; edit: EditProps; onAddTax: () => void; onDeleteTax: (id: string) => void }) {
  const [showAll, setShowAll] = React.useState(false);
  const cur = edit.currency;
  const v = ex.validation;
  const s = ex.summary;
  const present = (k: string) => !!s[k] && (s[k].value !== null || s[k].status === "unreadable");
  const alwaysShow = ["subtotal", "grand_total"];
  const keys = Object.keys(SUMMARY_LABELS).filter((k) => showAll || alwaysShow.includes(k) || present(k));
  const totalsCheck = v?.checks.find((c) => c.category === "total");
  const balanceCheck = v?.checks.find((c) => c.category === "balance");
  const subCheck = v?.checks.find((c) => c.category === "subtotal");
  const tone = (c?: { status: string }) => (c ? (c.status === "fail" ? "bad" : c.status === "pass" || c.status === "explained" ? "ok" : null) : null);
  const computedFor: Record<string, string | null | undefined> = {
    subtotal: v?.computed?.subtotal,
    tax_total: v?.computed?.tax_total,
    grand_total: v?.computed?.grand_total,
    balance_due: v?.computed?.balance_due,
  };
  const statusFor: Record<string, "ok" | "bad" | null> = { grand_total: tone(totalsCheck), balance_due: tone(balanceCheck), subtotal: tone(subCheck) };
  const itemName = (id: string) => ex.line_items.find((l) => l.id === id)?.name.value ?? "item";

  const money = (path: string, field = undefined as unknown as Extraction["summary"][string]) => (
    <FieldInput path={path} field={field} kind="money" currency={cur} align="right" withSymbol onSave={edit.onSave} onFocus={edit.onFocus} saving={edit.saving.has(path)} selected={edit.selectedPath === path} />
  );

  const taxRow = (t: TaxLine) => {
    const tc = v?.checks.find((c) => c.category === "tax" && c.fields.includes(`taxes.${t.id}.amount`));
    return (
      <div key={t.id} className="group">
        <TotalRow
          cur={cur}
          computed={tc?.expected ?? null}
          status={tone(tc)}
          label={
            <div className="flex items-center gap-1">
              <div className="w-[52%] min-w-0">
                <FieldInput path={`taxes.${t.id}.label`} field={t.label} onSave={edit.onSave} onFocus={edit.onFocus} saving={edit.saving.has(`taxes.${t.id}.label`)} placeholder="Tax" />
              </div>
              <div className="w-[64px]">
                <FieldInput path={`taxes.${t.id}.rate`} field={t.rate} kind="percent" align="right" onSave={edit.onSave} onFocus={edit.onFocus} saving={edit.saving.has(`taxes.${t.id}.rate`)} placeholder="%" />
              </div>
              <button type="button" onClick={() => onDeleteTax(t.id)} className="grid size-6 place-items-center rounded-md text-faint opacity-0 transition hover:bg-bad-soft hover:text-bad group-hover:opacity-100" aria-label="Delete tax line">
                <Trash2 className="size-3.5" />
              </button>
            </div>
          }
        >
          {money(`taxes.${t.id}.amount`, t.amount)}
        </TotalRow>
        {t.applies_to?.length ? (
          <div className="-mt-0.5 mb-1 px-5 text-[11.5px] text-muted">
            Applies to: {t.applies_to.map(itemName).join(", ")}
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <div>
      <div className="grid grid-cols-[1fr_150px_118px] gap-2 px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
        <span />
        <span className="pr-2 text-right">On document</span>
        <span className="text-right">Recalculated</span>
      </div>
      {["subtotal", "discount_total", "taxable_amount", "__taxes__", "tax_total", "shipping", "other_charges", "rounding", "grand_total", "amount_paid", "balance_due"].map((k) =>
        k === "__taxes__" ? (
          <React.Fragment key={k}>{ex.taxes.map(taxRow)}</React.Fragment>
        ) : keys.includes(k) ? (
          <TotalRow key={k} label={SUMMARY_LABELS[k]} cur={cur} computed={k in computedFor ? computedFor[k] ?? null : undefined} status={statusFor[k] ?? null} strong={k === "grand_total"}>
            {money(`summary.${k}`, s[k])}
          </TotalRow>
        ) : null,
      )}
      <div className="mt-1.5 flex gap-1 px-1.5">
        <Button size="xs" variant="ghost" onClick={onAddTax}>
          <Plus className="size-3.5" /> Tax line
        </Button>
        <Button size="xs" variant="ghost" onClick={() => setShowAll((x) => !x)}>
          {showAll ? "Hide empty totals" : "Show all total fields"}
        </Button>
      </div>
    </div>
  );
}
