from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Mapping

from dotenv import dotenv_values


ENV_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
CUSTOM_OVERRIDES_HEADER = "# Custom overrides"


class EnvTemplateError(RuntimeError):
    """Raised when the canonical PortWorld env template is invalid."""


class EnvFileParseError(RuntimeError):
    """Raised when an env file cannot be parsed as expected."""


@dataclass(frozen=True, slots=True)
class EnvTemplateLine:
    raw: str
    key: str | None = None


@dataclass(frozen=True, slots=True)
class EnvTemplate:
    source_path: Path
    lines: tuple[EnvTemplateLine, ...]
    ordered_keys: tuple[str, ...]
    default_values: OrderedDict[str, str]
    default_raw_values: dict[str, str]

    def defaults(self) -> OrderedDict[str, str]:
        return OrderedDict(self.default_values.items())

    def render(
        self,
        *,
        values: Mapping[str, str],
        custom_overrides: Mapping[str, str] | None = None,
    ) -> str:
        rendered_lines: list[str] = []
        for template_line in self.lines:
            if template_line.key is None:
                rendered_lines.append(template_line.raw)
                continue
            key = template_line.key
            raw_value = self._render_known_value(key=key, value=values[key])
            rendered_lines.append(f"{key}={raw_value}")

        if custom_overrides:
            rendered_lines.extend(["", CUSTOM_OVERRIDES_HEADER])
            for key, value in custom_overrides.items():
                rendered_lines.append(f"{key}={serialize_env_value(value)}")

        return "\n".join(rendered_lines) + "\n"

    def _render_known_value(self, *, key: str, value: str) -> str:
        default_value = self.default_values[key]
        if value == default_value:
            return self.default_raw_values[key]
        return serialize_env_value(value)


@dataclass(frozen=True, slots=True)
class ParsedEnvFile:
    path: Path
    known_values: OrderedDict[str, str]
    custom_overrides: OrderedDict[str, str]
    preserved_overrides: OrderedDict[str, str]


@dataclass(frozen=True, slots=True)
class CanonicalEnvPlan:
    values: OrderedDict[str, str]
    custom_overrides: OrderedDict[str, str]
    content: str


@dataclass(frozen=True, slots=True)
class EnvWriteResult:
    env_path: Path
    backup_path: Path | None
    content: str


def load_env_template(path: Path) -> EnvTemplate:
    raw_text = path.read_text(encoding="utf-8")
    return load_env_template_text(path, raw_text)


def load_env_template_text(path: Path, raw_text: str) -> EnvTemplate:
    lines: list[EnvTemplateLine] = []
    ordered_keys: list[str] = []
    default_values: OrderedDict[str, str] = OrderedDict()
    default_raw_values: dict[str, str] = {}

    for raw_line in raw_text.splitlines():
        key, raw_value = _parse_assignment_line(raw_line)
        if key is None:
            lines.append(EnvTemplateLine(raw=raw_line))
            continue

        if key in default_values:
            raise EnvTemplateError(f"Duplicate key in env template: {key}")

        lines.append(EnvTemplateLine(raw=raw_line, key=key))
        ordered_keys.append(key)
        default_values[key] = parse_raw_env_value(raw_value)
        default_raw_values[key] = raw_value

    return EnvTemplate(
        source_path=path,
        lines=tuple(lines),
        ordered_keys=tuple(ordered_keys),
        default_values=default_values,
        default_raw_values=default_raw_values,
    )


def parse_env_file(path: Path, *, template: EnvTemplate) -> ParsedEnvFile:
    if not path.exists():
        return ParsedEnvFile(
            path=path,
            known_values=OrderedDict(),
            custom_overrides=OrderedDict(),
            preserved_overrides=OrderedDict(),
        )

    parsed_items = dotenv_values(path)
    known_values: OrderedDict[str, str] = OrderedDict()
    custom_overrides: OrderedDict[str, str] = OrderedDict()
    preserved_overrides: OrderedDict[str, str] = OrderedDict()

    for key, raw_value in parsed_items.items():
        if key is None:
            continue
        value = "" if raw_value is None else str(raw_value)
        if key in template.default_values:
            known_values[key] = value
            continue
        custom_overrides[key] = value
        preserved_overrides[key] = value

    return ParsedEnvFile(
        path=path,
        known_values=known_values,
        custom_overrides=custom_overrides,
        preserved_overrides=preserved_overrides,
    )


def build_canonical_env_plan(
    *,
    template: EnvTemplate,
    existing_env: ParsedEnvFile | None = None,
    overrides: Mapping[str, str | None] | None = None,
    custom_overrides: Mapping[str, str | None] | None = None,
) -> CanonicalEnvPlan:
    values = template.defaults()
    if existing_env is not None:
        values.update(existing_env.known_values)

    normalized_overrides = _normalize_mapping(overrides)
    for key, value in normalized_overrides.items():
        if key not in template.default_values:
            raise EnvFileParseError(f"Unknown PortWorld env key override: {key}")
        values[key] = value

    final_custom_overrides: OrderedDict[str, str] = OrderedDict()
    if existing_env is not None:
        final_custom_overrides.update(existing_env.preserved_overrides)
    for key, value in _normalize_mapping(custom_overrides).items():
        final_custom_overrides[key] = value

    content = template.render(values=values, custom_overrides=final_custom_overrides)
    return CanonicalEnvPlan(
        values=values,
        custom_overrides=final_custom_overrides,
        content=content,
    )


def write_canonical_env(
    env_path: Path,
    *,
    template: EnvTemplate,
    existing_env: ParsedEnvFile | None = None,
    overrides: Mapping[str, str | None] | None = None,
    custom_overrides: Mapping[str, str | None] | None = None,
) -> EnvWriteResult:
    plan = build_canonical_env_plan(
        template=template,
        existing_env=existing_env,
        overrides=overrides,
        custom_overrides=custom_overrides,
    )

    env_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if env_path.exists():
        backup_path = env_path.with_name(f"{env_path.name}.bak.{_now_ms()}")
        shutil.copy2(env_path, backup_path)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{env_path.name}.", suffix=".tmp", dir=env_path.parent)
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(plan.content)
        tmp_path.replace(env_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return EnvWriteResult(
        env_path=env_path,
        backup_path=backup_path,
        content=plan.content,
    )


def serialize_env_value(value: str) -> str:
    if value == "":
        return ""
    if _can_emit_unquoted(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def parse_raw_env_value(raw_value: str) -> str:
    candidate = raw_value.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
        quote = candidate[0]
        inner = candidate[1:-1]
        if quote == '"':
            return _unescape_double_quoted(inner)
        return inner
    return raw_value


def _parse_assignment_line(raw_line: str) -> tuple[str | None, str | None]:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None, None
    match = ENV_ASSIGNMENT_RE.match(raw_line)
    if match is None:
        return None, None
    return match.group(1), match.group(2)


def _normalize_mapping(mapping: Mapping[str, str | None] | None) -> OrderedDict[str, str]:
    normalized: OrderedDict[str, str] = OrderedDict()
    if mapping is None:
        return normalized
    for key, value in mapping.items():
        normalized[key] = "" if value is None else str(value)
    return normalized


def _unescape_double_quoted(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _can_emit_unquoted(value: str) -> bool:
    if value.strip() != value:
        return False
    if any(ch in value for ch in ('\n', '#', '"', "'")):
        return False
    return True


def _now_ms() -> int:
    return int(time.time() * 1000)
