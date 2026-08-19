import "./globals.css";
import { UserProvider } from "@/components/UserContext";
import { Shell } from "@/components/Shell";

export const metadata = { title: "ApplyLoop" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* Browser extensions (password managers, etc.) inject attributes into <body>
          before React hydrates — a known false-positive, not a real mismatch. */}
      <body suppressHydrationWarning>
        <UserProvider>
          <Shell>{children}</Shell>
        </UserProvider>
      </body>
    </html>
  );
}
