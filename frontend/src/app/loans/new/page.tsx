import { Suspense } from "react";
import { NewLoanForm } from "./NewLoanForm";

export default function Page() {
  return (
    <Suspense>
      <NewLoanForm />
    </Suspense>
  );
}
