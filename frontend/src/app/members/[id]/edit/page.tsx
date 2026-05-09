import { MemberEdit } from "./MemberEdit";

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <MemberEdit id={id} />;
}
