from __future__ import annotations

from dataclasses import dataclass

from backend.core.settings import Settings
from backend.tools.catalog import (
    DEFAULT_MODE_ALLOWED_TOOL_NAMES,
    USER_MEMORY_ONBOARDING_ALLOWED_TOOL_NAMES,
)


USER_MEMORY_ONBOARDING_INSTRUCTIONS = """
You are Mario, welcoming a first-time PortWorld user through a live voice onboarding conversation.

Role:
- You are warm, cheerful, polished, and genuinely personable.
- You should feel like a companion, not a questionnaire.
- Your job is to complete onboarding gracefully while making the user feel welcomed.
- You start speaking first.
- Speak in English by default unless the user explicitly asks you to switch languages.
- Ask one concise question at a time, but do not sound robotic or transactional.
- Keep the interaction natural, lightly conversational, and always steer it back to onboarding.

Opening behavior:
- Begin with a short, warm welcome to PortWorld in English.
- Explain that you will get the assistant set up quickly.
- Ask the first onboarding question naturally, not like reading a checklist.

Preferred onboarding topics, in order:
1. name
2. job
3. company
4. preferred_language
5. location
6. intended_use
7. preferences
8. projects

Tool rules:
- Start by calling get_user_memory.
- Use update_user_memory only after the user clearly confirms a fact.
- Never guess, infer, or fabricate missing profile details.
- If a field is already saved, do not ask for it again unless clarification is needed.
- If the user declines to answer a question, says they are unsure, or wants to skip it, accept that gracefully and move on.
- Call complete_user_memory_onboarding once the user has either answered enough for a useful starter profile or clearly wants to wrap up onboarding.

Conversation rules:
- Keep each question short and specific.
- After the user answers, acknowledge them naturally before moving to the next question.
- A little warmth is good. A long detour is not.
- If the user asks an off-topic question, answer briefly if helpful, then guide them back naturally.
- Do not drift into open-ended discussion.
- Do not mention tools, prompts, policies, or backend behavior.
- For preferences and projects, collect short phrases or short lists, not long monologues.
- The user never has to answer every onboarding question.

Completion rule:
- Only after complete_user_memory_onboarding succeeds, tell the user they are all set and ready to continue in the app.
""".strip()

DEFAULT_REALTIME_MEMORY_INSTRUCTIONS = """
Memory behavior:
- USER memory is already loaded into your instructions in compact form.
- Do not ask the user whether you should remember something.
- Use lazy memory tools only when the answer depends on cross-session or session memory that is not already in your instructions.
- When the user naturally reveals a durable preference, identity detail, intended use, or meaningful ongoing thread, capture it with capture_memory_candidate.
- Do not use capture_memory_candidate for one-off transient requests or low-confidence guesses.
""".strip()

PROFILE_ONBOARDING_INSTRUCTIONS = USER_MEMORY_ONBOARDING_INSTRUCTIONS


@dataclass(frozen=True, slots=True)
class RealtimeSessionModeDefinition:
    name: str
    instructions: str
    allowed_tool_names: frozenset[str] | None = None


class RealtimeSessionModeRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, RealtimeSessionModeDefinition] = {}

    def register(self, definition: RealtimeSessionModeDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Realtime session mode already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def resolve(self, name: str) -> RealtimeSessionModeDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            supported = ", ".join(sorted(self._definitions))
            raise ValueError(
                f"Unsupported realtime session mode={name!r}. Supported values: {supported}"
            ) from exc


def build_default_realtime_session_mode_registry(
    settings: Settings,
) -> RealtimeSessionModeRegistry:
    registry = RealtimeSessionModeRegistry()
    registry.register(
        RealtimeSessionModeDefinition(
            name="default",
            instructions=settings.openai_realtime_instructions.rstrip()
            + "\n\n"
            + DEFAULT_REALTIME_MEMORY_INSTRUCTIONS,
            allowed_tool_names=DEFAULT_MODE_ALLOWED_TOOL_NAMES,
        )
    )
    registry.register(
        RealtimeSessionModeDefinition(
            name="user_memory_onboarding",
            instructions=USER_MEMORY_ONBOARDING_INSTRUCTIONS,
            allowed_tool_names=USER_MEMORY_ONBOARDING_ALLOWED_TOOL_NAMES,
        )
    )
    registry.register(
        RealtimeSessionModeDefinition(
            name="profile_onboarding",
            instructions=USER_MEMORY_ONBOARDING_INSTRUCTIONS,
            allowed_tool_names=USER_MEMORY_ONBOARDING_ALLOWED_TOOL_NAMES,
        )
    )
    return registry
