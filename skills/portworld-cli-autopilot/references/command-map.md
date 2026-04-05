# PortWorld CLI Command Map

All examples use placeholders (`<project>`, `<region>`, etc.). Replace with real values.

## Published CLI (`portworld` on PATH)

Use after `uv tool install portworld`, `pipx install portworld`, or the bootstrap installer — any install that exposes the `portworld` entry point.

### Local validation and inspection

```bash
portworld doctor --target local
portworld status
portworld config show
```

### Managed readiness checks

```bash
portworld doctor --target gcp-cloud-run --gcp-project <project> --gcp-region <region>
portworld doctor --target aws-ecs-fargate --aws-region <region>
portworld doctor --target azure-container-apps --azure-subscription <sub> --azure-resource-group <rg> --azure-region <region>
```

### Managed deploy commands

```bash
portworld deploy gcp-cloud-run --project <project> --region <region>
portworld deploy aws-ecs-fargate --region <region>
portworld deploy azure-container-apps --subscription <sub> --resource-group <rg> --region <region>
```

### Logs

```bash
portworld logs gcp-cloud-run --since 24h --limit 50
portworld logs aws-ecs-fargate --since 24h --limit 50
portworld logs azure-container-apps --since 24h --limit 50
```

### Provider/config maintenance

```bash
portworld providers list
portworld config show
portworld config edit providers
portworld config edit cloud
```

---

## Repo development (`uv run` + module)

Use inside a PortWorld git checkout with `uv sync` (or equivalent) so `portworld_cli` resolves from source.

### Bootstrap (from skill directory)

`cd` to the skill root (directory containing `SKILL.md`), then:

```bash
bash scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode source
bash scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode published --stack-name default
```

### Local validation and inspection

```bash
uv run python -m portworld_cli.main doctor --target local
uv run python -m portworld_cli.main status
uv run python -m portworld_cli.main config show
```

### Managed readiness checks

```bash
uv run python -m portworld_cli.main doctor --target gcp-cloud-run --gcp-project <project> --gcp-region <region>
uv run python -m portworld_cli.main doctor --target aws-ecs-fargate --aws-region <region>
uv run python -m portworld_cli.main doctor --target azure-container-apps --azure-subscription <sub> --azure-resource-group <rg> --azure-region <region>
```

### Managed deploy commands

```bash
uv run python -m portworld_cli.main deploy gcp-cloud-run --project <project> --region <region>
uv run python -m portworld_cli.main deploy aws-ecs-fargate --region <region>
uv run python -m portworld_cli.main deploy azure-container-apps --subscription <sub> --resource-group <rg> --region <region>
```

### Logs

```bash
uv run python -m portworld_cli.main logs gcp-cloud-run --since 24h --limit 50
uv run python -m portworld_cli.main logs aws-ecs-fargate --since 24h --limit 50
uv run python -m portworld_cli.main logs azure-container-apps --since 24h --limit 50
```

### Provider/config maintenance

```bash
uv run python -m portworld_cli.main providers list
uv run python -m portworld_cli.main config show
uv run python -m portworld_cli.main config edit providers
uv run python -m portworld_cli.main config edit cloud
```
