#!/usr/bin/env bash
#
# Generate Python gRPC stubs from the per-service .proto files under
# proto/library/v1/.
#
# Inputs:
#   PROTO_DIR  — directory containing the .proto tree (default: repo-root proto/)
#   OUT_DIR    — where to emit generated code (default: backend/src/library/generated/)
#
# Output (one trio per service):
#   $OUT_DIR/library/v1/book_pb2.py    + book_pb2_grpc.py    + book_pb2.pyi
#   $OUT_DIR/library/v1/member_pb2.py  + member_pb2_grpc.py  + member_pb2.pyi
#   $OUT_DIR/library/v1/loan_pb2.py    + loan_pb2_grpc.py    + loan_pb2.pyi
#   plus __init__.py files at each package level so the tree is importable as
#   library.generated.library.v1.<svc>_pb2.
#
# Note on the import-path rewrite at the bottom: protoc emits
# `from library.v1 import <svc>_pb2` inside <svc>_pb2_grpc.py, which assumes
# the proto package sits at the import root. Our generated tree lives one
# level deeper (under library.generated.*), so each line is rewritten in
# place. Well-known protoc quirk; see
# https://github.com/protocolbuffers/protobuf/issues/1491.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$BACKEND_DIR")"

PROTO_DIR="${PROTO_DIR:-$REPO_ROOT/proto}"
OUT_DIR="${OUT_DIR:-$BACKEND_DIR/src/library/generated}"

SERVICES=(book member loan)

for svc in "${SERVICES[@]}"; do
    if [[ ! -f "$PROTO_DIR/library/v1/${svc}.proto" ]]; then
        echo "gen_proto: cannot find ${svc}.proto at $PROTO_DIR/library/v1/${svc}.proto" >&2
        exit 1
    fi
done

mkdir -p "$OUT_DIR"

# Drop any stubs from prior generations so renamed/removed services don't
# leave stale pb2 modules behind to silently shadow imports.
rm -f "$OUT_DIR/library/v1/"*_pb2.py \
      "$OUT_DIR/library/v1/"*_pb2.pyi \
      "$OUT_DIR/library/v1/"*_pb2_grpc.py 2>/dev/null || true

# Compile all services in one protoc invocation so the descriptor pool can
# share file references — each .proto stays independent, but emitting them
# together is simpler than looping per-service and just as correct.
python -m grpc_tools.protoc \
    --proto_path="$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    --pyi_out="$OUT_DIR" \
    "$PROTO_DIR/library/v1/book.proto" \
    "$PROTO_DIR/library/v1/member.proto" \
    "$PROTO_DIR/library/v1/loan.proto"

# protoc creates the package-path directories but not the __init__.py marker
# files. Add them so the tree is a real Python package hierarchy.
touch "$OUT_DIR/__init__.py"
touch "$OUT_DIR/library/__init__.py"
touch "$OUT_DIR/library/v1/__init__.py"

# Rewrite the protoc-emitted import in each <svc>_pb2_grpc.py so it resolves
# under the library.generated.* namespace where the files actually live.
for svc in "${SERVICES[@]}"; do
    perl -pi -e "s|^from library\\.v1 import ${svc}_pb2|from library.generated.library.v1 import ${svc}_pb2|" \
        "$OUT_DIR/library/v1/${svc}_pb2_grpc.py"
done

echo "gen_proto: wrote stubs for [${SERVICES[*]}] to $OUT_DIR/library/v1/"
