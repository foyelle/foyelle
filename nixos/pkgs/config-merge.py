"""Merge Foyelle's enforced settings into the harness config.yaml.

  foyelle-config-merge ENFORCED_JSON CONFIG_YAML [OVERRIDES_JSON] [HARNESS_ENV]

Keys in ENFORCED_JSON always win (they are what the NixOS module decided); every other key the
harness wrote at runtime (for example a model the user picked) is kept. Lists are replaced, not
merged, so an enforced toolset or plugin list is exact.

OVERRIDES_JSON is what the user chose on the Settings surface (shell connector). Only the model
keys apply here. The route is ``model.route`` when set; otherwise remote when HARNESS_ENV holds a
remote API key (saved on the Settings surface), else what the module enforced. ``model.main``
pins the remote model. ENFORCED_JSON's ``_foyelle.routes`` describes both routes and is not
written to the config.
"""

import json
import os
import sys
import tempfile

import yaml


def merge(base, enforced):
    if isinstance(base, dict) and isinstance(enforced, dict):
        out = dict(base)
        for key, value in enforced.items():
            out[key] = merge(base.get(key), value) if isinstance(value, dict) else value
        return out
    return enforced


def env_has_remote_key(env_path, key_env="OPENCODE_ZEN_API_KEY"):
    try:
        with open(env_path, encoding="utf-8") as fh:
            return any(line.startswith(key_env + "=") and line.split("=", 1)[1].strip() for line in fh)
    except OSError:
        return False


def apply_overrides(config, overrides, routes, has_key=False):
    choice = overrides.get("model") if isinstance(overrides.get("model"), dict) else {}

    def name(key):
        value = choice.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    main, local_name = name("main"), name("local")
    route = choice.get("route") or ("remote" if has_key else None)
    remote, local = routes.get("remote") or {}, routes.get("local") or {}
    current = config.get("model") or {}
    if route is None and local and current.get("base_url") == local.get("base_url"):
        route = "local"  # the Nix default is already the local route
    if route == "local" and local:
        config["model"] = {"provider": "custom", "base_url": local.get("base_url"),
                           "default": local_name or local.get("model"), "api_key": "local"}
    elif route == "remote" and remote:
        config["model"] = {"provider": remote.get("provider"), "default": main or remote.get("model")}
    elif main and (config.get("model") or {}).get("provider") == remote.get("provider"):
        config["model"] = {**config["model"], "default": main}
    return config


def main(enforced_path, config_path, overrides_path=None, env_path=None):
    with open(enforced_path, encoding="utf-8") as fh:
        enforced = json.load(fh)
    routes = (enforced.pop("_foyelle", None) or {}).get("routes") or {}
    current = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            current = yaml.safe_load(fh) or {}
    current.pop("_foyelle", None)
    merged = merge(current, enforced)
    overrides = {}
    if overrides_path and os.path.exists(overrides_path):
        try:
            with open(overrides_path, encoding="utf-8") as fh:
                overrides = json.load(fh)
        except ValueError:
            overrides = {}
    if not isinstance(overrides, dict):
        overrides = {}
    key_env = (routes.get("remote") or {}).get("key_env") or "OPENCODE_ZEN_API_KEY"
    merged = apply_overrides(merged, overrides, routes, has_key=bool(env_path) and env_has_remote_key(env_path, key_env))
    directory = os.path.dirname(config_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config.", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.safe_dump(merged, fh, sort_keys=True, default_flow_style=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, config_path)


if __name__ == "__main__":
    main(*sys.argv[1:5])
