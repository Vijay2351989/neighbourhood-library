#!/usr/bin/env bash
#
# Generate Python gRPC stubs from proto/library/v1/library.proto.
#
# Inputs:
#   PROTO_DIR  — directory containing the .proto tree (default: repo-root proto/)
#   OUT_DIR    — where to emit generated code (default: backend/src/library/generated/)
#
# Output:
#   $OUT_DIR/library/v1/library_pb2.py
#   $OUT_DIR/library/v1/library_pb2_grpc.py
#   $OUT_DIR/library/v1/library_pb2.pyi
#   plus __init__.py files at each package level so the tree is importable as
#   library.generated.library.v1.library_pb2.
#
# Note on the import-path rewrite at the bottom: protoc emits
# `from library.v1 import library_pb2` inside _pb2_grpc.py, which assumes the
# proto package sits at the import root. Our generated tree lives one level
# deeper (under library.generated.*), so the line is rewritten in place.
# This is a well-known protoc quirk; see
# https://github.com/protocolbuffers/protobuf/issues/1491.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$BACKEND_DIR")"

PROTO_DIR="${PROTO_DIR:-$REPO_ROOT/proto}"
OUT_DIR="${OUT_DIR:-$BACKEND_DIR/src/library/generated}"

if [[ ! -f "$PROTO_DIR/library/v1/library.proto" ]]; then
    echo "gen_proto: cannot find library.proto at $PROTO_DIR/library/v1/library.proto" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

python -m grpc_tools.protoc \
    --proto_path="$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    --pyi_out="$OUT_DIR" \
    "$PROTO_DIR/library/v1/library.proto"

# protoc creates the package-path directories but not the __init__.py marker
# files. Add them so the tree is a real Python package hierarchy.
touch "$OUT_DIR/__init__.py"
touch "$OUT_DIR/library/__init__.py"
touch "$OUT_DIR/library/v1/__init__.py"

# Rewrite the protoc-emitted import in _pb2_grpc.py so it resolves under the
# library.generated.* namespace where the files actually live.
perl -pi -e 's|^from library\.v1 import library_pb2|from library.generated.library.v1 import library_pb2|' \
    "$OUT_DIR/library/v1/library_pb2_grpc.py"

echo "gen_proto: wrote stubs to $OUT_DIR/library/v1/"
