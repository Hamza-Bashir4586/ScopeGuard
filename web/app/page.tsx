import { Suspense } from "react";
import Dashboard from "@/components/Dashboard";
import { Spinner } from "@/components/ui";
import ScopeGuardWorkspace from "@/components/workspace/ScopeGuardWorkspace";

export default function Page() {
  // Opt-in presentation surface while the team connects the new backend features.
  // Existing API behavior and deep links are preserved when this flag is absent.
  if (process.env.NEXT_PUBLIC_DESIGN_PREVIEW === "1") return <ScopeGuardWorkspace />;
  return (
    <Suspense
      fallback={
        <div className="p-6">
          <Spinner />
        </div>
      }
    >
      <Dashboard />
    </Suspense>
  );
}
