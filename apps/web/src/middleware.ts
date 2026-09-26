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
  matcher: ["/((?!_next|.*\\..*).*)"],
};
