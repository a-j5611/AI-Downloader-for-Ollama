"""模型磁盘数据管理：彻底删除模型、清理未使用数据。

Ollama 的 `DELETE /api/delete` 只删清单（manifest），blob 数据会留在磁盘上
（要等下次服务启动才可能回收）。这里直接按磁盘结构精确清理：

    <models>/manifests/<registry>/<namespace>/<name>/<tag>   清单，内容引用下面的 blob
    <models>/blobs/sha256-<hex>                              实际数据

定位方式：清单文件的 SHA256 == `/api/tags` 返回的 digest（已实测一致），
因此可以精确找到某个模型对应的清单，再取出它引用的所有 blob。

**共享保护**：多个模型会共用同一个 blob（例如同一底模的不同 Modelfile 变体），
删除前会计算「被其他模型引用」的集合，只清理无人引用的文件。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

# 刚下载/正在下载的文件不动（避免误删进行中的拉取）
RECENT_GUARD_SECONDS = 600


class ModelStorage:
    def __init__(self, models_dir: str | Path):
        self.dir = Path(models_dir)

    # -------------------------------------------------- 基础路径
    @property
    def blobs_dir(self) -> Path:
        return self.dir / "blobs"

    @property
    def manifests_dir(self) -> Path:
        return self.dir / "manifests"

    def blob_file(self, digest: str) -> Path:
        return self.blobs_dir / digest.replace(":", "-")

    def readable(self) -> bool:
        return self.dir.exists()

    # -------------------------------------------------- 清单
    @staticmethod
    def manifest_sha(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception:
            return ""

    def manifest_index(self) -> dict[str, Path]:
        """{清单 sha256: 清单路径}"""
        index: dict[str, Path] = {}
        try:
            for path in self.manifests_dir.rglob("*"):
                if path.is_file():
                    sha = self.manifest_sha(path)
                    if sha:
                        index[sha] = path
        except Exception:
            pass
        return index

    def manifest_refs(self, path: Path) -> set[str]:
        """清单里引用的 blob digest 集合（config + layers）。"""
        refs: set[str] = set()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return refs
        cfg = (doc.get("config") or {}).get("digest")
        if cfg:
            refs.add(cfg)
        for layer in doc.get("layers") or []:
            digest = layer.get("digest")
            if digest:
                refs.add(digest)
        return refs

    def all_refs(self) -> dict[str, set[str]]:
        """{清单 sha256: 引用的 blob digest 集合}"""
        out: dict[str, set[str]] = {}
        for sha, path in self.manifest_index().items():
            out[sha] = self.manifest_refs(path)
        return out

    # -------------------------------------------------- 体积
    def blob_size(self, digest: str) -> int:
        path = self.blob_file(digest)
        try:
            return path.stat().st_size
        except Exception:
            return 0

    def dir_usage(self) -> int:
        total = 0
        for root in (self.blobs_dir, self.manifests_dir):
            try:
                for path in root.rglob("*"):
                    if path.is_file():
                        total += path.stat().st_size
            except Exception:
                pass
        return total

    # -------------------------------------------------- 计划
    def plan_delete(self, model_digest: str) -> dict:
        """计算「完全删除某模型」会删掉哪些文件、释放多少空间。

        返回 {manifest, files:[(Path,size)], freed, shared_size, shared_count}
        """
        index = self.manifest_index()
        manifest = index.get(model_digest)
        refs = self.manifest_refs(manifest) if manifest else set()

        others: set[str] = set()
        for sha, other_refs in self.all_refs().items():
            if sha == model_digest:
                continue
            others |= other_refs

        files: list[tuple[Path, int]] = []
        freed = 0
        shared_size = 0
        shared_count = 0
        for digest in sorted(refs):
            path = self.blob_file(digest)
            if not path.exists():
                continue
            size = path.stat().st_size
            if digest in others:
                shared_size += size
                shared_count += 1
                continue
            files.append((path, size))
            freed += size
        # 清单文件本身也算进去
        if manifest and manifest.exists():
            size = manifest.stat().st_size
            files.append((manifest, size))
            freed += size
        return {"manifest": manifest, "files": files, "freed": freed,
                "shared_size": shared_size, "shared_count": shared_count,
                "ref_count": len(refs)}

    def plan_orphans(self, known_manifest_shas: set[str] | None = None) -> dict:
        """找出无人引用的 blob（以及可选的失效清单）。

        known_manifest_shas: `/api/tags` 返回的 digest 集合；
        为 None 时只清理 blob，不动清单。
        """
        all_refs = self.all_refs()
        used: set[str] = set()
        for refs in all_refs.values():
            used |= refs

        now = time.time()
        files: list[tuple[Path, int]] = []
        freed = 0
        skipped_recent = 0
        try:
            for path in self.blobs_dir.iterdir():
                if not path.is_file():
                    continue
                name = path.name
                if not name.startswith("sha256-") or "partial" in name:
                    continue
                digest = "sha256:" + name[len("sha256-"):]
                if digest in used:
                    continue
                try:
                    stat = path.stat()
                except Exception:
                    continue
                if now - stat.st_mtime < RECENT_GUARD_SECONDS:
                    skipped_recent += 1
                    continue
                files.append((path, stat.st_size))
                freed += stat.st_size
        except Exception:
            pass

        orphan_manifests: list[tuple[Path, int]] = []
        if known_manifest_shas is not None:
            for sha, path in self.manifest_index().items():
                if sha in known_manifest_shas:
                    continue
                try:
                    size = path.stat().st_size
                except Exception:
                    continue
                orphan_manifests.append((path, size))

        return {"files": files, "freed": freed, "skipped_recent": skipped_recent,
                "orphan_manifests": orphan_manifests}

    # -------------------------------------------------- 未完成的下载
    def partials(self) -> list[dict]:
        """列出未完成下载留下的分片文件（下载中断后用于续传或清理）。"""
        out: list[dict] = []
        try:
            for path in self.blobs_dir.iterdir():
                if not path.is_file() or "partial" not in path.name:
                    continue
                try:
                    stat = path.stat()
                except Exception:
                    continue
                out.append({"path": path, "size": stat.st_size,
                            "mtime": stat.st_mtime,
                            "digest": path.name.split("-partial")[0]})
        except Exception:
            pass
        out.sort(key=lambda d: -d["size"])
        return out

    def partials_size(self) -> int:
        return sum(d["size"] for d in self.partials())

    def remove_partials(self) -> dict:
        """删除所有未完成下载的分片（用于无法续传时彻底重来）。"""
        return self.remove([(d["path"], d["size"]) for d in self.partials()])

    # -------------------------------------------------- 执行
    @staticmethod
    def remove(files: list[tuple[Path, int]]) -> dict:
        removed = 0
        freed = 0
        errors: list[str] = []
        for path, size in files:
            try:
                path.unlink()
                removed += 1
                freed += size
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
        return {"removed": removed, "freed": freed, "errors": errors}

    @staticmethod
    def human(num_bytes: float) -> str:
        try:
            num_bytes = float(num_bytes)
        except Exception:
            return "-"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(num_bytes) < 1024.0 or unit == "TB":
                return f"{num_bytes:.2f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
            num_bytes /= 1024.0
        return "-"
