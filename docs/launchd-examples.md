# Generic launchd service examples

These two files are generic, configurable macOS user LaunchAgent examples:

- `launchd/panel.plist.example`: a long-running local web panel.
- `launchd/fetch.plist.example`: a daily scheduled full pipeline run.

They are templates, not a working installation. They have only been validated
offline: nothing has been bootstrapped, loaded, started or registered by this
repository. They are for your own setup and must not be copied over a service you
already run.

## Prerequisites

- The project dependencies are already installed for the Python interpreter you
  name in `__PYTHON_PATH__`. Installation on a clean machine has not been
  verified.
- You have your own configuration file, rules and skill vocabulary.
- You have created the log directory you name in `__LOG_DIR__`.
- This is a macOS user LaunchAgent (`gui/$(id -u)` domain), not a
  system daemon.

## Placeholders

| Placeholder | Meaning |
|---|---|
| `__PROJECT_DIR__` | Absolute path to the project root. Used as `WorkingDirectory`. |
| `__PYTHON_PATH__` | Absolute path to a Python executable that has the project dependencies installed. |
| `__CONFIG_PATH__` | Absolute path to your own configuration file. Passed with an explicit `--config`. |
| `__LOG_DIR__` | Absolute path to a log directory you create first. |

The examples are unusable until every `__PLACEHOLDER__` has been
replaced.

## Rendering with the standard library

Parse each plist with `plistlib`, replace the placeholder strings
recursively, and serialize back with `plistlib`. Do not use `sed`
or shell string substitution: paths can contain spaces and XML special
characters such as `&`, and the XML producer must escape them itself.
Never interpolate user paths into shell code; pass them as arguments.

```python
#!/usr/bin/env python3
"""Render the launchd plist examples into a new output directory.

Standard library only. See docs/launchd-examples.md for the placeholder table.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import re
from pathlib import Path

KNOWN_PLACEHOLDERS = (
    "__PROJECT_DIR__",
    "__PYTHON_PATH__",
    "__CONFIG_PATH__",
    "__LOG_DIR__",
)
TEMPLATE_NAMES = ("panel.plist.example", "fetch.plist.example")
_PLACEHOLDER_RE = re.compile(r"__[A-Z0-9_]+__")


def _replace(value, mapping):
    if isinstance(value, str):
        for key, replacement in mapping.items():
            value = value.replace(key, replacement)
        return value
    if isinstance(value, list):
        return [_replace(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, mapping) for key, item in value.items()}
    return value


def validate_mapping(mapping):
    if set(mapping) != set(KNOWN_PLACEHOLDERS):
        raise ValueError(
            "mapping must use exactly " + ", ".join(KNOWN_PLACEHOLDERS))
    for key, value in mapping.items():
        if not isinstance(value, str) or not os.path.isabs(value):
            raise ValueError(f"{key} must be an absolute path: {value!r}")


def validate_environment(mapping):
    project_dir = Path(mapping["__PROJECT_DIR__"])
    python_path = Path(mapping["__PYTHON_PATH__"])
    config_path = Path(mapping["__CONFIG_PATH__"])
    log_dir = Path(mapping["__LOG_DIR__"])
    if not project_dir.is_dir():
        raise ValueError(f"project directory does not exist: {project_dir}")
    if not (python_path.is_file() and os.access(python_path, os.X_OK)):
        raise ValueError(f"python is not an executable file: {python_path}")
    if not config_path.is_file():
        raise ValueError(f"config file does not exist: {config_path}")
    if not log_dir.is_dir():
        raise ValueError(f"log directory does not exist: {log_dir}")


def _render_one(template_path, mapping):
    if not template_path.is_file():
        raise FileNotFoundError(f"missing template: {template_path}")
    with template_path.open("rb") as handle:
        data = plistlib.load(handle)
    rendered = _replace(data, mapping)
    payload = plistlib.dumps(rendered, fmt=plistlib.FMT_XML)
    leftover = sorted(set(_PLACEHOLDER_RE.findall(payload.decode("utf-8"))))
    if leftover:
        raise ValueError(f"{template_path.name}: unreplaced placeholders {leftover}")
    return template_path.name.replace(".example", ""), payload


def render_templates(template_dir, output_dir, mapping):
    validate_mapping(mapping)
    validate_environment(mapping)
    template_dir = Path(template_dir)
    if not template_dir.exists():
        raise FileNotFoundError(f"template directory does not exist: {template_dir}")
    if not template_dir.is_dir():
        raise NotADirectoryError(f"template path is not a directory: {template_dir}")
    # Read, replace and check both fixed templates before creating any output, so
    # a missing input cannot leave an empty or half-written output directory.
    rendered = [_render_one(template_dir / name, mapping) for name in TEMPLATE_NAMES]
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_dir}")
    output_dir.mkdir(parents=True)
    written = []
    for target_name, payload in rendered:
        target = output_dir / target_name
        target.write_bytes(payload)
        written.append(target)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render the launchd examples.")
    parser.add_argument("--template-dir", default="launchd")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--python-path", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--log-dir", required=True)
    args = parser.parse_args(argv)
    mapping = {
        "__PROJECT_DIR__": args.project_dir,
        "__PYTHON_PATH__": args.python_path,
        "__CONFIG_PATH__": args.config_path,
        "__LOG_DIR__": args.log_dir,
    }
    written = render_templates(args.template_dir, args.output_dir, mapping)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Save the script (for example as `render_launchd_examples.py`) and run it
with absolute paths:

```bash
python3 render_launchd_examples.py \
  --template-dir launchd \
  --output-dir "/tmp/launchd-rendered" \
  --project-dir "/absolute/path/to/au-job-pipeline" \
  --python-path "/absolute/path/to/python" \
  --config-path "/absolute/path/to/config.yaml" \
  --log-dir "/absolute/path/to/logs"
