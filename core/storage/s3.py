"""S3-backed durable storage with a disposable local materialization cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .base import Storage


_STEP_DIRECTORY = re.compile(r"step_(\d+)")
_MANIFEST_NAME = "manifest.json"
_ARTIFACT_MANIFEST_SUFFIX = ".manifest.json"


class S3Storage(Storage):
    """Persist runs in S3 while exposing a short-lived local working cache.

    S3 has no directory rename.  A checkpoint therefore uploads immutable
    files below a unique ``.tmp`` generation and publishes one manifest object
    last.  Readers only consider a step visible after that manifest exists.
    """

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "vf",
        cache_root: Path | str | None = None,
        client: Any | None = None,
        region_name: str | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket must not be empty")
        self.bucket = bucket.strip()
        self.prefix = prefix.strip("/")
        configured_cache = cache_root if cache_root is not None else os.environ.get(
            "VF_S3_CACHE_DIR", "/tmp/verifierforge-s3-cache"
        )
        self.root = Path(configured_cache)
        self.region_name = region_name or os.environ.get("VF_S3_REGION")
        self.client = client if client is not None else _new_s3_client(self.region_name)

    @classmethod
    def from_env(cls, *, cache_root: Path | str | None = None) -> "S3Storage":
        """Build the opt-in backend from non-secret storage configuration."""
        bucket = os.environ.get("VF_S3_BUCKET", "").strip()
        if not bucket:
            raise ValueError("VF_STORAGE_BACKEND=s3 requires VF_S3_BUCKET")
        return cls(
            bucket,
            prefix=os.environ.get("VF_S3_PREFIX", "vf"),
            cache_root=cache_root,
            region_name=os.environ.get("VF_S3_REGION"),
        )

    def save_checkpoint(self, job_id: str, step: int, path: Path) -> None:
        """Upload a complete checkpoint and publish its manifest atomically."""
        if step < 0:
            raise ValueError("checkpoint step must be non-negative")
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)

        generation = uuid.uuid4().hex
        files = list(_tree_files(source))
        manifest_files: list[dict[str, Any]] = []
        for relative_path, source_file in files:
            key = self._key(job_id, "ckpt", ".tmp", f"step_{step}", generation, relative_path)
            identity = _file_identity(source_file)
            self._put_file(key, source_file)
            manifest_files.append({"path": relative_path, "key": key, **identity})

        manifest = {
            "schema_version": 1,
            "step": step,
            "generation": generation,
            "files": manifest_files,
        }
        self._put_json(self._checkpoint_manifest_key(job_id, step), manifest)
        self._materialize_checkpoint(job_id, step, manifest)

    def load_latest_checkpoint(self, job_id: str) -> Path | None:
        """Materialize and return the highest manifest-published checkpoint."""
        published = self._published_steps(job_id)
        self._materialize_metrics(job_id)
        if not published:
            return None
        step = max(published)
        return self._materialize_checkpoint(job_id, step, self._get_checkpoint_manifest(job_id, step))

    def checkpoint_paths(self, job_id: str) -> list[tuple[int, Path]]:
        """Return all published checkpoint wrappers in numeric order.

        This LocalStorage-compatible helper lets existing checkpoint/resume
        callers work against the disposable cache without changing trainer
        code.  The manifests in S3 remain the source of truth.
        """
        self._materialize_metrics(job_id)
        return [
            (step, self._materialize_checkpoint(job_id, step, self._get_checkpoint_manifest(job_id, step)))
            for step in self._published_steps(job_id)
        ]

    def prune_native_checkpoints(self, job_id: str, *, retain_step: int) -> list[int]:
        """Prune only disposable cached native payloads, retaining HF exports."""
        pruned: list[int] = []
        for step, wrapper in self.checkpoint_paths(job_id):
            if step == retain_step:
                continue
            native = wrapper / f"global_step_{step}"
            if not native.is_dir():
                continue
            hf_export = native / "actor" / "huggingface"
            if hf_export.is_dir():
                for child in native.iterdir():
                    if child.name != "actor":
                        _remove_path(child)
                        continue
                    for actor_child in child.iterdir():
                        if actor_child.name != "huggingface":
                            _remove_path(actor_child)
            else:
                shutil.rmtree(native)
            pruned.append(step)
        return pruned

    def append_metrics(self, job_id: str, record: dict) -> None:
        """Append one immutable JSON record; no S3 object is ever rewritten."""
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
        step = _record_step(record)
        key = self._key(job_id, "metrics.jsonl", f"{step:012d}-{uuid.uuid4().hex}.json")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=encoded, ContentType="application/json")

        metrics_path = self._job_dir(job_id) / "metrics.jsonl"
        with metrics_path.open("ab") as handle:
            handle.write(encoded)

    def read_metrics(self, job_id: str) -> list[dict[str, Any]]:
        """Read immutable metric records in stable step/key order."""
        records: list[tuple[int, str, dict[str, Any]]] = []
        for key in self._list_keys(self._key(job_id, "metrics.jsonl") + "/"):
            payload = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            record = json.loads(payload.decode("utf-8"))
            if not isinstance(record, dict):
                raise ValueError(f"metric object {key!r} is not a JSON object")
            records.append((_record_step(record), key, record))
        return [record for _, _, record in sorted(records, key=lambda item: (item[0], item[1]))]

    def put_artifact(self, job_id: str, name: str, path: Path) -> None:
        """Upload an artifact generation and atomically publish its manifest."""
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        name_parts = _safe_relative_parts(name)
        generation = uuid.uuid4().hex
        files = list(_tree_files(source))
        payload: list[dict[str, Any]] = []
        for relative_path, source_file in files:
            key = self._key(job_id, "artifacts", ".tmp", generation, *name_parts, relative_path)
            identity = _file_identity(source_file)
            self._put_file(key, source_file)
            payload.append({"path": relative_path, "key": key, **identity})
        manifest = {
            "schema_version": 1,
            "kind": "directory" if source.is_dir() else "file",
            "name": name,
            "files": payload,
        }
        self._put_json(self._artifact_manifest_key(job_id, name_parts), manifest)

    def get_artifact(self, job_id: str, name: str, dest: Path) -> Path:
        """Download the manifest-published artifact into ``dest``."""
        name_parts = _safe_relative_parts(name)
        manifest = self._get_json(self._artifact_manifest_key(job_id, name_parts))
        files = _manifest_files(manifest)
        requested_destination = Path(dest)
        if manifest.get("kind") == "directory":
            destination = requested_destination / name_parts[-1] if requested_destination.is_dir() else requested_destination
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            temporary.mkdir(parents=True, exist_ok=False)
            try:
                self._download_manifest_files(files, temporary)
                _replace_tree(temporary, destination)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
            return destination

        if len(files) != 1:
            raise ValueError(f"file artifact {name!r} has {len(files)} files")
        destination = requested_destination / name_parts[-1] if requested_destination.is_dir() else requested_destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        self._download_one(files[0], temporary)
        os.replace(temporary, destination)
        return destination

    def _job_dir(self, job_id: str) -> Path:
        destination = self.root / job_id
        destination.mkdir(parents=True, exist_ok=True)
        return destination

    def _key(self, job_id: str, *parts: str) -> str:
        safe_job = _safe_job_id(job_id)
        values = [value.strip("/") for value in parts if value.strip("/")]
        prefix = [self.prefix] if self.prefix else []
        return "/".join([*prefix, "jobs", safe_job, *values])

    def _checkpoint_manifest_key(self, job_id: str, step: int) -> str:
        return self._key(job_id, "ckpt", f"step_{step}", _MANIFEST_NAME)

    def _artifact_manifest_key(self, job_id: str, name_parts: tuple[str, ...]) -> str:
        parent = name_parts[:-1]
        leaf = name_parts[-1] + _ARTIFACT_MANIFEST_SUFFIX
        return self._key(job_id, "artifacts", *parent, leaf)

    def _put_file(self, key: str, source: Path) -> None:
        with Path(source).open("rb") as handle:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=handle)

    def _put_json(self, key: str, value: dict[str, Any]) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
            ContentType="application/json",
        )

    def _get_json(self, key: str) -> dict[str, Any]:
        payload = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"manifest {key!r} is not a JSON object")
        return value

    def _get_checkpoint_manifest(self, job_id: str, step: int) -> dict[str, Any]:
        manifest = self._get_json(self._checkpoint_manifest_key(job_id, step))
        if manifest.get("step") != step:
            raise ValueError(f"checkpoint manifest step mismatch for {job_id!r} step {step}")
        _manifest_files(manifest)
        return manifest

    def _published_steps(self, job_id: str) -> list[int]:
        prefix = self._key(job_id, "ckpt") + "/"
        steps: set[int] = set()
        for key in self._list_keys(prefix):
            relative = key[len(prefix) :]
            parts = relative.split("/")
            if len(parts) != 2 or parts[1] != _MANIFEST_NAME:
                continue
            match = _STEP_DIRECTORY.fullmatch(parts[0])
            if match:
                steps.add(int(match.group(1)))
        return sorted(steps)

    def _list_keys(self, prefix: str) -> Iterable[str]:
        continuation: str | None = None
        while True:
            arguments: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if continuation is not None:
                arguments["ContinuationToken"] = continuation
            response = self.client.list_objects_v2(**arguments)
            for entry in response.get("Contents", []):
                key = entry.get("Key")
                if isinstance(key, str):
                    yield key
            if not response.get("IsTruncated"):
                return
            continuation = response.get("NextContinuationToken")
            if not isinstance(continuation, str):
                raise RuntimeError("truncated S3 listing did not return a continuation token")

    def _materialize_checkpoint(self, job_id: str, step: int, manifest: dict[str, Any]) -> Path:
        destination = self._job_dir(job_id) / "ckpt" / f"step_{step}"
        temporary = destination.parent / f".step_{step}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            self._download_manifest_files(_manifest_files(manifest), temporary)
            _replace_tree(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return destination

    def _materialize_metrics(self, job_id: str) -> Path:
        destination = self._job_dir(job_id) / "metrics.jsonl"
        records = self.read_metrics(job_id)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
                handle.write("\n")
        os.replace(temporary, destination)
        return destination

    def _download_manifest_files(self, files: list[dict[str, Any]], destination: Path) -> None:
        for entry in files:
            relative = _safe_manifest_path(entry["path"])
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._download_one(entry, target)

    def _download_one(self, entry: dict[str, Any], destination: Path) -> None:
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("manifest file has no S3 key")
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        payload = response["Body"].read()
        expected_size = entry.get("size_bytes")
        expected_sha = entry.get("sha256")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise ValueError("manifest file has invalid size_bytes")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ValueError("manifest file has invalid sha256")
        if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ValueError(f"S3 object identity mismatch for {key!r}")
        Path(destination).write_bytes(payload)


def _new_s3_client(region_name: str | None) -> Any:
    try:
        import boto3
    except ModuleNotFoundError as error:  # pragma: no cover - dependency failure boundary.
        raise RuntimeError("S3Storage requires boto3; install requirements-trainer.txt") from error
    return boto3.client("s3", region_name=region_name)


def _safe_job_id(job_id: str) -> str:
    if not job_id or "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
        raise ValueError("job_id must be a non-empty path component")
    return job_id


def _safe_relative_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if (
        not name
        or not path.parts
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or any(not part for part in path.parts)
    ):
        raise ValueError("artifact name must be a relative path")
    return tuple(path.parts)


def _safe_manifest_path(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("manifest file path must be text")
    path = PurePosixPath(value)
    if not value or not path.parts or path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ValueError("manifest file path must be relative")
    return path


def _tree_files(source: Path) -> Iterable[tuple[str, Path]]:
    source = Path(source)
    if source.is_file() or source.is_symlink() and source.resolve().is_file():
        yield source.name, source
        return
    if not source.is_dir():
        raise ValueError(f"storage source must be a file or directory: {source}")
    for root, _directories, filenames in os.walk(source, followlinks=True):
        root_path = Path(root)
        for filename in sorted(filenames):
            candidate = root_path / filename
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(source).as_posix()
            yield relative, candidate


def _file_identity(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def _manifest_files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest files must be a list")
    validated: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("manifest file entry must be an object")
        _safe_manifest_path(entry.get("path"))
        validated.append(entry)
    return validated


def _record_step(record: dict[str, Any]) -> int:
    value = record.get("step", 0)
    if isinstance(value, bool):
        raise ValueError("metric step must be an integer")
    try:
        step = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("metric step must be an integer") from error
    if step < 0:
        raise ValueError("metric step must be non-negative")
    return step


def _replace_tree(temporary: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        previous = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.old")
        os.rename(destination, previous)
        try:
            os.rename(temporary, destination)
        except Exception:
            os.rename(previous, destination)
            raise
        shutil.rmtree(previous)
    else:
        os.rename(temporary, destination)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
