import { Suspense } from "react";
import { LoansList } from "./LoansList";

export default function Page() {
  return (
    <Suspense>
      <LoansList />
    </Suspense>
  );
}
