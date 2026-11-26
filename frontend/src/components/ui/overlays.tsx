"use client";

import { X } from "lucide-react";
import { Dialog as RDialog, DropdownMenu as RMenu, Popover as RPopover, Tooltip as RTooltip } from "radix-ui";
import * as React from "react";

import { cn } from "@/lib/utils";

import { Button } from "./primitives";

/** Dialog that becomes a bottom sheet on small screens (iOS-like). */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
  size = "md",
  headerLeft,
  headerRight,
  hideClose,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
  size?: "sm" | "md" | "lg" | "xl";
  headerLeft?: React.ReactNode;
  headerRight?: React.ReactNode;
  hideClose?: boolean;
}) {
  const width = { sm: "sm:max-w-md", md: "sm:max-w-xl", lg: "sm:max-w-3xl", xl: "sm:max-w-5xl" }[size];
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-50 bg-overlay backdrop-blur-[2px] data-[state=open]:animate-fade-in" />
        <RDialog.Content
          className={cn(
            "fixed z-50 flex max-h-[92dvh] w-full flex-col overflow-hidden border border-border bg-bg shadow-pop outline-none",
            "inset-x-0 bottom-0 rounded-t-[22px] data-[state=open]:animate-slide-up",
            "sm:inset-auto sm:left-1/2 sm:top-1/2 sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-[22px]",
            width,
            className,
          )}
        >
          <div className="mx-auto mt-2 h-1.5 w-10 rounded-full bg-border-strong sm:hidden" />
          <div className="flex items-center gap-3 border-b border-border/70 px-5 py-3.5">
            {headerLeft ? <div className="shrink-0">{headerLeft}</div> : null}
            <div className={cn("min-w-0 flex-1", headerLeft && headerRight ? "text-center" : "")}>
              <RDialog.Title className="truncate text-[16px] font-semibold tracking-tight text-fg">{title}</RDialog.Title>
              {description ? <RDialog.Description className="mt-0.5 text-[13px] text-muted">{description}</RDialog.Description> : <RDialog.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</RDialog.Description>}
            </div>
            {headerRight ? <div className="shrink-0">{headerRight}</div> : null}
            {!hideClose && !headerRight ? (
              <RDialog.Close asChild>
                <Button variant="ghost" size="icon-sm" aria-label="Close">
                  <X className="size-4" />
                </Button>
              </RDialog.Close>
            ) : null}
          </div>
          <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">{children}</div>
          {footer ? <div className="safe-bottom flex items-center justify-end gap-2 border-t border-border/70 bg-surface px-5 py-3">{footer}</div> : null}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  tone = "danger",
  onConfirm,
  loading,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  tone?: "danger" | "primary";
  onConfirm: () => void;
  loading?: boolean;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="px-5 py-4 text-[14px] text-fg-2">{description}</div>
    </Sheet>
  );
}

export const Menu = RMenu.Root;
export const MenuTrigger = RMenu.Trigger;

export function MenuContent({ children, align = "end", className }: { children: React.ReactNode; align?: "start" | "end" | "center"; className?: string }) {
  return (
    <RMenu.Portal>
      <RMenu.Content
        align={align}
        sideOffset={6}
        className={cn("z-50 min-w-48 rounded-xl border border-border bg-surface p-1 shadow-pop data-[state=open]:animate-fade-in", className)}
      >
        {children}
      </RMenu.Content>
    </RMenu.Portal>
  );
}

export function MenuItem({ children, onSelect, tone, icon, disabled }: { children: React.ReactNode; onSelect?: () => void; tone?: "danger"; icon?: React.ReactNode; disabled?: boolean }) {
  return (
    <RMenu.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex cursor-pointer select-none items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] outline-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        tone === "danger" ? "text-bad data-[highlighted]:bg-bad-soft" : "text-fg data-[highlighted]:bg-surface-3",
      )}
    >
      {icon ? <span className="text-muted [&_svg]:size-4">{icon}</span> : null}
      {children}
    </RMenu.Item>
  );
}

export function MenuLabel({ children }: { children: React.ReactNode }) {
  return <RMenu.Label className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-faint">{children}</RMenu.Label>;
}

export function MenuSeparator() {
  return <RMenu.Separator className="my-1 h-px bg-border" />;
}

export function Tip({ content, children, side = "top" }: { content: React.ReactNode; children: React.ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  if (!content) return <>{children}</>;
  return (
    <RTooltip.Provider delayDuration={250}>
      <RTooltip.Root>
        <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
        <RTooltip.Portal>
          <RTooltip.Content
            side={side}
            sideOffset={6}
            className="z-[60] max-w-xs rounded-lg bg-fg px-2.5 py-1.5 text-[12px] leading-snug text-bg shadow-pop data-[state=delayed-open]:animate-fade-in"
          >
            {content}
          </RTooltip.Content>
        </RTooltip.Portal>
      </RTooltip.Root>
    </RTooltip.Provider>
  );
}

export function Pop({ trigger, children, align = "start", className }: { trigger: React.ReactNode; children: React.ReactNode; align?: "start" | "center" | "end"; className?: string }) {
  return (
    <RPopover.Root>
      <RPopover.Trigger asChild>{trigger}</RPopover.Trigger>
      <RPopover.Portal>
        <RPopover.Content align={align} sideOffset={6} className={cn("z-50 rounded-xl border border-border bg-surface p-3 shadow-pop data-[state=open]:animate-fade-in", className)}>
          {children}
        </RPopover.Content>
      </RPopover.Portal>
    </RPopover.Root>
  );
}
