from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from llm_client import LLMSettings, build_provider, LLMProviderError


class LocalVectorStore:
    def __init__(self, project_root: str | Path, settings: LLMSettings) -> None:
        self.project_root = Path(project_root).resolve()
        self.settings = settings
        self.store_dir = self.project_root / ".axiom" / "vector_store"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.store_dir / "index.json"

        self.provider = build_provider(self.settings)
        self._load_index()

    def _load_index(self) -> None:
        if self.index_file.exists():
            try:
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
                self.documents: list[dict[str, Any]] = data.get("documents", [])
                self.file_hashes: dict[str, str] = data.get("file_hashes", {})
            except json.JSONDecodeError:
                self.documents = []
                self.file_hashes = {}
        else:
            self.documents = []
            self.file_hashes = {}

    def _save_index(self) -> None:
        self.index_file.write_text(json.dumps({"documents": self.documents, "file_hashes": self.file_hashes}, indent=2), encoding="utf-8")

    def _chunk_text(self, text: str, max_chunk_size: int = 1500) -> list[str]:
        # Very simple chunking by paragraphs, keeping chunks under max_chunk_size
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = ""

        for paragraph in paragraphs:
            if len(current_chunk) + len(paragraph) > max_chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            current_chunk += paragraph + "\n\n"

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    def add_document(self, file_path: str, content: str) -> None:
        """Embed and store a document in chunks. Replaces existing chunks for the file."""
        import hashlib
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Skip if file content hasn't changed
        if self.file_hashes.get(file_path) == content_hash:
            return

        # Remove old chunks for this file
        self.documents = [doc for doc in self.documents if doc["path"] != file_path]
        self.file_hashes[file_path] = content_hash

        chunks = self._chunk_text(content)
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            try:
                embedding = self.provider.embed(chunk)
                self.documents.append({
                    "path": file_path,
                    "chunk_index": i,
                    "content": chunk,
                    "embedding": embedding
                })
            except LLMProviderError as e:
                # If we fail to embed one chunk, log/print it and continue or break
                print(f"[VectorStore] Failed to embed chunk {i} of {file_path}: {e}")

    def sync_index(self) -> None:
        """Persist changes to disk."""
        self._save_index()

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Search the vector store using cosine similarity."""
        if not self.documents:
            return []

        try:
            query_embedding = self.provider.embed(query)
        except LLMProviderError as e:
            print(f"[VectorStore] Search query embedding failed: {e}")
            return []

        results = []
        for doc in self.documents:
            similarity = self._cosine_similarity(query_embedding, doc["embedding"])
            results.append({
                "path": doc["path"],
                "content": doc["content"],
                "similarity": similarity
            })

        # Sort by similarity descending
        results.sort(key=lambda x: x["similarity"], reverse=True)

        # Deduplicate by path, keeping the best chunk per file, up to top_k
        deduped = []
        seen_paths = set()
        for r in results:
            if r["path"] not in seen_paths:
                seen_paths.add(r["path"])
                deduped.append(r)
            if len(deduped) >= top_k:
                break

        return deduped
