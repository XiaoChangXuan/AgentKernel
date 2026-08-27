"""V0.5 tutorial: Resource Handles keep large bytes outside model context.

Run from the repository root:

    python examples/tutorials/v0_5_resource_handle.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import LocalResourceStore, ResourceOwner, ResourceService  # noqa: E402


def main() -> None:
    payload = ("diagnostic line\n" * 2_000).encode()
    owner = ResourceOwner("tutorial-agent", "tutorial-v0-5-session")

    with tempfile.TemporaryDirectory(prefix="agentkernel-v0-5-") as directory:
        store_root = Path(directory) / "resources"
        service = ResourceService(LocalResourceStore(store_root))
        handle = service.create_artifact(
            payload,
            owner=owner,
            media_type="text/plain",
            encoding="utf-8",
            source_tool_name="logs.collect",
            source_tool_call_id="call-logs-1",
            source_operation_id="op-logs-1",
        )

        restarted = ResourceService(LocalResourceStore(store_root))
        read = restarted.read(handle.uri, owner=owner, offset=0, limit=32)

        print("V0.5 Resource Handle")
        print(f"handle_uri={handle.uri}")
        print(f"context_marker_bytes={len(handle.uri)}")
        print(f"resource_bytes={handle.size_bytes}")
        print(f"read_preview={read.data.decode()!r}")
        print(f"has_more={read.has_more}")
        print(f"restart_read_success={read.data == payload[:32]}")


if __name__ == "__main__":
    main()
