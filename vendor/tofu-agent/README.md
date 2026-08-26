# Bundled Tofu Agent runtime

GovTrans ships the headless Tofu Agent `0.17.0` wheel required by its
asynchronous task-handle integration. The wheel was built from the local Tofu
source tree and deliberately contains only the Python runtime packages and the
small provider setup assets. It excludes Tofu databases, conversations, logs,
uploads, `.env` files, application routes and browser frontend.

- Upstream project: https://github.com/rangehow/ToFu
- Source base commit: `1ad0eb410a6781fd6bdc6db79eb8754e0bce5d6b`
- Build provenance: filtered from the reviewed local working tree based on that
  commit; the wheel itself contains the shipped Python source and its RECORD
  manifest.
- License: MIT; see `LICENSE`
- Wheel: `tofu_agent-0.17.0-py3-none-any.whl`
- SHA-256: `681ddbeaf599b7932308e42a5ae7330064dc4bbe9c68e2b30af6561d3f9daca8`
- Dependency lock: `requirements.lock`

The Docker build verifies this hash before installing the wheel. Do not replace
the wheel without rebuilding it from a reviewed Tofu source tree, updating the
lock file, updating the hash here and in `docker/tofu-agent.Dockerfile`, and
rerunning the deployment smoke tests.
