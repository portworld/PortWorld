# Backend Cloud Provider Implementation Plan

## Summary

This document turns the current multi-cloud deployment direction into a concrete implementation sequence.

The goals of this plan are:

- keep the current GCP Cloud Run path working
- introduce a real provider-target deployment foundation in the public CLI
- generalize the managed storage contract so the backend runtime is not GCP-shaped
- add two new official managed deploy targets:
  - `aws-ecs-fargate`
  - `azure-container-apps`
- keep the backend Docker-first and self-hostable while making managed deploys more portable

This plan intentionally does **not** try to solve every cloud platform at once.
It focuses on the minimum architecture and target set needed to move from a GCP-heavy implementation to a serious multi-cloud platform.

## Locked Choices

- keep the current GCP target and command contract intact
- add AWS and Azure in the same implementation wave
- treat the first AWS target as `aws-ecs-fargate`
- treat the first Azure target as `azure-container-apps`
- keep this wave repo-backed and source-build capable; later published-image runtime modes remain a separate follow-up
- keep v1 infrastructure scope at the app layer rather than full landing-zone provisioning
- require custom-domain HTTPS for the first official AWS deploy path
- keep GCP logs and update-deploy behavior intact; defer AWS/Azure logs and update flows until after deploy parity lands

## Current Starting Point

The repo already has the right high-level split:

- backend runtime logic is mostly provider-neutral
- managed storage already exists behind a common `BackendStorage` contract
- the public CLI already has stable command surfaces for:
  - `portworld doctor --target local`
  - `portworld doctor --target gcp-cloud-run`
  - `portworld deploy gcp-cloud-run`
  - `portworld status`
  - `portworld providers ...`

The main portability blockers are:

- managed storage naming and validation are still GCP-shaped
- object storage supports only `gcs`
- project config supports only `gcp-cloud-run`
- deploy state paths are hard-coded around GCP
- the managed deploy runtime is one large GCP-specific orchestration path

## Implementation Principles

1. Preserve working GCP behavior while extracting reusable deploy structure.
2. Normalize backend runtime contracts before adding more provider adapters.
3. Separate image source from deployment target so later published-image workflows fit naturally.
4. Keep provider-specific resource orchestration inside provider adapter packages.
5. Limit the first multi-cloud wave to production-credible paths, not demo-only paths.
6. Prefer clear operator prerequisites over hidden “magic” provisioning of foundational infrastructure.

## Step-By-Step Plan

### Step 1: Generalize the managed storage runtime contract

Remove GCP-specific naming from backend runtime settings and storage bootstrap.

#### Work

- change `BACKEND_STORAGE_BACKEND` so `managed` is the canonical value
- keep `postgres_gcs` as a backward-compatible alias during migration
- expand `BACKEND_OBJECT_STORE_PROVIDER` to support:
  - `filesystem`
  - `gcs`
  - `s3`
  - `azure_blob`
- introduce neutral object-store env naming:
  - `BACKEND_OBJECT_STORE_NAME`
  - `BACKEND_OBJECT_STORE_ENDPOINT`
  - `BACKEND_OBJECT_STORE_PREFIX`
- keep `BACKEND_OBJECT_STORE_BUCKET` as a compatibility alias for existing GCP flows
- update settings validation so the runtime validates managed storage generically instead of assuming `postgres_gcs + gcs`
- keep Postgres metadata storage as the shared managed metadata layer

#### Backend files likely affected

- `backend/core/settings.py`
- `backend/bootstrap/runtime.py`
- `backend/.env.example`
- `backend/README.md`

#### Required result

- local runtime still uses `local + filesystem`
- managed runtime can start with `managed + gcs`
- managed runtime can also validate `managed + s3` and `managed + azure_blob`

### Step 2: Add managed object-store implementations for AWS and Azure

Make the storage layer actually support the new providers introduced in Step 1.

#### Work

