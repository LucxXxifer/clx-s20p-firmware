"""Synthetic official-input-shaped evidence; no fixture performs network I/O."""
import base64
import hashlib
import json
from pathlib import Path
import components
import plugin_sources

CI = Path(__file__).resolve().parents[1]


def files(data):
    return [{"path": "Makefile", "sha256": components.digest(data),
             "blob_sha1": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
             "content": base64.b64encode(data).decode()}]


def inputs():
    lock = json.loads((CI / "sources.lock.json").read_text())
    rows = [{"repository": plugin_sources.repository(row["url"]), "ref": row["ref"],
             "resolved_ref": "main" if row["ref"] == "HEAD" else row["ref"],
             "base_commit": row["commit"], "head_commit": row["commit"],
             "comparison": {"base_commit": row["commit"], "merge_base": row["commit"], "compare_status": "identical"},
             "previous_comparison": None} for row in plugin_sources.selected(lock)]
    ca = b"PKG_VERSION:=20260816\nPKG_RELEASE:=2\nPKG_HASH:=" + b"a" * 64 + b"\n"
    quick = b"PKG_VERSION:=0.13.0\nPKG_HASH:=" + b"b" * 64 + b"\nPKG_SOURCE_URL:=https://github.com/linkease/istore-packages/releases/download/prebuilt/\n"
    value = {"schema_version": 1, "plugin_sources": rows,
             "ca": {"repository": components.CA_REPOSITORY, "commit": "a" * 40,
                    "path": "package/system/ca-certificates", "files": files(ca)},
             "quickstart": {"repository": components.QUICKSTART_REPOSITORY, "commit": "b" * 40,
                            "path": "quickstart", "files": files(quick)},
             "go": [{"version": "go1.26.10", "sha256": "c" * 64}, {"version": "go1.27.3", "sha256": "d" * 64}],
             "adguard": {"version": "0.107.79", "commit": "e" * 40,
                         "source_sha256": "e" * 64, "frontend_sha256": "f" * 64}}
    value["fingerprint"] = components.fingerprint(value)
    return components.validate(value)