```

What the renderer enforces:

- it renders exactly the two fixed templates, `panel.plist.example` and
  `fetch.plist.example`; any other file in the directory is ignored;
- the template directory must exist and be a directory, and both fixed templates
  must exist, be readable and parse as plists;
- both templates are read, replaced and checked for leftover placeholders before
  the output directory is created, so a missing or invalid input fails without
  leaving an empty or partial output;
- the mapping has exactly the four known placeholders, and every value is an
  absolute path;
- `__PROJECT_DIR__` and `__LOG_DIR__` exist and are directories,
  `__PYTHON_PATH__` is an executable file, and `__CONFIG_PATH__` is a file;
- the output directory must be new: the renderer refuses to overwrite an
  existing path or an already rendered product;
- no placeholder may remain in the serialized plist, and output is produced with
  `plistlib` so XML special characters stay escaped.

Rendering only writes the new output directory. It does not install, load,
bootstrap or kickstart a service, and it does not touch the working tree, the
existing plists, your running service or your configuration.

## After rendering

Create the log directory before you load a service, then check the rendered
files:

```bash
mkdir -p "/absolute/path/to/logs"
plutil -lint /tmp/launchd-rendered/panel.plist /tmp/launchd-rendered/fetch.plist
```

If you choose to install them yourself, the usual user-level commands are:

```bash
cp /tmp/launchd-rendered/panel.plist ~/Library/LaunchAgents/local.au-job-pipeline.panel.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.au-job-pipeline.panel.plist
launchctl print gui/$(id -u)/local.au-job-pipeline.panel
launchctl bootout gui/$(id -u)/local.au-job-pipeline.panel
```

These are your next steps to choose; this repository does not run them. Do not
copy the examples over an existing deployment.

## Panel versus scheduled pipeline

- The panel example uses `RunAtLoad` and `KeepAlive`, with
  `ThrottleInterval` 10 and `ProcessType Background`. It is a
  resident service that binds to `127.0.0.1` only.
- The fetch example uses `StartCalendarInterval` and deliberately has no
  `KeepAlive` and `RunAtLoad` false. It is a timer, not a
  resident service. The 07:30 schedule is an example in local time; choose your
  own time.
- The `run` subcommand fetches over the network, analyzes and writes
  your database and reports. It is not an offline smoke test and needs working
  network and data sources.
- The service `Label` values are generic, so they do not collide with a service
  you may already run under another name.

## Panel exposure

The web panel has no built-in authentication. The example binds it to
`127.0.0.1` only. Labels, notes and the database are your private data.
Remote access requires your own protected access layer, for example an
authenticating reverse proxy or a private network, and should never expose the
port directly.

## Status

The examples are templates that have been validated offline only. Installing and
running them, and the choice of schedule, are yours. Which of these files a
public release ships has not been decided.
