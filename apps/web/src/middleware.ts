import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

import { clerkEnabled } from "@/lib/auth";

// With Clerk configured, its middleware attaches the session to every request. Without it
// (dev auth mode) requests pass straight through and identity comes from cookies.
export default clerkEnabled
  ? clerkMiddleware()
  : function middleware() {
      return NextResponse.next();
    };

export const config = {
  matcher: [
    // Skip Next.js internals and static files, unless found in search params
    "/((?!_next|.*\\..*).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
    // Clerk's own endpoints (session sync, handshake)
    "/__clerk/:path*",
  ],
};
