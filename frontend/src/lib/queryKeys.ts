// Centralized TanStack Query key factories. Keeping these typed and in one
// place means cache invalidation after a mutation can never silently miss a
// matching list query.

import type { LoanFilter } from "@/generated/library/v1/loan_pb";

export const bookKeys = {
  all: ["books"] as const,
  lists: () => [...bookKeys.all, "list"] as const,
  list: (params: { search?: string; offset?: number; pageSize?: number }) =>
    [...bookKeys.lists(), params] as const,
  details: () => [...bookKeys.all, "detail"] as const,
  detail: (id: string) => [...bookKeys.details(), id] as const,
};

export const memberKeys = {
  all: ["members"] as const,
  lists: () => [...memberKeys.all, "list"] as const,
  list: (params: { search?: string; offset?: number; pageSize?: number }) =>
    [...memberKeys.lists(), params] as const,
  details: () => [...memberKeys.all, "detail"] as const,
  detail: (id: string) => [...memberKeys.details(), id] as const,
  loans: (
    id: string,
    params: { filter: LoanFilter; offset?: number; pageSize?: number },
  ) => [...memberKeys.detail(id), "loans", params] as const,
};

export const loanKeys = {
  all: ["loans"] as const,
  lists: () => [...loanKeys.all, "list"] as const,
  list: (params: {
    filter?: LoanFilter;
    memberId?: string;
    bookId?: string;
    offset?: number;
    pageSize?: number;
  }) => [...loanKeys.lists(), params] as const,
};