- add an `S3ObjectStore` implementation
- add an `AzureBlobObjectStore` implementation
- extend `build_object_store()` to resolve all official providers
- define how credentials are obtained:
  - GCP via current ambient credentials path
  - AWS via task role or standard AWS credential chain
  - Azure via managed identity and `DefaultAzureCredential`
- keep all artifact path behavior and normalization identical across providers

#### Backend files likely affected

- `backend/infrastructure/storage/object_store.py`
- `backend/infrastructure/storage/gcs.py`
- new provider modules for S3 and Azure Blob

#### Required result

- profile artifacts, session artifacts, and vision artifacts remain addressable through one contract
- no backend caller outside storage needs provider-specific branching

### Step 3: Introduce a generic cloud-target contract in `portworld_cli`

Split the current GCP monolith into a reusable deployment model before adding AWS and Azure.

#### Work

- define a target adapter contract for:
  - doctor/readiness evaluation
  - deploy orchestration
  - deploy-state serialization
  - provider summary metadata
  - normalized final status output
- extract generic deploy concerns out of the current GCP runtime:
  - project-root and config discovery
  - env parsing
  - deploy-state read/write
  - feature summary generation
  - final human/JSON output structure
- keep provider-specific resource provisioning inside provider packages
- introduce an internal image-source abstraction now, but keep only the repo-backed source path live in this implementation wave

#### CLI files likely affected

- `portworld_cli/deploy_runtime.py`
- `portworld_cli/doctor_runtime.py`
- `portworld_cli/provider_catalog.py`
- `portworld_cli/deploy_state.py`
- `portworld_cli/paths.py`

#### Required result

- GCP still deploys through `portworld deploy gcp-cloud-run`
- AWS and Azure can be added without copying the entire deploy runtime shape

### Step 4: Refactor the current GCP path onto the new target contract

Make GCP the reference implementation of the new deploy-target architecture.

#### Work

- move current GCP-specific deploy logic behind the new provider-target interface
- keep target names, flags, and state file shape unchanged
- keep the current Cloud Build, Secret Manager, Cloud SQL, GCS, and Cloud Run flow intact
- keep current `doctor --target gcp-cloud-run`, `logs gcp-cloud-run`, and `providers show gcp` behavior intact

#### Required result

- no user-visible regression for existing GCP users
- the codebase has one obvious pattern for future provider targets

### Step 5: Expand project config and deploy-state models for multiple cloud targets

Teach the CLI to remember cloud defaults and deploy summaries for more than one provider.

#### Work

- extend `.portworld/project.json` with sibling cloud sections:
  - `deploy.gcp_cloud_run`
  - `deploy.aws_ecs_fargate`
  - `deploy.azure_container_apps`
- allow `cloud_provider` values:
  - `gcp`
  - `aws`
  - `azure`
- add generic state path helpers for `.portworld/state/<target>.json`
- keep `.portworld/state/gcp-cloud-run.json` valid and readable
- define the new state files:
  - `.portworld/state/aws-ecs-fargate.json`
  - `.portworld/state/azure-container-apps.json`

#### Required result

- config editing and status reporting can reason about multiple managed targets
- remembered deploy metadata is target-specific instead of GCP-specific

### Step 6: Add AWS provider catalog and doctor support

Introduce AWS as a first-class managed target before implementing deploy mutation.

#### Work

- add AWS provider metadata to `providers list/show`
- add `portworld doctor --target aws-ecs-fargate`
- implement `aws` CLI checks:
  - CLI installed
  - authenticated account available
  - region resolved
  - required account/service permissions are inspectable
- validate required project/deploy inputs:
  - VPC id
  - subnet ids
  - ECS cluster name or default
  - custom domain
  - ACM certificate ARN
- validate backend managed-storage readiness for AWS:
  - RDS PostgreSQL target shape
  - S3 bucket naming/input rules
  - Secrets Manager prerequisites

