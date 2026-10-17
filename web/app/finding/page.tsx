import { Suspense } from "react";
import type { Metadata } from "next";
import FindingDetail from "@/components/FindingDetail";
import { Spinner } from "@/components/ui";

export const metadata: Metadata = { title: "Finding · ScopeGuard" };

export default function Page() {
  return (
    <Suspense
      fallback={
        <div className="p-6">
          <Spinner label="Loading finding…" />
        </div>
      }
    >
      <FindingDetail />
    </Suspense>
  );
}
