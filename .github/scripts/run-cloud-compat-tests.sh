#!/usr/bin/env bash
set -euo pipefail

python -m pytest \
  tests/cli/core/test_portworld_cli_runtime_env_vars.py \
  tests/cli/core/test_portworld_cli_doctor_routing.py \
  tests/cli/aws/test_portworld_cli_aws_common.py \
  tests/cli/aws/test_portworld_cli_aws_doctor.py \
  tests/cli/aws/test_portworld_cli_aws_deploy.py \
  tests/cli/aws/test_portworld_cli_aws_runtime_env_vars.py \
  tests/cli/azure/test_portworld_cli_azure_doctor.py \
  tests/cli/azure/test_portworld_cli_azure_deploy.py \
  tests/cli/azure/test_portworld_cli_azure_runtime_env_vars.py \
  tests/cli/core/test_portworld_cli_targets.py \
  tests/cli/core/test_portworld_cli_project_config_v4.py \
  tests/cli/core/test_portworld_cli_deploy_state_targets.py \
  tests/cli/core/test_portworld_cli_status.py \
  tests/backend/test_backend_storage_settings.py \
  tests/backend/test_backend_object_store_factory.py
