"use client";

import {
  BookOpen,
  FileText,
  FileUp,
  LayoutDashboard,
  Menu as MenuIcon,
  Monitor,
  Moon,
  ScanLine,
  Settings,
  Sun,
  Table2,
  ContactRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { Api } from "@/lib/api";
import type { SystemInfo } from "@/lib/types";
import { cn, UNIT_SHORT } from "@/lib/utils";

import { Tip } from "../ui/overlays";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/scan", label: "Scan Document", icon: ScanLine },
  { href: "/upload", label: "Upload Document", icon: FileUp },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/extracted", label: "Extracted Data", icon: Table2 },
  { href: "/price-book", label: "Price Book", icon: BookOpen },
  { href: "/clients", label: "Scan Business Card", icon: ContactRound },
  { href: "/settings", label: "Settings", icon: Settings },
];

type Theme = "light" | "dark" | "system";

export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem('sw-theme')||'system';var d=t==='dark'||(t==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = React.useState<Theme>("system");
  React.useEffect(() => {
    try {
      setTheme((localStorage.getItem("sw-theme") as Theme) || "system");
    } catch {
      /* storage unavailable */
    }
  }, []);
  const apply = React.useCallback((t: Theme) => {
    setTheme(t);
    try {
      localStorage.setItem("sw-theme", t);
    } catch {
      /* ignore */
    }
    const dark = t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  }, []);
  React.useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const fn = () => document.documentElement.classList.toggle("dark", mq.matches);
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, [theme]);
  return [theme, apply];
}