#### Required result

- AWS readiness can fail fast with specific remediation output before any resource mutation exists

### Step 7: Implement AWS deploy as `aws-ecs-fargate`

Add the first official AWS deployment path.

#### Work

- add `portworld deploy aws-ecs-fargate`
- use AWS CLI-backed adapters for:
  - ECR repository creation or reuse
  - image push path
  - ECS task execution and task role setup
  - ECS cluster/service deployment
  - ALB target group and HTTPS listener wiring
  - RDS PostgreSQL provisioning or reuse
  - S3 bucket provisioning or reuse
  - Secrets Manager secret creation and version updates
  - CloudWatch log group setup
- generate runtime env vars using the neutral managed-storage contract:
  - `BACKEND_STORAGE_BACKEND=managed`
  - `BACKEND_OBJECT_STORE_PROVIDER=s3`
  - object store name/prefix settings
- inject secrets rather than writing sensitive values into plain env vars where possible
- record deploy summary and state under `aws-ecs-fargate.json`

#### Explicit AWS v1 scope

- require existing VPC and subnet inputs
- require custom-domain HTTPS from day one
- optionally support Route53 alias automation when hosted zone input is available
- if DNS automation is unavailable, print a deterministic manual DNS step and still complete deploy

#### Required result

- the backend is reachable over HTTPS on AWS
- `/livez` succeeds
- `/ws/session` works through the deployed endpoint

### Step 8: Add Azure provider catalog and doctor support

Introduce Azure as a first-class managed target before implementing deploy mutation.

#### Work

- add Azure provider metadata to `providers list/show`
- add `portworld doctor --target azure-container-apps`
- implement `az` CLI checks:
  - CLI installed
  - authenticated account/subscription available
  - region resolved
  - resource group and target inputs validated
- validate backend managed-storage readiness for Azure:
  - PostgreSQL Flexible Server target shape
  - Storage Account and Blob container naming/input rules
  - Key Vault secret handling
  - managed identity expectations

#### Required result

- Azure readiness can fail fast with provider-specific remediation before deploy exists

### Step 9: Implement Azure deploy as `azure-container-apps`

Add the first official Azure deployment path.

#### Work

- add `portworld deploy azure-container-apps`
- use Azure CLI-backed adapters for:
  - resource group creation or reuse
  - ACR repository setup
  - image push path
  - Container Apps environment setup
  - Container App deployment with public ingress
  - PostgreSQL Flexible Server/database creation or reuse
  - Storage Account plus Blob container creation or reuse
  - Key Vault secret creation and updates
  - managed identity creation and binding
- generate runtime env vars using the neutral managed-storage contract:
  - `BACKEND_STORAGE_BACKEND=managed`
  - `BACKEND_OBJECT_STORE_PROVIDER=azure_blob`
  - object store name/endpoint/prefix settings
- record deploy summary and state under `azure-container-apps.json`

#### Explicit Azure v1 scope

- use the provider FQDN and built-in TLS endpoint
- defer custom domain support to a later phase
- prefer managed identity over static storage credentials

#### Required result

- the backend is reachable on the Azure-generated HTTPS endpoint
- `/livez` succeeds
- `/ws/session` works through the deployed endpoint

### Step 10: Normalize status output across GCP, AWS, and Azure

Make the new targets visible through the existing inspection surface.

#### Work

- extend `portworld status` so it can report:
  - current project mode
  - active target
  - latest known deploy URL
  - target-specific runtime summary
- normalize deploy-state reading across:
  - `gcp-cloud-run`
  - `aws-ecs-fargate`
  - `azure-container-apps`
- keep `status` state-first and read-only

#### Required result

- users can inspect the latest managed deployment without rerunning deploy
- GCP, AWS, and Azure all fit one human-readable and JSON status shape

### Step 11: Update config UX and docs for multi-cloud setup

Make the new provider surface visible and operable from the public CLI and docs.

