from __future__ import annotations

from pathlib import Path
import subprocess

from portworld_cli.aws.common import run_aws_json, run_aws_text
from portworld_cli.aws.stages.config import ResolvedAWSDeployConfig
from portworld_cli.aws.stages.shared import stage_ok, to_json_argument
from portworld_cli.deploy.config import DeployStageError


def ensure_s3_bucket(config: ResolvedAWSDeployConfig, *, stage_records: list[dict[str, object]]) -> None:
    head = run_aws_text(["s3api", "head-bucket", "--bucket", config.bucket_name, "--region", config.region])
    if head.ok:
        stage_records.append(stage_ok("s3_bucket", f"S3 bucket `{config.bucket_name}` is ready."))
        return

    lowered = (head.message or "").lower()
    if "forbidden" in lowered:
        raise DeployStageError(
            stage="s3_bucket",
            message=(
                f"S3 bucket `{config.bucket_name}` exists but is not accessible with current AWS credentials."
            ),
            action="Choose another --bucket value or fix S3 permissions.",
        )

    if "notfound" in lowered or "404" in lowered or "not found" in lowered:
        args = ["s3api", "create-bucket", "--bucket", config.bucket_name, "--region", config.region]
        if config.region != "us-east-1":
            args.extend(
                [
                    "--create-bucket-configuration",
                    to_json_argument({"LocationConstraint": config.region}),
                ]
            )
        created = run_aws_json(args)
        if not created.ok:
            raise DeployStageError(
                stage="s3_bucket",
                message=created.message or "Unable to create S3 bucket.",
                action="Verify permissions for s3:CreateBucket and retry.",
            )
        stage_records.append(stage_ok("s3_bucket", f"Created S3 bucket `{config.bucket_name}`."))
        return

    raise DeployStageError(
        stage="s3_bucket",
        message=head.message or "Unable to inspect S3 bucket.",
        action="Verify S3 permissions and bucket naming.",
    )


def ensure_ecr_repository(config: ResolvedAWSDeployConfig, *, stage_records: list[dict[str, object]]) -> None:
    describe = run_aws_json(
        [
            "ecr",
            "describe-repositories",
            "--region",
            config.region,
            "--repository-names",
            config.ecr_repository,
        ]
    )
    if describe.ok:
        stage_records.append(stage_ok("ecr_repository", f"ECR repository `{config.ecr_repository}` is ready."))
        return

    message = (describe.message or "").lower()
    if "repositorynotfoundexception" not in message and "not found" not in message:
        raise DeployStageError(
            stage="ecr_repository",
            message=describe.message or "Unable to inspect ECR repository.",
            action="Verify ecr:DescribeRepositories permissions and retry.",
        )

    created = run_aws_json(
        [
            "ecr",
            "create-repository",
            "--region",
            config.region,
            "--repository-name",
            config.ecr_repository,
        ]
    )
    if not created.ok:
        raise DeployStageError(
            stage="ecr_repository",
            message=created.message or "Unable to create ECR repository.",
            action="Ensure ecr:CreateRepository permission or pre-create repository.",
        )
    stage_records.append(stage_ok("ecr_repository", f"Created ECR repository `{config.ecr_repository}`."))


def docker_login_to_ecr(config: ResolvedAWSDeployConfig, *, stage_records: list[dict[str, object]]) -> None:
    login = run_aws_text(["ecr", "get-login-password", "--region", config.region])
    if not login.ok or not isinstance(login.value, str) or not login.value:
        raise DeployStageError(
            stage="docker_login",
            message=login.message or "Unable to fetch ECR docker login password.",
            action="Verify AWS auth and ECR permissions.",
        )

    registry = f"{config.account_id}.dkr.ecr.{config.region}.amazonaws.com"
    completed = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", registry],
        input=login.value,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeployStageError(
            stage="docker_login",
            message=(completed.stderr or completed.stdout).strip() or "docker login to ECR failed.",
            action="Ensure Docker is running and ECR auth is configured.",
        )
    stage_records.append(stage_ok("docker_login", f"Logged into ECR registry `{registry}`."))


def build_and_push_image(
    config: ResolvedAWSDeployConfig,
    *,
    stage_records: list[dict[str, object]],
    project_root: Path,
) -> None:
    completed = subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/amd64",
            "-f",
            "backend/Dockerfile",
            "-t",
            config.image_uri,
            "--push",
            ".",
        ],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DeployStageError(
            stage="publish_image",
            message=(completed.stderr or completed.stdout).strip() or "docker buildx build --push failed.",
            action="Verify Docker buildx is available and registry push permissions are granted.",
        )
    stage_records.append(stage_ok("publish_image", f"Built and pushed `{config.image_uri}`."))
