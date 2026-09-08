"""Vendored from ~/hcls-capstone-dev/gauravks-fde-k8s-copilot's
harvest/genai-replay-harness/src/genai_replay_harness/genai_cassette.py (~130 lines,
self-contained, no exotic dependencies). Vendored rather than a cross-repo path dependency so this
repo stays cloneable and testable standalone.

`google-genai`'s built-in record/replay layer, as a reusable context manager.

Almost nobody knows this exists: `google-genai` ships `DebugConfig(client_mode, replays_directory,
replay_id)`, driven entirely by three environment variables
(`GOOGLE_GENAI_CLIENT_MODE` / `_REPLAYS_DIRECTORY` / `_REPLAY_ID`). `Client.__init__` does
`self._debug_config = debug_config or DebugConfig()`, and `DebugConfig()`'s fields default from
those env vars — so **every** `genai.Client(...)` constructed anywhere in the process picks this up
automatically. No code under test has to import this module, know it exists, or change in any way.

## Workflow

1. **Record once, live:**

   ```python
   with genai_cassette("record", cassette_dir / "my_interaction"):
       real_call_that_constructs_a_genai_Client()
   ```

   This makes one real, credentialed, billed API call and writes the interaction to
   `cassette_dir/my_interaction.json`.

2. **Sanitize before committing.** The raw cassette contains request/response headers, which can
   include `authorization` and `x-goog-user-project`, and the URL contains your project id. Strip
   or replace all three before committing — `sanitize_cassette()` below does the specific
   transformation the parent project verified is both sufficient (its own test reads the
   committed file and asserts none of the three appear) and safe (the interaction still replays
   correctly afterward, because content and status code are untouched).

3. **Replay forever, free, offline:**

   ```python
   with genai_cassette("replay", cassette_dir / "my_interaction"):
       exact_same_call_with_zero_live_credentials_needed()
   ```

   Deterministic every time — a real advantage over a live call even in CI-only contexts, since a
   live model is subject to updates and sampling noise a cassette is immune to by construction.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
from typing import Iterator, Optional, Union

_SANITIZE_HEADERS = ("authorization", "x-goog-user-project")


@contextlib.contextmanager
def genai_cassette(mode: str, cassette_file: Union[pathlib.Path, str]) -> Iterator[None]:
    """Set the three `GOOGLE_GENAI_*` env vars for the duration of the `with` block, restoring
    whatever was there before on exit — never leaks into a later test in the same process.

    `cassette_file` is the path to the cassette JSON file, laid out as
    `<replays_root>/<module>/<function>/<vertex|mldev>.json` — `google-genai`'s own
    `ReplayApiClient` requires the replay id to have at least 3 `/`-separated segments (its error
    message literally says so if you get this wrong: `"Session ID must be in the format of
    module/function/[vertex|mldev]"`). This function walks up from the file to find a directory at
    least 3 levels below an ancestor, i.e. it treats `cassette_file.parents[2]` as the replays root
    and everything below it (minus the `.json` suffix) as the replay id.
    """
    cassette_file = pathlib.Path(cassette_file)
    directory = cassette_file.parents[2]
    rid = str(cassette_file.relative_to(directory)).removesuffix(".json")

    keys = (
        "GOOGLE_GENAI_CLIENT_MODE",
        "GOOGLE_GENAI_REPLAYS_DIRECTORY",
        "GOOGLE_GENAI_REPLAY_ID",
    )
    previous = {k: os.environ.get(k) for k in keys}
    os.environ["GOOGLE_GENAI_CLIENT_MODE"] = mode
    os.environ["GOOGLE_GENAI_REPLAYS_DIRECTORY"] = str(directory)
    os.environ["GOOGLE_GENAI_REPLAY_ID"] = rid
    try:
        yield
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def sanitize_cassette(
    cassette_path: Union[pathlib.Path, str], project_id: Optional[str] = None
) -> None:
    """In-place: drop auth-material headers entirely (don't redact-in-place — remove the key), and
    replace `projects/<id>/locations/...` with a placeholder so the file is safe to commit and
    reproduces the interaction on any project.

    Dropping rather than masking the header keys matters: a masked-but-present `authorization`
    header is one `git log -p` away from someone assuming the mask is reversible. It is not
    reversible; it should not exist in the artifact at all.
    """
    path = pathlib.Path(cassette_path)
    data = json.loads(path.read_text())

    def _strip_headers(obj):
        if isinstance(obj, dict):
            for key in _SANITIZE_HEADERS:
                obj.pop(key, None)
            for value in obj.values():
                _strip_headers(value)
        elif isinstance(obj, list):
            for item in obj:
                _strip_headers(item)

    _strip_headers(data)
    text = json.dumps(data)
    if project_id:
        text = text.replace(project_id, "{PROJECT_ID}")
    text = re.sub(r"projects/[^/]+/locations/[^/\"]+", "{VERTEX_URL_PREFIX}", text)
    path.write_text(text)


def assert_cassette_is_sanitized(
    cassette_path: Union[pathlib.Path, str], project_id: Optional[str] = None
) -> None:
    """The commit-time gate: read the file back and fail loudly if sanitization was skipped."""
    text = pathlib.Path(cassette_path).read_text()
    lowered = text.lower()
    for header in _SANITIZE_HEADERS:
        assert header not in lowered, (
            f"cassette still contains a {header!r} header — re-sanitize before committing"
        )
    if project_id:
        assert project_id not in text, (
            "cassette still contains a literal project id — re-sanitize before committing"
        )
