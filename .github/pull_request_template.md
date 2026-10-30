## What & why

## How it was tested
- [ ] `make lint` / `make typecheck` / `make test` pass
- [ ] `make smoke` passes on a fresh `docker compose up --build`

## Safety checklist
- [ ] No code is emitted that is absent from an ingested dataset
- [ ] No dose regimen fields added to any response schema
- [ ] Sample data stays labelled `SAMPLE` and uses `SAMPLE-` codes
