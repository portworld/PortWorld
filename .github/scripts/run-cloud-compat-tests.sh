#!/usr/bin/env bash
set -euo pipefail

python -m unittest \
  tests.test_portworld_cli_runtime_env_vars \
  tests.test_portworld_cli_doctor_routing \
  tests.test_portworld_cli_aws_common \
  tests.test_portworld_cli_aws_doctor \
  tests.test_portworld_cli_aws_deploy \
  tests.test_portworld_cli_aws_runtime_env_vars \
  tests.test_portworld_cli_azure_doctor \
  tests.test_portworld_cli_azure_deploy \
  tests.test_portworld_cli_azure_runtime_env_vars \
  tests.test_portworld_cli_targets \
  tests.test_portworld_cli_project_config_v4 \
  tests.test_portworld_cli_deploy_state_targets \
  tests.test_portworld_cli_status \
  tests.test_backend_storage_settings \
  tests.test_backend_object_store_factory
