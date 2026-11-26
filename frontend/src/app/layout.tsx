import type { Metadata, Viewport } from "next";
import { Toaster } from "sonner";

import { AppShell, THEME_SCRIPT } from "@/components/shell/AppShell";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ScanWise AI", template: "%s · ScanWise AI" },
  description: "Intelligent invoice & financial document reader with evidence-backed extraction and exact validation.",
  applicationName: "ScanWise AI",
  appleWebApp: { capable: true, title: "ScanWise", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3f4f7" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0d14" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
        <Toaster position="top-center" richColors closeButton toastOptions={{ style: { borderRadius: 14 } }} />
      </body>
    </html>
  );
}
