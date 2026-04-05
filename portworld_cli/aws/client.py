from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.aws.executor import AWSExecutor
from portworld_cli.aws.types import AWSCommandResult


@dataclass(frozen=True, slots=True)
class AWSStorageAdapter:
    executor: AWSExecutor

    def run_json(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AWSImageAdapter:
    executor: AWSExecutor

    def run_json(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AWSNetworkAdapter:
    executor: AWSExecutor

    def run_json(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_json(args)


@dataclass(frozen=True, slots=True)
class AWSDatabaseAdapter:
    executor: AWSExecutor

    def run_json(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AWSComputeAdapter:
    executor: AWSExecutor

    def run_json(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_json(args)


@dataclass(frozen=True, slots=True)
class AWSLoggingAdapter:
    executor: AWSExecutor

    def run_json(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AWSCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AWSAdapters:
    executor: AWSExecutor
    storage: AWSStorageAdapter
    image: AWSImageAdapter
    network: AWSNetworkAdapter
    database: AWSDatabaseAdapter
    compute: AWSComputeAdapter
    logging: AWSLoggingAdapter

    @classmethod
    def create(cls, *, executor: AWSExecutor | None = None) -> "AWSAdapters":
        resolved_executor = executor or AWSExecutor()
        return cls(
            executor=resolved_executor,
            storage=AWSStorageAdapter(resolved_executor),
            image=AWSImageAdapter(resolved_executor),
            network=AWSNetworkAdapter(resolved_executor),
            database=AWSDatabaseAdapter(resolved_executor),
            compute=AWSComputeAdapter(resolved_executor),
            logging=AWSLoggingAdapter(resolved_executor),
        )
