import { redirect } from "next/navigation";

import { clerkEnabled } from "@/lib/auth";

export const dynamic = "force-dynamic";

// Clerk's hosted sign-up form; see sign-in/page.tsx for why dev mode redirects.
export default async function SignUpPage() {
  if (!clerkEnabled) redirect("/");
  const { SignUp } = await import("@clerk/nextjs");
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <SignUp />
    </div>
  );
}
