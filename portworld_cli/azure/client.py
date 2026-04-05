from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.azure.executor import AzureExecutor
from portworld_cli.azure.types import AzureCommandResult


@dataclass(frozen=True, slots=True)
class AzureImageAdapter:
    executor: AzureExecutor

    def run_json(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AzureStorageAdapter:
    executor: AzureExecutor

    def run_json(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AzureNetworkAdapter:
    executor: AzureExecutor

    def run_json(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_json(args)


@dataclass(frozen=True, slots=True)
class AzureDatabaseAdapter:
    executor: AzureExecutor

    def run_json(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_json(args)


@dataclass(frozen=True, slots=True)
class AzureComputeAdapter:
    executor: AzureExecutor

    def run_json(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AzureLoggingAdapter:
    executor: AzureExecutor

    def run_json(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_json(args)

    def run_text(self, args: list[str]) -> AzureCommandResult:
        return self.executor.run_text(args)


@dataclass(frozen=True, slots=True)
class AzureAdapters:
    executor: AzureExecutor
    image: AzureImageAdapter
    storage: AzureStorageAdapter
    network: AzureNetworkAdapter
    database: AzureDatabaseAdapter
    compute: AzureComputeAdapter
    logging: AzureLoggingAdapter

    @classmethod
    def create(cls, *, executor: AzureExecutor | None = None) -> "AzureAdapters":
        resolved_executor = executor or AzureExecutor()
        return cls(
            executor=resolved_executor,
            image=AzureImageAdapter(resolved_executor),
            storage=AzureStorageAdapter(resolved_executor),
            network=AzureNetworkAdapter(resolved_executor),
            database=AzureDatabaseAdapter(resolved_executor),
            compute=AzureComputeAdapter(resolved_executor),
            logging=AzureLoggingAdapter(resolved_executor),
        )
