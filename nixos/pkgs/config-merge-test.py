"""Checks for config-merge's model routing (run by the config-merge Nix check)."""

import importlib.util
import sys

spec = importlib.util.spec_from_file_location("config_merge", sys.argv[1])
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)

ROUTES = {"local": {"base_url": "http://192.168.64.1:11434/v1", "model": "qwen3.5:9b"},
          "remote": {"provider": "opencode-zen", "model": "glm-5.3-flash-free"}}
LOCAL = {"provider": "custom", "base_url": "http://192.168.64.1:11434/v1", "default": "qwen3.5:9b", "api_key": "local"}


def model(overrides, has_key=False, current=LOCAL):
    return cm.apply_overrides({"model": dict(current)}, overrides, ROUTES, has_key)["model"]


# The Nix default (local) is kept, and model.local renames its model.
assert model({}) == LOCAL
assert model({"model": {"local": " qwen3:4b-instruct "}})["default"] == "qwen3:4b-instruct"
assert model({"model": {"route": "local", "local": "qwen3:4b-instruct"}})["default"] == "qwen3:4b-instruct"
# A saved key switches to remote unless the route says local; model.main names the remote model.
assert model({}, has_key=True) == {"provider": "opencode-zen", "default": "glm-5.3-flash-free"}
assert model({"model": {"main": "big-pickle"}}, has_key=True)["default"] == "big-pickle"
assert model({"model": {"route": "local", "local": "qwen3:4b-instruct"}}, has_key=True)["default"] == "qwen3:4b-instruct"
# model.local does not touch a remote route.
assert model({"model": {"route": "remote", "local": "x"}})["provider"] == "opencode-zen"
# The key variable comes from the remote route.
import os
import tempfile
with tempfile.TemporaryDirectory() as d:
    env = os.path.join(d, ".env")
    with open(env, "w") as fh:
        fh.write("OPENCODE_ZEN_API_KEY=old\nOPENROUTER_API_KEY=sk-or-v1-x\n")
    assert cm.env_has_remote_key(env, "OPENROUTER_API_KEY")
    with open(env, "w") as fh:
        fh.write("OPENCODE_ZEN_API_KEY=old\n")
    assert not cm.env_has_remote_key(env, "OPENROUTER_API_KEY")
print("config-merge: ok")
