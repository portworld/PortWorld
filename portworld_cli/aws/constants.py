from __future__ import annotations

COMMAND_NAME = "portworld deploy aws-ecs-fargate"

RDS_INSTANCE_CLASS = "db.t3.micro"
RDS_STORAGE_GB = "20"
RDS_PASSWORD_PARAM_PREFIX = "/portworld"

ECS_EXECUTION_ROLE_NAME = "portworld-ecs-task-execution"
ECS_TASK_ROLE_SUFFIX = "ecs-task-runtime"
ECS_TASK_INLINE_POLICY_NAME = "portworld-ecs-task-runtime-s3"
ECS_SERVICE_LINKED_ROLE_NAME = "AWSServiceRoleForECS"
ECS_TASK_CPU = "1024"
ECS_TASK_MEMORY = "2048"

MANAGED_CACHE_POLICY_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
MANAGED_ORIGIN_REQUEST_POLICY_ALL_VIEWER = "216adef6-5c7f-47e4-b989-5492eafa07d3"
