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
- `backend/app/` — FastAPI modular monolith: `api/` (routers) → `services/` (bounded contexts:
  `outreach` · `sourcing` · `workspace` · `insights` · `billing`) over shared `models.py` +
  `targeting.py`; `agents/` (LLM agents), `core/` (kernel incl. the agent runtime), `ext/`
  (people-data + channel adapters), `worker.py` (the self-clocking send/source engine).
- `frontend/` — React + Vite + Tailwind ("Wellspring" design), typed against the backend's OpenAPI
  (`make gen-api` regenerates `src/lib/api/schema.d.ts` offline; CI fails if it goes stale).
- `shared/` — the canonical targeting case table pinned by both test suites.
- `infra/` — docker / local.

## License
Proprietary — all rights reserved. See [LICENSE](LICENSE).
