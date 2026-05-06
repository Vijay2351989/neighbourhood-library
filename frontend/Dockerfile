# Frontend dev image for the Neighborhood Library staff UI.
#
# Phase 1 scope: bring up `next dev` inside Compose so the home page is
# reachable at http://localhost:3000. Production builds (`next build` /
# `next start`) are intentionally out of scope for the take-home.
#
# Contract this image owes docker-compose.yml:
#   - listens on :3000 (must bind 0.0.0.0 — `localhost` would not be
#     reachable across the Docker port mapping)
#   - serves the home page at `/`
#   - consumes NEXT_PUBLIC_API_BASE_URL at runtime
FROM node:20-alpine

WORKDIR /app

# Install deps first so this layer caches across source-only edits.
COPY package.json package-lock.json ./
RUN npm ci

# Bring in the .proto via the named build context wired up in
# docker-compose.yml (additional_contexts.proto -> repo-root proto/). Path is
# /app/proto, which matches the relative `../proto` referenced in
# package.json's gen:proto script after we cd into /app.
COPY --from=proto . /app/proto

# Then copy the rest of the app. .dockerignore keeps node_modules / .next
# / local env files / the generated stubs directory out of the build context.
COPY . .

# Generate Connect / protobuf-es TypeScript stubs into src/generated/ before
# `next dev` starts — page code in Phase 6 will import from here. We invoke
# `buf generate` directly rather than `npm run gen:proto` because the npm
# script's input path `../proto` is host-relative; inside the container the
# proto build context lands at /app/proto.
RUN npx --no-install buf generate /app/proto

EXPOSE 3000

# `next dev` defaults the hostname to 0.0.0.0 in recent versions, but we
# pass it explicitly so the binding is unambiguous and survives any future
# default change. The `--` after `npm run dev` tells npm to forward the
# remaining flags to the underlying `next` process.
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]
