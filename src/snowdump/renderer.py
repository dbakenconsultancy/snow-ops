from pathlib import Path
import os

import sqlparse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateRuntimeError


def _raise(message: str) -> None:
    raise TemplateRuntimeError(message)


def _env_var(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise TemplateRuntimeError(
            f"env_var('{name}') is not set — export it or add it to .env"
        )
    return value


def build_env(project_dir: Path) -> Environment:
    """Jinja environment rooted at project_dir.

    Templates address each other relative to that root, e.g.
    ``{% from 'modules/macros.sql' import my_macro %}``.
    """
    env = Environment(
        loader=FileSystemLoader(str(project_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.globals["raise"] = _raise
    env.globals["env_var"] = _env_var
    return env


def render_file(sql_file: Path, project_dir: Path, context: dict, env: Environment) -> str:
    template_name = sql_file.relative_to(project_dir).as_posix()
    return env.get_template(template_name).render(**context)


def split_statements(sql: str) -> list[str]:
    return [s.strip().rstrip(";").strip() for s in sqlparse.split(sql) if s.strip()]
