# Backend Cloud Provider Implementation Plan

## Summary

This plan replaces the prior cloud-provider roadmap with a phased implementation that matches the current codebase reality.

Goals:

- preserve existing `gcp-cloud-run` behavior and command contracts
- introduce reusable multi-target deployment architecture in `portworld_cli`
- normalize backend managed-storage runtime contracts so they are not GCP-shaped
- add two additional managed targets:
  - `aws-ecs-fargate`
  - `azure-container-apps`
- keep deployments Docker-first and self-hostable while improving managed portability

Sequencing is intentionally phased to reduce regression risk:

1. foundation abstraction + GCP parity refactor
2. AWS support
3. Azure support
4. cross-target UX and documentation parity

## Locked Choices

- keep existing GCP target names and command behavior stable
- rollout model is phased, not a single large implementation wave
- first AWS target is `aws-ecs-fargate`
- first Azure target is `azure-container-apps`
- keep repo-backed source build support in scope; zero-clone published-image managed deploy remains out of scope
- infrastructure scope is app-layer provisioning, not full landing-zone bootstrap
- AWS v1 acceptance uses ALB HTTPS endpoint first; custom-domain TLS is a follow-up milestone
- Azure v1 uses provider FQDN HTTPS endpoint
- keep GCP `logs` and `update deploy` behavior intact; defer AWS/Azure parity for those commands

## Current Reality (Grounded in Code)

- CLI deploy/doctor/status/config/state are currently single-target GCP (`gcp-cloud-run`).
- deploy runtime is still a GCP monolith centered on `run_deploy_gcp_cloud_run`.
- workspace paths and deploy-state plumbing are hard-wired to `.portworld/state/gcp-cloud-run.json`.
- project config currently models only `deploy.gcp_cloud_run` and GCP cloud-provider enums.
- backend managed storage contract still assumes `postgres_gcs + gcs` and object store factory only resolves `gcs`.

Implication: architecture generalization must land before AWS/Azure deploy adapters.

## Implementation Principles

1. Preserve working GCP behavior while extracting reusable structures.
2. Normalize runtime contracts before adding provider-specific deploy mutation.
3. Keep provider-specific provisioning in provider adapters.
4. Keep explicit operator prerequisites over hidden foundational magic.
5. Keep acceptance criteria objective and target-specific.

## Phase 1: Foundation (Target Abstraction + GCP Parity)

### Scope

- define internal cloud-target adapter interfaces for deploy, doctor, state, and provider metadata
- split GCP deploy monolith into reusable orchestration + GCP target implementation
- generalize config/state pathing from single hard-coded target to target-based helpers
- keep all GCP command surfaces stable

### Required interface and model deltas

- extend project config model to support cloud provider enum values `gcp|aws|azure`
- extend deploy config shape to include sibling target configs:
  - `deploy.gcp_cloud_run`
  - `deploy.aws_ecs_fargate`
  - `deploy.azure_container_apps`
- generalize state path helpers to `.portworld/state/<target>.json`
- keep `.portworld/state/gcp-cloud-run.json` fully readable/writable for backward compatibility
- introduce target registry used by deploy/doctor/providers/status dispatch

### Required outcomes

- `portworld deploy gcp-cloud-run` remains behaviorally equivalent
- `portworld doctor --target gcp-cloud-run` remains behaviorally equivalent
- status/config/session loading can address target-specific state without GCP-only branching

## Phase 2: Backend Managed-Storage Contract Normalization

### Scope

- canonicalize managed backend storage naming:
  - `BACKEND_STORAGE_BACKEND=managed` (canonical)
  - `postgres_gcs` retained as backward-compatible alias
- expand supported object-store providers:
  - `filesystem`
  - `gcs`
  - `s3`
  - `azure_blob`
- introduce neutral managed object-store env shape:
  - `BACKEND_OBJECT_STORE_NAME`
  - `BACKEND_OBJECT_STORE_ENDPOINT`
  - `BACKEND_OBJECT_STORE_PREFIX`
- keep `BACKEND_OBJECT_STORE_BUCKET` as compatibility alias

### Storage implementation scope

- keep existing `ObjectStore` contract
- add provider implementations:
  - `S3ObjectStore`
  - `AzureBlobObjectStore`
- update object-store factory dispatch for `gcs|s3|azure_blob`
- keep artifact path normalization behavior identical across providers

### Credential assumptions

- GCP: ambient ADC path (existing)
- AWS: task role or standard AWS credential chain
- Azure: managed identity / `DefaultAzureCredential`

### Required outcomes

- runtime validates:
  - `local + filesystem`
  - `managed + gcs`
  - `managed + s3`
  - `managed + azure_blob`
- no backend caller outside storage needs provider-specific branches

## Phase 3: AWS Target (`aws-ecs-fargate`)

### Doctor and provider surface

