"""Offline V0.5 Resource handle creation, bounded read, and restart example."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    LocalResourceStore,
    ResourceOwner,
    ResourceService,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agentkernel-resource-example-") as root:
        store_root = Path(root) / "resources"
        owner = ResourceOwner("example-agent", "example-session")
        service = ResourceService(LocalResourceStore(store_root))
        handle = service.create_artifact(
            ("diagnostic line\n" * 1_000).encode(),
            owner=owner,
            media_type="text/plain",
            encoding="utf-8",
            source_tool_name="example.logs",
            source_tool_call_id="call-1",
            source_operation_id="op-1",
        )
        print(handle.uri)

        restarted = ResourceService(LocalResourceStore(store_root))
        chunk = restarted.read(handle.uri, owner=owner, offset=0, limit=80)
        print(chunk.data.decode())
        print(f"next_offset={chunk.next_offset} has_more={chunk.has_more}")


if __name__ == "__main__":
    main()
