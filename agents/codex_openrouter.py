"""Codex with the full OpenRouter model slug kept (Harbor's Codex adapter keeps only the last path segment).

  harbor run ... -a agents.codex_openrouter:CodexOpenRouter -m openai/deepseek/deepseek-v4.1-flash-1017 \
      --ak config=$PWD/agents/codex_deepseek.toml --ak reasoning_effort=high \
      --ae OPENAI_API_KEY=$OPENROUTER_API_KEY --ae OPENAI_BASE_URL=https://openrouter.ai/api/v1
  The config declares the model in a catalog (uploaded here) so Codex uses its real 1M window.
"""
import shlex
from pathlib import Path
from typing import override

from harbor.agents.installed.codex import Codex

_PLACEHOLDER = "kwb-model-placeholder"
CATALOG = Path(__file__).with_name("codex_models_deepseek.json")
REMOTE_CATALOG = "/tmp/codex-models/codex_models_deepseek.json"


class CodexOpenRouter(Codex):
    """Codex adapter that passes `provider/model` slugs through to --model."""

    @staticmethod
    @override
    def name() -> str:
        return "codex-openrouter"

    @override
    async def run(self, instruction, environment, context):
        await environment.exec(command="mkdir -p /tmp/codex-models && chmod 755 /tmp/codex-models")
        await environment.upload_file(CATALOG, REMOTE_CATALOG)
        await environment.exec(command=f"chmod 644 {REMOTE_CATALOG}")
        full = self.model_name.split("/", 1)[1] if self.model_name.startswith("openai/") else self.model_name
        self._full_model, self.model_name = full, f"openai/{_PLACEHOLDER}"
        try:
            return await super().run(instruction, environment, context)
        finally:
            self.model_name, self._full_model = f"openai/{full}", None

    @override
    async def exec_as_agent(self, environment, command, **kw):
        if getattr(self, "_full_model", None):
            command = command.replace(f"--model {_PLACEHOLDER} ", f"--model {shlex.quote(self._full_model)} ")
        return await super().exec_as_agent(environment, command, **kw)
