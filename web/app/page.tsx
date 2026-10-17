import { Suspense } from "react";
import Dashboard from "@/components/Dashboard";
import { Spinner } from "@/components/ui";

export default function Page() {
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
