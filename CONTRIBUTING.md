# Contributing

1. Branch from `main`; use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
2. Run `make lint typecheck test` before pushing; CI runs the same steps plus a Docker smoke test.
3. Never commit secrets or real patient data. Bundled data must stay labelled `SAMPLE` with `SAMPLE-` codes.
4. Engine changes must keep the safety invariants: no invented codes, no unapproved links in FHIR output, no dose regimens.
