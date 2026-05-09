import { BookEdit } from "./BookEdit";

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <BookEdit id={id} />;
}
