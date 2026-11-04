"""Small public inventory derived only from checksum-verified build evidence."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9+._-]{1,128}\Z")
VERSION = re.compile(r"[A-Za-z0-9+.:_~/-]{1,160}\Z")
REPORTS = ("minimal-security", "kernel-security", "wifi-logging")
REPORT_FIELDS = {"status", "profile_id", "profile_sha256", "cve", "runtime_tested"}


def read_json(root, name, *, optional=False):
    path = Path(root) / name
    if optional and not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("invalid changelog evidence: " + name)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate changelog JSON key")
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def public_source(url):
    if not isinstance(url, str):
        raise ValueError("invalid public source URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port or parsed.query or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.netloc)
            or not re.fullmatch(r"/[A-Za-z0-9._+/-]+", parsed.path)
            or ".." in parsed.path.split("/")):
        raise ValueError("source URL is not a public HTTPS repository")
    return "https://" + parsed.netloc.lower() + parsed.path.rstrip("/").removesuffix(".git")


def patch_path(value):
    return (isinstance(value, str) and len(value) <= 300
            and re.fullmatch(r"(?:ci|wrt_core)/[A-Za-z0-9_./+-]+", value) is not None
            and ".." not in value.split("/") and "//" not in value
            and (value.endswith(".patch") or
                 (value.startswith(("ci/security/", "ci/wifi-logging/"))
                  and value.endswith("/manifest.json"))))


def build_inventory(root):
    root = Path(root)
    rows = read_json(root, "inspection/installed-packages.json")["packages"]
    packages = {}
    for row in rows:
        name, version = row.get("name"), row.get("version")
        if (not isinstance(name, str) or not NAME.fullmatch(name) or name in packages
                or not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise ValueError("invalid or duplicate changelog package")
        packages[name] = version
    source = read_json(root, "provenance/source-lock.json")
    sources = {}
    for row in source["sources"]:
        url, commit = public_source(row["url"]), row["commit"]
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise ValueError("invalid changelog source commit")
        if url in sources and sources[url] != commit:
            raise ValueError("ambiguous changelog source commit")
        sources[url] = commit
    files = read_json(root, "provenance/wrapper-source-files.json", optional=True)
    patches = {}
    for row in files or []:
        if patch_path(row.get("path")):
            name, digest = row["path"], row.get("sha256", "")
            if name in patches or not HASH.fullmatch(digest):
                raise ValueError("invalid changelog patch hash")
            patches[name] = digest
    reports = {}
    for name in REPORTS:
        report = read_json(root, "reports/" + name + ".json", optional=True)
        if report is not None:
            reports[name] = {k: report[k] for k in sorted(REPORT_FIELDS) if k in report}
    go = read_json(root, "reports/go-x509-security.json", optional=True)
    components = read_json(root, "reports/components.json", optional=True)
    ca = components.get("ca", {}) if components else {}
    config = root / "metadata/final.config"
    cron = None
    if config.exists():
        if config.is_symlink() or not config.is_file() or config.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("invalid final config evidence")
        cron = "CONFIG_BUSYBOX_CONFIG_FEATURE_SYSLOG_INFO=y" in config.read_text().splitlines()
    result = {
        "schema_version": 1, "wrapper_commit": source["wrapper_checkout_commit"],
        "upstream_commit": source["upstream_commit"], "packages": packages, "sources": sources,
        "patches": patches, "patch_inventory_available": files is not None,
        "reports": reports, "cron_syslog_info": cron,
        "ca": {k: ca[k] for k in ("version", "bundle_sha256", "certificate_count") if k in ca},
        "go": ({"status": go["status"],
                "versions": sorted({r["go_version"] for r in go["binaries"]}),
                "exception_count": len(go["known_exceptions"]),
                "advisories": sorted(set(re.findall(r"(?:CVE-\d{4}-\d+|GO-\d{4}-\d+)",
                                                   json.dumps(go["known_exceptions"]))))}
               if go else {}),
    }
    validate_inventory(result)
    return result


def validate_inventory(value, metadata=None):
    expected = {"schema_version", "wrapper_commit", "upstream_commit", "packages", "sources",
                "patches", "patch_inventory_available", "reports", "cron_syslog_info", "ca", "go"}
    if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != 1:
        raise ValueError("unsupported changelog inventory")
    for key in ("wrapper_commit", "upstream_commit"):
        if not isinstance(value[key], str) or not SHA.fullmatch(value[key]):
            raise ValueError("invalid changelog source identity")
    packages = value["packages"]
    if not isinstance(packages, dict) or not 1 <= len(packages) <= 5000 or "kernel" not in packages:
        raise ValueError("missing changelog package inventory")
    for k, v in packages.items():
        if not NAME.fullmatch(k) or not isinstance(v, str) or not VERSION.fullmatch(v):
            raise ValueError("invalid changelog package inventory")
    for key in ("sources", "patches"):
        if not isinstance(value[key], dict) or len(value[key]) > 2000:
            raise ValueError("invalid changelog source/patch inventory")
        for k, v in value[key].items():
            valid_key = public_source(k) == k if key == "sources" else patch_path(k)
            if not valid_key or not isinstance(v, str) or not (SHA if key == "sources" else HASH).fullmatch(v):
                raise ValueError("invalid changelog source/patch entry")
    if (type(value["patch_inventory_available"]) is not bool or
            (value["cron_syslog_info"] is not None and type(value["cron_syslog_info"]) is not bool)):
        raise ValueError("invalid changelog evidence availability")
    if not isinstance(value["reports"], dict) or set(value["reports"]) - set(REPORTS):
        raise ValueError("invalid changelog security reports")
    for report in value["reports"].values():
        if not isinstance(report, dict) or set(report) - REPORT_FIELDS:
            raise ValueError("invalid changelog report fields")
        for k, v in report.items():
            if k == "runtime_tested":
                valid = type(v) is bool
            elif k == "profile_sha256":
                valid = isinstance(v, str) and HASH.fullmatch(v)
            else:
                valid = isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9._+-]{1,160}", v)
            if not valid:
                raise ValueError("invalid changelog report value")
    if not isinstance(value["ca"], dict) or set(value["ca"]) - {"version", "bundle_sha256", "certificate_count"}:
        raise ValueError("invalid CA changelog evidence")
    for k, v in value["ca"].items():
        valid = (type(v) is int and 0 < v < 10000) if k == "certificate_count" else (
            isinstance(v, str) and (HASH if k == "bundle_sha256" else VERSION).fullmatch(v))
        if not valid:
            raise ValueError("invalid CA changelog value")
    go = value["go"]
    if not isinstance(go, dict) or (go and set(go) != {"status", "versions", "exception_count", "advisories"}):
        raise ValueError("invalid Go changelog evidence")
    if go:
        if (not isinstance(go["status"], str) or not NAME.fullmatch(go["status"])
                or type(go["exception_count"]) is not int or not 0 <= go["exception_count"] <= 1000
                or not isinstance(go["versions"], list) or not isinstance(go["advisories"], list)
                or len(go["versions"]) > 100 or len(go["advisories"]) > 1000
                or any(not isinstance(v, str) or not re.fullmatch(r"go[0-9.]+", v) for v in go["versions"])
                or any(not isinstance(v, str) or not re.fullmatch(r"(?:CVE|GO)-\d{4}-\d+", v)
                       for v in go["advisories"])):
            raise ValueError("invalid Go changelog values")
    if metadata and (value["wrapper_commit"] != metadata["wrapper_commit"] or
                     value["upstream_commit"] != metadata["upstream_commit"] or
                     packages["kernel"] != metadata["kernel_abi"] or
                     len(packages) != metadata["package_count"]):
        raise ValueError("changelog inventory does not match exact release")
    return value
