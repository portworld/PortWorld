from __future__ import annotations

import secrets
from urllib.parse import quote

from portworld_cli.azure.client import AzureAdapters
from portworld_cli.azure.common import is_postgres_url, read_dict_string
from portworld_cli.azure.stages.config import ResolvedAzureDeployConfig
from portworld_cli.azure.stages.shared import stage_ok, to_azure_secret_name
from portworld_cli.deploy.config import DeployStageError


def ensure_postgres_and_database_url(
    config: ResolvedAzureDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    adapters: AzureAdapters,
) -> str:
    if config.database_url is not None:
        stage_records.append(stage_ok("postgres_provision", "Using explicit BACKEND_DATABASE_URL value."))
        return config.database_url

    existing_secret_url = resolve_database_url_from_container_app_secret(config, adapters=adapters)
    if existing_secret_url is not None:
        stage_records.append(stage_ok("postgres_provision", "Using existing database URL from Container App secret."))
        return existing_secret_url

    admin_password = generate_database_password()
    server = adapters.database.run_json(
        [
            "postgres",
            "flexible-server",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.postgres_server_name,
        ]
    )
    fqdn: str | None = None
    if server.ok and isinstance(server.value, dict):
        fqdn = read_dict_string(server.value, "fullyQualifiedDomainName")
        update_password = adapters.database.run_json(
            [
                "postgres",
                "flexible-server",
                "update",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--name",
                config.postgres_server_name,
                "--admin-password",
                admin_password,
            ]
        )
        if not update_password.ok:
            raise DeployStageError(
                stage="postgres_provision",
                message=update_password.message or "Unable to rotate PostgreSQL admin password.",
                action="Provide --database-url or grant permissions to update postgres server credentials.",
            )
    else:
        create = adapters.database.run_json(
            [
                "postgres",
                "flexible-server",
                "create",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--name",
                config.postgres_server_name,
                "--location",
                config.region,
                "--tier",
                "Burstable",
                "--sku-name",
                "Standard_B1ms",
                "--storage-size",
                "32",
                "--version",
                "16",
                "--admin-user",
                config.postgres_admin_username,
                "--admin-password",
                admin_password,
                "--public-access",
                "0.0.0.0",
            ]
        )
        if not create.ok or not isinstance(create.value, dict):
            raise DeployStageError(
                stage="postgres_provision",
                message=create.message or "Unable to create Azure Database for PostgreSQL flexible server.",
                action="Verify postgres provider registration, region availability, and permissions.",
            )
        fqdn = read_dict_string(create.value, "fullyQualifiedDomainName")

    if fqdn is None:
        raise DeployStageError(
            stage="postgres_provision",
            message="Unable to resolve PostgreSQL server FQDN.",
            action="Inspect flexible server status and retry deploy.",
        )

    db = adapters.database.run_json(
        [
            "postgres",
            "flexible-server",
            "db",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--server-name",
            config.postgres_server_name,
            "--database-name",
            config.postgres_database_name,
        ]
    )
    if not db.ok:
        create_db = adapters.database.run_json(
            [
                "postgres",
                "flexible-server",
                "db",
                "create",
                "--subscription",
                config.subscription_id,
                "--resource-group",
                config.resource_group,
                "--server-name",
                config.postgres_server_name,
                "--database-name",
                config.postgres_database_name,
            ]
        )
        if not create_db.ok:
            raise DeployStageError(
                stage="postgres_provision",
                message=create_db.message or "Unable to create PostgreSQL database.",
                action="Verify database name constraints and postgres server health.",
            )

    encoded_password = quote(admin_password, safe="")
    database_url = (
        f"postgresql://{config.postgres_admin_username}:{encoded_password}@{fqdn}:5432/"
        f"{config.postgres_database_name}?sslmode=require"
    )
    stage_records.append(
        stage_ok(
            "postgres_provision",
            f"Provisioned PostgreSQL `{config.postgres_server_name}` and database `{config.postgres_database_name}`.",
        )
    )
    return database_url


def resolve_database_url_from_container_app_secret(
    config: ResolvedAzureDeployConfig,
    *,
    adapters: AzureAdapters,
) -> str | None:
    secret_name = to_azure_secret_name("BACKEND_DATABASE_URL")
    secret = adapters.compute.run_json(
        [
            "containerapp",
            "secret",
            "show",
            "--subscription",
            config.subscription_id,
            "--resource-group",
            config.resource_group,
            "--name",
            config.app_name,
            "--secret-name",
            secret_name,
        ]
    )
    if not secret.ok or not isinstance(secret.value, dict):
        return None
    value = read_dict_string(secret.value, "value")
    if value is None or not is_postgres_url(value):
        return None
    return value


def generate_database_password() -> str:
    return f"Pw-{secrets.token_urlsafe(24)}!"
