# Contributing

Thanks for helping. Please follow the [Code of conduct](CODE_OF_CONDUCT.md) and report security
problems as described in [SECURITY.md](SECURITY.md).

1. **Set up**: `uv sync --locked`, then `uv run pre-commit install`.
2. **Branch** from `main` as `feature/…`, `bug/…`, `task/…`, `docs/…` or `refactor/…`. The hooks
   refuse commits to `main` and badly named branches.
3. **Commit** with a prefix: `ADDED:`, `FIXED:`, `TASK:`, `DOCS:` or `REFACTOR:` (see
   `scripts/check_commit_msg.sh`).
4. **Check** with `uv run task lint` and `uv run task test`. For mobile changes, also run
   `docker build -f docker/mobile.Dockerfile --target test .` (or `npm run lint && npm run
   test:ci` in `mobile/`).
5. **Fixtures**: if you edit `data/fixtures/*.json`, run `uv run task bundle` to rebuild
   `data/demo.rl`. A test fails if the bundled store drifts from the fixtures.
6. Keep demo data fictional. Never commit credentials, `.env` or anything under `var/`.
