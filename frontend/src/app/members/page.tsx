import { Suspense } from "react";
import { MembersList } from "./MembersList";

export default function Page() {
  return (
    <Suspense>
      <MembersList />
    </Suspense>
  );
}
