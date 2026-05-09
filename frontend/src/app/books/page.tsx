import { Suspense } from "react";
import { BooksList } from "./BooksList";

// Wrap useSearchParams() consumers in <Suspense> so Next.js can prerender
// the surrounding shell while the URL-driven list bails out to dynamic.
export default function Page() {
  return (
    <Suspense>
      <BooksList />
    </Suspense>
  );
}