- add provider catalog metadata for AWS
- add `portworld doctor --target aws-ecs-fargate`
- doctor validates:
  - AWS CLI presence and authenticated account
  - region resolution
  - deploy inputs (VPC, subnets, cluster/service inputs)
  - managed storage prerequisites (RDS PostgreSQL shape, S3 naming constraints, Secrets Manager readiness)

### Deploy scope

- add `portworld deploy aws-ecs-fargate`
- adapter responsibilities:
  - ECR repository create/reuse
  - image push
  - ECS task execution role + task role
  - ECS service deployment
  - ALB target group + HTTPS listener
  - RDS PostgreSQL provisioning/reuse
  - S3 bucket provisioning/reuse
  - Secrets Manager secret creation/version updates
  - CloudWatch logs setup
- runtime env uses normalized managed contract (`managed + s3`)

### AWS v1 acceptance

- backend reachable via ALB HTTPS endpoint
- `/livez` and `/ws/session` pass through deployed endpoint
- custom domain and Route53 automation are explicitly deferred

## Phase 4: Azure Target (`azure-container-apps`)

### Doctor and provider surface

- add provider catalog metadata for Azure
- add `portworld doctor --target azure-container-apps`
- doctor validates:
  - Azure CLI presence and authenticated subscription
  - region/resource group inputs
  - managed storage prerequisites (PostgreSQL Flexible Server, Blob, Key Vault/identity prerequisites)

### Deploy scope

- add `portworld deploy azure-container-apps`
- adapter responsibilities:
  - resource group create/reuse
  - ACR setup
  - image push
  - Container Apps environment + app deployment with ingress
  - PostgreSQL Flexible Server/database provisioning/reuse
  - Storage Account + Blob container provisioning/reuse
  - Key Vault secret setup
  - managed identity setup/binding
- runtime env uses normalized managed contract (`managed + azure_blob`)

### Azure v1 acceptance

- backend reachable via Azure provider FQDN HTTPS endpoint
- `/livez` and `/ws/session` pass through deployed endpoint
- custom domain support deferred

## Phase 5: Cross-Target UX, Docs, and Contract Tests

### UX and docs

- `portworld status` reads target-specific state and stays read-only
- `portworld init` / `portworld config edit cloud` expose all supported managed targets
- keep secrets out of `.portworld/project.json`
- align operator docs with actual CLI support and prerequisites

### Contract tests

- config parsing and migration tests for multi-target project schema
- target-specific deploy-state serialization tests
- storage validation tests for `managed + gcs|s3|azure_blob`
- CLI command tests for:
  - `doctor --target gcp-cloud-run`
  - `doctor --target aws-ecs-fargate`
  - `doctor --target azure-container-apps`

#### Required result

- the CLI and docs describe the same supported cloud-provider surface
- new users do not have to reverse-engineer provider prerequisites from code

## Acceptance Criteria by Phase

### Phase 1

- no user-visible GCP regression for deploy/doctor/status/config paths
- target abstraction exists and is used by GCP implementation

### Phase 2

- backend runtime accepts neutral managed storage settings across GCP/AWS/Azure providers
- compatibility aliases keep current GCP deploys valid

### Phase 3

- AWS doctor + deploy commands available and functional
- successful deployed endpoint passes `/livez` and `/ws/session`

### Phase 4

- Azure doctor + deploy commands available and functional
- successful deployed endpoint passes `/livez` and `/ws/session`

### Phase 5

- status/config UX works consistently for all managed targets
- docs and CLI behavior are aligned

## Explicitly Deferred

- managed targets beyond GCP, AWS, Azure
- AWS/Azure `logs` command parity in this slice
- AWS/Azure `update deploy` parity in this slice
- zero-clone published-image managed deploy mode
- full landing-zone/network bootstrap from scratch
- teardown and rollback orchestration
- AWS custom-domain automation in v1
- Azure custom-domain support in v1

## Assumptions

1. Existing GCP users require strict compatibility for current config/env/state semantics.
2. First AWS/Azure release remains CLI-driven app-layer provisioning, not full platform automation.

## References Checked

- AWS ALB listeners and protocol behavior:
  - <https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html>
- AWS ECS secrets via Secrets Manager:
  - <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html>
- AWS ECS task IAM roles:
  - <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html>
- AWS ECS logs (`awslogs`):
  - <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_awslogs.html>
- S3 bucket naming rules:
  - <https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html>
- Azure Container Apps ingress/FQDN:
  - <https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview>
- Azure Container Apps secrets:
  - <https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets>
- Azure Container Apps managed identity:
  - <https://learn.microsoft.com/en-us/azure/container-apps/managed-identity>
- Azure Database for PostgreSQL Flexible Server:
  - <https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/overview>
- Azure Storage account naming rules:
  - <https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules#microsoftstorage>
