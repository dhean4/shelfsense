import { redirect } from "next/navigation";

import { clerkEnabled } from "@/lib/auth";

export const dynamic = "force-dynamic";

// Clerk's hosted sign-in form. In dev auth mode there is nothing to sign in to (identity
// comes from the dev identity control), so the route sends the visitor home instead of
// rendering a component that needs ClerkProvider.
export default async function SignInPage() {
  if (!clerkEnabled) redirect("/");
  const { SignIn } = await import("@clerk/nextjs");
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <SignIn />
    </div>
  );
}