export function Logo({ className }: { className?: string }) {
  // unique id per instance: a duplicate id inside a display:none sidebar would blank every other copy
  const gid = `swg-${React.useId().replace(/:/g, "")}`;
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#4f8bff" />
          <stop offset="1" stopColor="#2246d6" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${gid})`} />
      <path d="M9 12V10.5A1.5 1.5 0 0 1 10.5 9H12M20 9h1.5A1.5 1.5 0 0 1 23 10.5V12M23 20v1.5a1.5 1.5 0 0 1-1.5 1.5H20M12 23h-1.5A1.5 1.5 0 0 1 9 21.5V20" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" fill="none" />
      <path d="M12.5 13.5h7M12.5 16.5h7M12.5 19.5h4" stroke="#fff" strokeOpacity=".9" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function ThemeSwitcher({ theme, setTheme }: { theme: Theme; setTheme: (t: Theme) => void }) {
  const opts: { v: Theme; icon: typeof Sun; label: string }[] = [
    { v: "light", icon: Sun, label: "Light" },
    { v: "dark", icon: Moon, label: "Dark" },
    { v: "system", icon: Monitor, label: "System" },
  ];
  return (
    <div className="flex rounded-xl bg-surface-3 p-0.5">
      {opts.map((o) => (
        <Tip key={o.v} content={`${o.label} theme`}>
          <button
            type="button"
            onClick={() => setTheme(o.v)}
            aria-label={`${o.label} theme`}
            className={cn("grid h-7 flex-1 place-items-center rounded-[10px] transition", theme === o.v ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg")}
          >
            <o.icon className="size-3.5" />
          </button>
        </Tip>
      ))}
    </div>
  );
}

function EngineStatus({ info }: { info: SystemInfo | null }) {
  if (!info) return <div className="h-14 animate-pulse rounded-xl bg-surface-3" />;
  const ocrOk = info.ocr.engines.some((e) => e.name === info.ocr.engine && e.available);
  const vision = info.llm.active;
  return (
    <Link href="/settings" className="block rounded-xl border border-border bg-surface-2 px-3 py-2.5 text-[12px] transition hover:border-border-strong">
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted">OCR engine</span>
        <span className="flex items-center gap-1.5 font-medium text-fg-2">
          <span className={cn("size-1.5 rounded-full", ocrOk ? "bg-ok" : "bg-bad")} />
          PP-OCRv6
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-muted">Vision AI</span>
        <span className="flex min-w-0 items-center gap-1.5 font-medium text-fg-2">
          <span className={cn("size-1.5 shrink-0 rounded-full", vision && info.llm.mode !== "off" ? "bg-ok" : "bg-warn")} />
          <span className="truncate">{vision && info.llm.mode !== "off" ? vision.split("/")[0] : "Not configured"}</span>
        </span>
      </div>
    </Link>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [theme, setTheme] = useTheme();
  const [drawer, setDrawer] = React.useState(false);
  const [info, setInfo] = React.useState<SystemInfo | null>(null);

  React.useEffect(() => {
    Api.system().then(setInfo).catch(() => setInfo(null));
  }, [pathname]);
  React.useEffect(() => setDrawer(false), [pathname]);

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  const immersive = pathname.startsWith("/scan");
  // The review workspace needs every pixel: collapse the sidebar to an icon rail there.
  const rail = /^\/documents\/[^/]+$/.test(pathname);

  return (
    <div className="min-h-dvh">
      {/* Desktop icon rail (review workspace) */}
      {rail ? (
        <aside className="fixed inset-y-0 left-0 z-30 hidden w-[68px] flex-col items-center gap-1 border-r border-border bg-surface/80 pb-4 pt-4 backdrop-blur-xl lg:flex">
          <Link href="/" className="mb-3" aria-label="ScanWise home">
            <Logo className="size-9" />
          </Link>
          {NAV.map((n) => (
            <Tip key={n.href} content={n.label} side="right">
              <Link
                href={n.href}
                aria-label={n.label}
                className={cn("grid size-10 place-items-center rounded-xl transition-colors", isActive(n.href) ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-3 hover:text-fg")}
              >
                <n.icon className="size-[19px]" />
              </Link>
            </Tip>
          ))}
          <div className="mt-auto">
            <Tip content={`Theme: ${theme}`} side="right">
              <button
                type="button"
                onClick={() => setTheme(theme === "light" ? "dark" : theme === "dark" ? "system" : "light")}
                className="grid size-10 place-items-center rounded-xl text-muted hover:bg-surface-3 hover:text-fg"
                aria-label="Change theme"
              >
                {theme === "light" ? <Sun className="size-[18px]" /> : theme === "dark" ? <Moon className="size-[18px]" /> : <Monitor className="size-[18px]" />}
              </button>
            </Tip>
          </div>
        </aside>
      ) : null}

      {/* Desktop sidebar */}
      <aside className={cn("fixed inset-y-0 left-0 z-30 hidden w-[248px] flex-col border-r border-border bg-surface/80 px-3 pb-4 pt-5 backdrop-blur-xl", !rail && "lg:flex")}>
        <Link href="/" className="mb-6 flex items-center gap-2.5 px-2">
          <Logo className="size-8" />
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">ScanWise</div>
            <div className="text-[11px] font-medium text-muted">Intelligent document reader</div>
          </div>
        </Link>
        <Link
          href="/scan"
          className="mb-4 flex h-10 items-center justify-center gap-2 rounded-xl bg-accent text-[14px] font-medium text-accent-fg shadow-sm shadow-accent/25 transition hover:bg-accent-hover active:scale-[0.98]"
        >
          <ScanLine className="size-4" /> New scan
        </Link>
        <nav className="flex flex-1 flex-col gap-0.5">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={cn(
                "group flex h-9.5 items-center gap-3 rounded-xl px-3 text-[14px] font-medium transition-colors",
                isActive(n.href) ? "bg-accent-soft text-accent" : "text-fg-2 hover:bg-surface-3 hover:text-fg",
              )}
            >
              <n.icon className={cn("size-[18px]", isActive(n.href) ? "text-accent" : "text-muted group-hover:text-fg-2")} />
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="space-y-3">
          <EngineStatus info={info} />
          <ThemeSwitcher theme={theme} setTheme={setTheme} />
        </div>
      </aside>

      {/* Mobile top bar */}
      {!immersive && (
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-surface/85 px-4 backdrop-blur-xl lg:hidden">
          <Link href="/" className="flex items-center gap-2">
            <Logo className="size-7" />
            <span className="text-[15px] font-semibold tracking-tight">ScanWise</span>
          </Link>
          <button type="button" onClick={() => setDrawer(true)} className="grid size-9 place-items-center rounded-xl text-fg-2 hover:bg-surface-3" aria-label="Open menu">
            <MenuIcon className="size-5" />
          </button>
        </header>
      )}

      {/* Mobile drawer */}
      {drawer && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-overlay animate-fade-in" onClick={() => setDrawer(false)} />
          <div className="absolute inset-y-0 right-0 flex w-[82%] max-w-xs flex-col gap-1 bg-surface p-4 shadow-pop animate-slide-up">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[15px] font-semibold">Menu</span>
              <button type="button" onClick={() => setDrawer(false)} className="grid size-9 place-items-center rounded-xl hover:bg-surface-3" aria-label="Close menu">
                <X className="size-5" />
              </button>
            </div>
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} className={cn("flex h-11 items-center gap-3 rounded-xl px-3 text-[15px] font-medium", isActive(n.href) ? "bg-accent-soft text-accent" : "text-fg-2")}>
                <n.icon className="size-5" /> {n.label}
              </Link>
            ))}
            <div className="mt-auto space-y-3">
              <EngineStatus info={info} />
              <ThemeSwitcher theme={theme} setTheme={setTheme} />
            </div>
          </div>
        </div>
      )}

      <main className={cn(rail ? "lg:pl-[68px]" : "lg:pl-[248px]", !immersive && "pb-24 lg:pb-0")}>{children}</main>

      {/* shared unit suggestions for unit inputs */}
      <datalist id="sw-units">
        {Object.entries(UNIT_SHORT).map(([code, short]) => (
          <option key={code} value={code}>
            {short}
          </option>
        ))}
      </datalist>

      {/* Mobile bottom tab bar */}
      {!immersive && (
        <nav className="safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/90 backdrop-blur-xl lg:hidden">
          <div className="grid h-16 grid-cols-5 items-center">
            {[NAV[0], NAV[3]].map((n) => (
              <TabLink key={n.href} href={n.href} icon={n.icon} label={n.label.split(" ")[0]} active={isActive(n.href)} />
            ))}
            <div className="grid place-items-center">
              <Link href="/scan" aria-label="Scan document" className="-mt-7 grid size-14 place-items-center rounded-2xl bg-accent text-accent-fg shadow-lg shadow-accent/35 active:scale-95">
                <ScanLine className="size-6" />
              </Link>
            </div>
            <TabLink href="/price-book" icon={BookOpen} label="Price Book" active={isActive("/price-book")} />
            <TabLink href="/settings" icon={Settings} label="Settings" active={isActive("/settings")} />
          </div>
        </nav>
      )}
    </div>
  );
}

function TabLink({ href, icon: Icon, label, active }: { href: string; icon: typeof Sun; label: string; active: boolean }) {
  return (
    <Link href={href} className={cn("flex flex-col items-center gap-1 text-[10.5px] font-medium", active ? "text-accent" : "text-muted")}>
      <Icon className="size-[22px]" />
      {label}
    </Link>
  );
}

export function PageContainer({ children, className, wide }: { children: React.ReactNode; className?: string; wide?: boolean }) {
  return <div className={cn("mx-auto w-full px-4 py-6 sm:px-6 lg:px-10 lg:py-9", wide ? "max-w-[1500px]" : "max-w-6xl", className)}>{children}</div>;
}
