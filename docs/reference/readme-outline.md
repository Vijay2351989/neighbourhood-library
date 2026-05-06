# Project README — Outline

**Status:** Complete (this is the skeleton; the actual README ships in [Phase 7](../phases/phase-7-polish.md))
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)

The root `README.md` is the rubric's documentation deliverable. It must cover the items below. This skeleton is what Phase 7 fills in with concrete commands and copy.

---

## Required sections

- **What this is.** One paragraph: a take-home build of a small library management service. gRPC-Web + Python + Postgres + Next.js.

- **Architecture overview.** A trimmed version of the [00-overview.md §6](../00-overview.md#6-architecture) diagram and a 2-paragraph explanation.

- **Prerequisites.** Docker Desktop, that's it. (Optionally: Python 3.12, Node 20, `uv`, `buf` for local dev outside Docker.)

- **Quick start.**
  1. `git clone ...`
  2. `docker compose up` — wait for "api: listening on :50051".
  3. (Optional) `docker compose --profile seed up seed` to populate sample data.
  4. Open `http://localhost:3000`.

- **What you can do.** A short tour of the UI mapped to the four assignment requirements (book CRUD, member CRUD, borrow, return, list) plus fines visibility.

- **Database setup.** How to point at an external Postgres if not using Compose; how migrations work; how to reset the DB (`docker compose down -v`).

- **.proto compilation.** How to regenerate stubs after editing `proto/library/v1/library.proto` — one command for each side (`backend/scripts/gen_proto.sh` and `cd frontend && npx buf generate`).

- **Running the server outside Docker.** Optional dev workflow: `cd backend && uv sync && uv run alembic upgrade head && uv run python -m library.main`.

- **Environment variables.** Table: `DATABASE_URL`, `GRPC_PORT`, `DEFAULT_LOAN_DAYS`, `FINE_GRACE_DAYS`, `FINE_PER_DAY_CENTS`, `FINE_CAP_CENTS`, `NEXT_PUBLIC_API_BASE_URL`.

- **Sample client script.** `python backend/scripts/sample_client.py` — what it does and what to expect in the output.

- **How to test.** `cd backend && uv run pytest`. Note that testcontainers needs Docker running. Mention the optional Playwright test if it ships.

- **Troubleshooting.** Three or four common gotchas: port conflicts, Docker memory, regenerating stubs after a `.proto` change, browser CORS errors.

- **Project layout.** A tree of the top two levels of directories with one-line descriptions.

- **Design decisions.** Link to [docs/README.md](../README.md) for anyone who wants the full reasoning.

---

## Notes for the author

- **The README is what the rubric scores on documentation.** Don't skimp.
- Every command in "Quick start" must actually work end-to-end before the deliverable ships. Test on a fresh clone if possible.
- Keep the README under ~600 lines. Anything longer belongs in `docs/`.
- Link liberally into `docs/` for anyone who wants depth; the README is the on-ramp, not the manual.
