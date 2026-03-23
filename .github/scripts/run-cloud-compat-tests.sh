#!/usr/bin/env bash
set -euo pipefail

python -m unittest \
  tests.cli.core.test_portworld_cli_runtime_env_vars \
  tests.cli.core.test_portworld_cli_doctor_routing \
  tests.cli.aws.test_portworld_cli_aws_common \
  tests.cli.aws.test_portworld_cli_aws_doctor \
  tests.cli.aws.test_portworld_cli_aws_deploy \
  tests.cli.aws.test_portworld_cli_aws_runtime_env_vars \
  tests.cli.azure.test_portworld_cli_azure_doctor \
  tests.cli.azure.test_portworld_cli_azure_deploy \
  tests.cli.azure.test_portworld_cli_azure_runtime_env_vars \
  tests.cli.core.test_portworld_cli_targets \
  tests.cli.core.test_portworld_cli_project_config_v4 \
  tests.cli.core.test_portworld_cli_deploy_state_targets \
  tests.cli.core.test_portworld_cli_status \
  tests.backend.test_backend_storage_settings \
  tests.backend.test_backend_object_store_factory
