# PortWorld CLI Command Map

## Source-mode bootstrap (repo development)
```bash
./skills/portworld-cli-autopilot/scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode source
```

## Published-mode bootstrap (operator local runtime)
```bash
./skills/portworld-cli-autopilot/scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode published --stack-name default
```

## Local validation and inspection
```bash
uv run python -m portworld_cli.main doctor --target local
uv run python -m portworld_cli.main status
uv run python -m portworld_cli.main config show
```

## Managed readiness checks
```bash
uv run python -m portworld_cli.main doctor --target gcp-cloud-run --project <project> --region <region>
uv run python -m portworld_cli.main doctor --target aws-ecs-fargate --aws-region <region>
uv run python -m portworld_cli.main doctor --target azure-container-apps --azure-subscription <sub> --azure-resource-group <rg> --azure-region <region>
```

## Managed deploy commands
```bash
uv run python -m portworld_cli.main deploy gcp-cloud-run --project <project> --region <region>
uv run python -m portworld_cli.main deploy aws-ecs-fargate --region <region>
uv run python -m portworld_cli.main deploy azure-container-apps --subscription <sub> --resource-group <rg> --region <region>
```

## Logs
```bash
uv run python -m portworld_cli.main logs gcp-cloud-run --since 24h --limit 50
uv run python -m portworld_cli.main logs aws-ecs-fargate --since 24h --limit 50
uv run python -m portworld_cli.main logs azure-container-apps --since 24h --limit 50
```

## Provider/config maintenance
```bash
uv run python -m portworld_cli.main providers list
uv run python -m portworld_cli.main config show
uv run python -m portworld_cli.main config edit providers
uv run python -m portworld_cli.main config edit cloud
```
