# Sourcewell

[![CI](https://github.com/nbaghiro/sourcewell/actions/workflows/ci.yml/badge.svg)](https://github.com/nbaghiro/sourcewell/actions/workflows/ci.yml)

AI-agent platform for automated outbound cold-reach (recruiting first; a generic outbound-funnel engine).

See `.docs/product-spec.md` and `.docs/tech-spec.md` for how the product and the implementation are
intended to work.

## Quickstart (backend)

```bash
make up          # start Postgres (:8902) + Mailpit (:8904 web / :8905 smtp)
make install     # uv sync backend deps
make migrate     # apply DB migrations
make dev         # API on http://localhost:8901  (docs at /docs)
make worker      # runtime worker: ticks due enrollments (optional for QA)
make test        # full test suite   (make test-fast for the no-DB subset)
make check       # lint + typecheck + test
```

Local host ports use the **89xx** band so they don't clash with other repos.

To drive the product end to end by hand (contacts → campaign → rank → approve → send → reply),
follow **[the QA guide](.docs/qa-guide.md)**.

## Layout
- `backend/app/` — FastAPI modular monolith: `core/` (kernel), `runtime/` (autonomous send engine),
  and feature modules grouped into bounded contexts (`outreach/` · `people/` · `workspace/` ·
  `insights/` · `agent/`), with shared `models.py` + `targeting.py`.
- `frontend/` — React + Vite + Tailwind ("Wellspring" design), typed against the backend's OpenAPI.
- `infra/` — docker / local.

## License
Proprietary — all rights reserved. See [LICENSE](LICENSE).
