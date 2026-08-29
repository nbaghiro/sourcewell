"""Dump the OpenAPI schema to a file without running a server.

Usage: `uv run python -m app.openapi_export <out_path>` (defaults to stdout). The frontend's
`npm run gen:api` consumes the exported file, so `make gen-api` regenerates
`frontend/src/lib/api/schema.d.ts` offline; CI diffs the result to catch a stale schema.
"""

import json
import sys

from app.main import create_app


def main() -> None:
    schema = create_app().openapi()
    payload = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as f:
            f.write(payload)
    else:
        sys.stdout.write(payload)


if __name__ == "__main__":
    main()