#### Work

- update `portworld init` and `portworld config edit cloud` so users can select:
  - local
  - GCP Cloud Run
  - AWS ECS/Fargate
  - Azure Container Apps
- keep secrets out of `.portworld/project.json`
- document target-specific prerequisites and exact required inputs
- add a cloud-provider roadmap section in docs that explains:
  - current official targets
  - required CLIs
  - network prerequisites
  - storage model for each provider

#### Docs likely affected

- `backend/README.md`
- `docs/BACKEND_SELF_HOSTING.md`
- `docs/roadmap/backend/...` references to managed deploy targets

#### Required result

- the CLI and docs describe the same supported cloud-provider surface
- new users do not have to reverse-engineer provider prerequisites from code

### Step 12: Add compatibility tests and acceptance coverage

Lock the new architecture down before adding more providers.

#### Work

- add unit coverage for:
  - project config parsing with multiple cloud targets
  - deploy-state parsing for all target files
  - generic storage settings validation
  - object-store provider selection
- add CLI runtime tests for:
  - `doctor --target gcp-cloud-run`
  - `doctor --target aws-ecs-fargate`
  - `doctor --target azure-container-apps`
  - deploy config resolution precedence
- add user-run acceptance scenarios for each managed target
- build the backend after non-trivial changes

#### Required result

- existing GCP behavior is regression-protected
- new multi-cloud config and state logic is stable before logs/update parity work begins

## Acceptance Criteria

By the end of this implementation slice:

- GCP Cloud Run deploy still works with the current command contract
- the backend runtime supports `managed + gcs|s3|azure_blob`
- the public CLI supports:
  - `portworld doctor --target gcp-cloud-run`
  - `portworld doctor --target aws-ecs-fargate`
  - `portworld doctor --target azure-container-apps`
  - `portworld deploy gcp-cloud-run`
  - `portworld deploy aws-ecs-fargate`
  - `portworld deploy azure-container-apps`
- `portworld status` can summarize deploy state for all three targets
- AWS deploy produces a working HTTPS endpoint with custom-domain TLS
- Azure deploy produces a working HTTPS endpoint on the provider FQDN
- the backend keeps durable memory behavior on AWS and Azure via Postgres + object storage

## Explicitly Deferred

- broad multi-cloud parity beyond GCP, AWS, and Azure
- AWS/Azure `logs` commands in the first wave
- AWS/Azure `update deploy` behavior in the first wave
- zero-clone published-image runtime mode
- full network landing-zone creation from scratch
- teardown and rollback orchestration
- custom domain support for Azure in the first wave

## Implementation Notes

- The current backend runtime is in better shape than the current CLI deploy layer. Most of the work should happen in `portworld_cli`, not in websocket/session code.
- The managed storage contract should be generalized before AWS and Azure deploy adapters land. Otherwise provider deploy code will need to keep translating back into GCP-shaped runtime settings.
- AWS uses ECS/Fargate rather than App Runner in this plan because this path is a better fit for the backend’s long-lived WebSocket traffic and gives clearer control over HTTPS, ALB, IAM, logging, and runtime shape.
- Azure uses Container Apps because it offers the closest operator experience to the current Cloud Run path while still supporting public HTTPS ingress and managed service composition.

## References Checked

- AWS Application Load Balancer listeners and WebSocket support:
  - <https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html>
- AWS ECS secrets via Secrets Manager:
  - <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html>
- AWS ECS task IAM roles:
  - <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html>
- AWS ECS `awslogs` logging:
  - <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_awslogs.html>
- Azure Container Apps ingress and WebSockets/FQDN behavior:
  - <https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview>
- Azure Container Apps secrets and Key Vault references:
  - <https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets>
- Azure identity and `DefaultAzureCredential` overview:
  - <https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/overview>
- Azure Database for PostgreSQL Flexible Server overview:
  - <https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/overview>
