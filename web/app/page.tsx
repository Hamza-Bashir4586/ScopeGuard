import { Suspense } from "react";
import ConnectedWorkspace from "@/components/workspace/ConnectedWorkspace";
import { Spinner } from "@/components/ui";
import ScopeGuardWorkspace from "@/components/workspace/ScopeGuardWorkspace";

export default function Page() {
  // Fictional, browser-only preview is separate from the default v2 API workspace.
  if (process.env.NEXT_PUBLIC_DESIGN_PREVIEW === "1") return <ScopeGuardWorkspace />;
  return (
    <Suspense
      fallback={
        <div className="p-6">
          <Spinner />
        </div>
      }
    >
      <ConnectedWorkspace />
    </Suspense>
  );
}
