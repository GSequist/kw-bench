"""Harbor adapter for micro-cc (https://pypi.org/project/micro-cc/): runs `microcc-headless` inside the task container.

  PYTHONPATH=. harbor run -p datasets/kw-sap-vat -i sap-vat-chain-b -a agents.microcc_agent:MicroccAgent \
    -m openrouter/deepseek/deepseek-v4.1-flash-1017 --ak version=0.2.122
  Pin the PyPI release with --ak version=X. Local build: MICROCC_LOCAL=/path/to/micro_cc-*.whl or /path/to/micro-cc repo.
"""
import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import override

from pydantic import Field

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.options import InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

VENV = "/opt/microcc"
LOG = "/logs/agent"
# harbor provider prefix -> (micro-cc provider, credential/config env vars to forward)
PROVIDERS = {
    "anthropic": ("anthropic", ["ANTHROPIC_API_KEY"]),
    "openrouter": ("openrouter", ["OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"]),
    "openai": ("openai", ["OPENAI_API_KEY", "OPENAI_BASE_URL"]),
    "bedrock": ("bedrock", ["AWS_BEARER_TOKEN_BEDROCK", "AWS_REGION"]),
    "vertex": ("vertex", ["ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION", "GOOGLE_APPLICATION_CREDENTIALS"]),
}
# id -> micro-cc alias, looked up in the installed catalog; unknown ids pass through unchanged
RESOLVE = ("import sys; from micro_cc.models.catalog import MODELS; m, p = sys.argv[1:3]; "
           "ids = lambda e: {k: (v if isinstance(v, str) else v.get('id')) for k, v in e.get('ids', {}).items()}; "
           "print(next((a for a, e in MODELS.items() if a == m or ids(e).get(p) == m), m))")


class MicroccOptions(InstalledAgentOptions):
    no_subagents: bool = Field(default=False, description="Pass --no-subagents (single agent, no orchestration).")
    max_loops: int | None = Field(default=None, description="Cap on tool-call rounds (--max-loops).")
    wait_timeout: float = Field(default=3600.0, description="Max seconds to wait for a subagent checkpoint (--wait-timeout).")
    local: str | None = Field(default=None, description="Host path to a micro-cc wheel or repo; defaults to $MICROCC_LOCAL.")


class MicroccAgent(BaseInstalledAgent):
    """micro-cc headless as a Harbor installed agent."""

    options_model = MicroccOptions
    options: MicroccOptions

    @staticmethod
    @override
    def name() -> str:
        return "microcc"

    @override
    def get_version_command(self) -> str | None:
        return f"{VENV}/bin/python -c 'import importlib.metadata as m; print(m.version(\"micro-cc\"))'"

    def _local(self) -> Path | None:
        p = self.options.local or self._get_env("MICROCC_LOCAL")
        return Path(p).expanduser() if p else None

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        spec = f"micro-cc=={self._version}" if self._version else "micro-cc"
        local = self._local()
        if local and local.is_file():
            await environment.upload_file(local, f"/tmp/{local.name}")
            spec = f"/tmp/{local.name}"
        elif local and local.is_dir():
            with tempfile.TemporaryDirectory() as tmp:  # only what a build needs, not .git/node_modules
                for item in ("pyproject.toml", "README.md", "LICENSE", "src"):
                    src = local / item
                    if src.is_dir():
                        shutil.copytree(src, f"{tmp}/{item}", ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
                    elif src.exists():
                        shutil.copy(src, f"{tmp}/{item}")
                await environment.upload_dir(tmp, "/tmp/microcc-src")
            spec = "/tmp/microcc-src"
        await self.exec_as_root(environment, command=(
            "set -e; "
            'if python3 -c "import sys; sys.exit(sys.version_info < (3, 12))" 2>/dev/null; then '
            f"python3 -m venv {VENV} && {VENV}/bin/pip install -q --upgrade pip && {VENV}/bin/pip install -q {shlex.quote(spec)}; "
            "else curl -LsSf https://astral.sh/uv/install.sh | sh && . $HOME/.local/bin/env && "
            f"uv venv --python 3.12 {VENV} && uv pip install -q --python {VENV}/bin/python {shlex.quote(spec)}; fi; "
            f"ln -sf {VENV}/bin/microcc-headless /usr/local/bin/microcc-headless; "
            f"ln -sf {VENV}/bin/microcc /usr/local/bin/microcc; "
            "microcc-headless --help >/dev/null"))

    def _provider_env(self) -> tuple[str, str, dict[str, str]]:
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("model must be provider/model, e.g. openrouter/deepseek/deepseek-v4.1-flash-1017")
        prefix, model = self.model_name.split("/", 1)
        if prefix not in PROVIDERS:
            raise ValueError(f"unsupported provider '{prefix}' (supported: {', '.join(PROVIDERS)})")
        provider, keys = PROVIDERS[prefix]
        env = {k: v for k in keys if (v := self._get_env(k))}
        if keys[0] not in env:
            raise ValueError(f"{keys[0]} is not set")
        env["MICROCC_PROVIDER"] = provider
        if provider == "openrouter":
            env["OPENROUTER_MODEL"] = model
        return provider, model, env

    @override
    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        provider, model, env = self._provider_env()
        flags = ["--verbose", "--output-format", "json", "--wait-timeout", str(int(self.options.wait_timeout))]
        if self.options.no_subagents:
            flags.append("--no-subagents")
        if self.options.max_loops:
            flags += ["--max-loops", str(self.options.max_loops)]
        resolve = f"{VENV}/bin/python -c {shlex.quote(RESOLVE)} {shlex.quote(model)} {provider}"
        try:
            await self.exec_as_agent(environment, env=env, command=(
                f"mkdir -p {LOG}; M=$({resolve}); echo \"model: $M ({provider})\" > {LOG}/microcc.txt; "
                f"microcc-headless /app {shlex.quote(instruction)} --model \"$M\" {' '.join(flags)} 2>&1 "
                f"| tee -a {LOG}/microcc.txt; rc=${{PIPESTATUS[0]}}; echo \"exit: $rc\" >> {LOG}/microcc.txt; "
                "[ $rc -le 1 ]"))  # 1 = run ended PAUSED/FAILED: outputs may exist, still grade
        finally:
            await self.exec_as_agent(environment, command=(
                f"mkdir -p {LOG}/microcc-projects && cp -r $HOME/.micro-cc/projects/. {LOG}/microcc-projects/ 2>/dev/null || true"))

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        tin = tout = 0
        for p in (self.logs_dir / "microcc-projects").glob("*/tokens.json"):  # main run + every subagent
            try:
                t = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            tin += int(t.get("total_input", 0))
            tout += int(t.get("output", 0))
        if tin or tout:
            context.n_input_tokens, context.n_output_tokens = tin, tout
        context.metadata = {**(context.metadata or {}), "microcc_projects": len(list((self.logs_dir / "microcc-projects").glob("*")))}
