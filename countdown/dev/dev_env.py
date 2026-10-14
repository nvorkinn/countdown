"""Writes dev/.env, so a renderer can be run by hand (PyCharm, `uv run countdown-server`) instead of by the
orchestrator, and the screen (VS Code) can use the same broker. See "Running it in development" in ../README.md.

    BROKER_URL=http://localhost:5000 ORCHESTRATOR_TOKEN=... uv run python dev/dev_env.py <slug>

It reads the slug's assignment the way the orchestrator does (read-only: it doesn't report a status), and keeps
its claim token for the renderer. Only run it against a broker that has no orchestrator watching it, or the
orchestrator starts a container for the assignment and claims the screen first."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

from countdown_orchestrator.broker import OrchestratorBroker

DEV_DIR = Path(__file__).parent
ENV_FILE = DEV_DIR / ".env"
# Each process keeps its .auth_broker_device in its working directory, and the renderer's and the screen's
# are different devices (a secret registered as one role is refused as the other), so they can't share one.
WORKING_DIRS = ("renderer", "screen")
# The renderer needs some CLAIM_TOKEN even once it's claimed (it's then ignored, see RendererRegistrar), and the
# broker clears the token when it's used.
CLAIMED = "already-claimed"


def write_env(broker_url: str, slug: str, claim_token: str, **extra: str) -> dict[str, str]:
    """Writes dev/.env (owner-only: the claim token is a credential) and makes the working directories. Returns
    the variables written."""
    values = {
        "BROKER_URL": broker_url,
        "DEVICE_NAME": slug,
        "CLAIM_TOKEN": claim_token or CLAIMED,
        "LOG_LEVEL": "DEBUG",
        **extra,
    }
    ENV_FILE.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    ENV_FILE.chmod(0o600)
    for name in WORKING_DIRS:
        (DEV_DIR / name).mkdir(exist_ok=True)
    return values


def add_pycharm_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-pycharm", action="store_true", help="don't write a PyCharm run configuration")
    parser.add_argument(
        "--idea-dir",
        type=Path,
        help="the project's .idea directory, if it isn't the repository's (default: <repository>/.idea)",
    )
    parser.add_argument("--module", default="countdown-server", help="the PyCharm module the renderer runs in")


def write_pycharm_config(args: argparse.Namespace, slug: str, env: dict[str, str]) -> Path | None:
    """Writes .idea/runConfigurations/countdown_server_<slug>.xml: `countdown_server.main` with these variables and
    dev/renderer as its working directory, so it only has to be run or debugged. Does nothing with --no-pycharm.
    The file holds the variables (the claim token, say), and .idea/ is git-ignored."""
    if args.no_pycharm:
        return None
    if args.idea_dir:
        idea_dir = args.idea_dir.resolve()
    else:
        top = subprocess.run(
            ["git", "-C", str(DEV_DIR), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
        idea_dir = Path(top) / ".idea"
    project_dir = idea_dir.parent
    working_directory = DEV_DIR.resolve() / WORKING_DIRS[0]
    # Relative to the project where it can be, so the configuration works wherever it's cloned.
    if working_directory.is_relative_to(project_dir):
        working_directory = Path("$PROJECT_DIR$") / working_directory.relative_to(project_dir)

    name = f"countdown-server ({slug})"
    component = ElementTree.Element("component", name="ProjectRunConfigurationManager")
    configuration = ElementTree.SubElement(
        component,
        "configuration",
        {"default": "false", "name": name, "type": "PythonConfigurationType", "factoryName": "Python"},
    )
    ElementTree.SubElement(configuration, "module", name=args.module)
    ElementTree.SubElement(configuration, "option", name="INTERPRETER_OPTIONS", value="")
    ElementTree.SubElement(configuration, "option", name="PARENT_ENVS", value="true")
    envs = ElementTree.SubElement(configuration, "envs")
    for key, value in env.items():
        ElementTree.SubElement(envs, "env", name=key, value=value)
    options = {
        "SDK_HOME": "",
        "WORKING_DIRECTORY": str(working_directory),
        "IS_MODULE_SDK": "true",
        "ADD_CONTENT_ROOTS": "true",
        "ADD_SOURCE_ROOTS": "true",
        "SCRIPT_NAME": "countdown_server.main",
        "PARAMETERS": "",
        "SHOW_COMMAND_LINE": "false",
        "EMULATE_TERMINAL": "false",
        "MODULE_MODE": "true",
        "REDIRECT_INPUT": "false",
        "INPUT_FILE": "",
    }
    for key, value in options.items():
        ElementTree.SubElement(configuration, "option", name=key, value=value)
    ElementTree.SubElement(configuration, "method", v="2")
    ElementTree.indent(component)

    path = idea_dir / "runConfigurations" / f"countdown_server_{slug.replace('-', '_')}.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ElementTree.tostring(component, encoding="unicode") + "\n")
    path.chmod(0o600)
    print(f"Wrote the PyCharm run configuration {name!r}: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slug", help="the renderer's slug, as picked on the broker's config page")
    parser.add_argument("--broker-url", default=os.environ.get("BROKER_URL"))
    parser.add_argument("--orchestrator-token", default=os.environ.get("ORCHESTRATOR_TOKEN"))
    add_pycharm_arguments(parser)
    args = parser.parse_args()
    if not args.broker_url or not args.orchestrator_token:
        parser.error("BROKER_URL and ORCHESTRATOR_TOKEN are required (as arguments or environment)")

    assignments = OrchestratorBroker(args.broker_url, args.orchestrator_token).assignments()
    assignment = next((a for a in assignments if a.slug == args.slug), None)
    if assignment is None:
        print(f"No assignment with slug {args.slug!r}; there are: {sorted(a.slug for a in assignments)}")
        return 1
    if not assignment.claim_token:
        print("It has no claim token left (it's been claimed): the renderer needs the identity it saved then.")

    env = write_env(args.broker_url, assignment.slug, assignment.claim_token)
    write_pycharm_config(args, assignment.slug, env)
    print(f"Wrote {ENV_FILE} for {assignment.slug!r} ({assignment.status.value})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
