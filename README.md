# Campus Café

Serverless ordering app: browser UI → API Gateway (HTTP API v2) → two Python Lambda services (Catalog, Orders), each with its own DynamoDB table. Infrastructure is AWS SAM. Progress is tracked in [TASKS.md](TASKS.md).

```
campus-cafe/
├── openapi.yaml          public HTTP contract
├── template.yaml         AWS SAM template (single deployment path)
├── services/catalog/     Catalog Lambda (owns Catalog table)
├── services/orders/      Orders Lambda (owns Orders table; calls Catalog via the gateway)
├── frontend/             HTML/CSS/JS (pending)
├── scripts/              seed.py, seed_data.json, run_unit_tests.sh
├── tests/                unit tests (moto)
└── evidence/             real command output and measurements
```

## Local checks (no AWS needed)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
scripts/run_unit_tests.sh
.venv/bin/cfn-lint template.yaml
```

## Deploy (requires AWS profile, SAM CLI, python3.12 supported in your region)
```bash
aws sts get-caller-identity        # confirm account and region first
sam validate --lint
sam build
sam deploy --guided                # stack name: campus-cafe
python scripts/seed.py --stack campus-cafe
```

## Cleanup
```bash
sam delete --stack-name campus-cafe
```

Sections still to be written once deployed: frontend startup, acceptance run, measurements.
