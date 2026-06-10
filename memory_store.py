import json
import re
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


VALID_MEMORY_TYPES = {
    "note",
    "lesson",
    "fact",
    "scene",
    "preference",
    "warning",
    "calibration",
}


class MemoryStoreError(ValueError):
    """Raised when persistent memory cannot be read or updated."""


def memory_log(msg: str) -> None:
    """Log memory store messages with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [MEMORY] {msg}")


def utcish_now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class MemoryStore:
    """File-backed persistent memory for operator notes and AI lessons."""

    def __init__(
        self,
        enabled: bool = True,
        path: str = "data/memory.json",
        max_memories: int = 500,
        prompt_limit: int = 8,
        max_text_chars: int = 500,
    ):
        self.enabled = bool(enabled)
        self.path = Path(path or "data/memory.json")
        self.max_memories = max(1, int(max_memories))
        self.prompt_limit = max(1, int(prompt_limit))
        self.max_text_chars = max(40, int(max_text_chars))
        self._lock = RLock()
        self._data = {"version": 1, "next_id": 1, "memories": []}
        if self.enabled:
            self._load()
        memory_log(
            "Initialized "
            f"enabled={self.enabled} path={self.path} "
            f"max_memories={self.max_memories} prompt_limit={self.prompt_limit}"
        )

    def get_status(self) -> Dict[str, Any]:
        """Return memory store status for diagnostics."""
        with self._lock:
            memories = self._data.get("memories", [])
            active = [memory for memory in memories if not memory.get("archived")]
            return {
                "status": "success",
                "enabled": self.enabled,
                "path": str(self.path),
                "count": len(active),
                "archived_count": len(memories) - len(active),
                "max_memories": self.max_memories,
                "prompt_limit": self.prompt_limit,
            }

    def add(
        self,
        text: str,
        memory_type: str = "note",
        source: str = "operator",
        tags: Optional[List[str]] = None,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a persistent memory entry."""
        if not self.enabled:
            raise MemoryStoreError("memory store is disabled")

        text = self._validate_text(text)
        memory_type = self._validate_type(memory_type)
        source = self._validate_source(source)
        tags = self._normalize_tags(tags)
        confidence = self._normalize_confidence(confidence)
        if metadata is not None and not isinstance(metadata, dict):
            raise MemoryStoreError("metadata must be a JSON object")

        with self._lock:
            now = utcish_now()
            memory = {
                "id": self._data["next_id"],
                "type": memory_type,
                "source": source,
                "text": text,
                "tags": tags,
                "confidence": confidence,
                "created_at": now,
                "updated_at": now,
                "last_used_at": None,
                "access_count": 0,
                "archived": False,
                "metadata": deepcopy(metadata or {}),
            }
            self._data["next_id"] += 1
            self._data["memories"].append(memory)
            self._trim_locked()
            self._save_locked()
            memory_log(f"Added memory id={memory['id']} type={memory_type} source={source}")
            return {"status": "success", "memory": deepcopy(memory)}

    def list_memories(
        self,
        limit: int = 50,
        query: str = "",
        memory_type: Optional[str] = None,
        include_archived: bool = False,
    ) -> Dict[str, Any]:
        """List memories, optionally filtered by query and type."""
        limit = self._positive_int(limit, "limit", upper=500)
        query = query if isinstance(query, str) else ""
        if memory_type:
            memory_type = self._validate_type(memory_type)

        with self._lock:
            memories = list(self._data.get("memories", []))
            if not include_archived:
                memories = [memory for memory in memories if not memory.get("archived")]
            if memory_type:
                memories = [memory for memory in memories if memory.get("type") == memory_type]
            if query.strip():
                terms = self._terms(query)
                scored = [
                    (self._score_memory(memory, terms), memory)
                    for memory in memories
                ]
                memories = [memory for score, memory in scored if score > 0]
                memories.sort(key=lambda item: self._score_memory(item, terms), reverse=True)
            else:
                memories.sort(
                    key=lambda memory: memory.get("updated_at") or memory.get("created_at") or "",
                    reverse=True,
                )

            return {
                "status": "success",
                "count": len(memories),
                "memories": deepcopy(memories[:limit]),
            }

    def relevant_memories(
        self,
        query: str = "",
        limit: Optional[int] = None,
        include_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return compact memories most relevant to the current goal."""
        if not self.enabled:
            return {"status": "success", "count": 0, "memories": []}

        limit = self.prompt_limit if limit is None else self._positive_int(limit, "limit", upper=50)
        terms = self._terms(query)
        include_type_set = None
        if include_types:
            include_type_set = {self._validate_type(item) for item in include_types}

        with self._lock:
            candidates = [
                memory
                for memory in self._data.get("memories", [])
                if not memory.get("archived")
                and (include_type_set is None or memory.get("type") in include_type_set)
            ]
            scored = [(self._score_memory(memory, terms), memory) for memory in candidates]
            scored = [
                (score, memory)
                for score, memory in scored
                if score > 0 or not terms
            ]
            scored.sort(
                key=lambda item: (
                    item[0],
                    item[1].get("confidence", 0),
                    item[1].get("updated_at") or item[1].get("created_at") or "",
                ),
                reverse=True,
            )
            selected = [self._compact_memory(memory) for _, memory in scored[:limit]]
            return {"status": "success", "count": len(selected), "memories": selected}

    def archive(self, memory_id: int, archived: bool = True) -> Dict[str, Any]:
        """Archive or unarchive one memory."""
        if not self.enabled:
            raise MemoryStoreError("memory store is disabled")

        memory_id = self._positive_int(memory_id, "memory_id")
        with self._lock:
            memory = self._find_locked(memory_id)
            memory["archived"] = bool(archived)
            memory["updated_at"] = utcish_now()
            self._save_locked()
            return {"status": "success", "memory": deepcopy(memory)}

    def delete(self, memory_id: int) -> Dict[str, Any]:
        """Delete one memory entry."""
        if not self.enabled:
            raise MemoryStoreError("memory store is disabled")

        memory_id = self._positive_int(memory_id, "memory_id")
        with self._lock:
            before = len(self._data["memories"])
            self._data["memories"] = [
                memory for memory in self._data["memories"] if memory.get("id") != memory_id
            ]
            if len(self._data["memories"]) == before:
                raise MemoryStoreError(f"memory {memory_id} not found")
            self._save_locked()
            return {"status": "success", "deleted": memory_id}

    def mark_used(self, memory_ids: List[int]) -> None:
        """Record prompt use for selected memories."""
        if not self.enabled:
            return

        ids = {int(memory_id) for memory_id in memory_ids}
        with self._lock:
            now = utcish_now()
            changed = False
            for memory in self._data.get("memories", []):
                if memory.get("id") in ids:
                    memory["last_used_at"] = now
                    memory["access_count"] = int(memory.get("access_count", 0)) + 1
                    changed = True
            if changed:
                self._save_locked()

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._save_locked()
                return
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except json.JSONDecodeError as e:
                raise MemoryStoreError(f"memory file is not valid JSON: {e}") from e

            if not isinstance(data, dict):
                raise MemoryStoreError("memory file root must be a JSON object")
            memories = data.get("memories", [])
            if not isinstance(memories, list):
                raise MemoryStoreError("memory file memories must be a list")
            self._data = {
                "version": int(data.get("version", 1)),
                "next_id": max(1, int(data.get("next_id", 1))),
                "memories": memories,
            }
            highest_id = max(
                [int(memory.get("id", 0)) for memory in memories if isinstance(memory, dict)]
                or [0]
            )
            self._data["next_id"] = max(self._data["next_id"], highest_id + 1)

    def _save_locked(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temp_path.replace(self.path)

    def _trim_locked(self) -> None:
        memories = self._data["memories"]
        if len(memories) <= self.max_memories:
            return

        memories.sort(
            key=lambda memory: (
                not bool(memory.get("archived")),
                memory.get("updated_at") or memory.get("created_at") or "",
            ),
            reverse=True,
        )
        self._data["memories"] = memories[: self.max_memories]

    def _find_locked(self, memory_id: int) -> Dict[str, Any]:
        for memory in self._data.get("memories", []):
            if memory.get("id") == memory_id:
                return memory
        raise MemoryStoreError(f"memory {memory_id} not found")

    def _compact_memory(self, memory: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": memory.get("id"),
            "type": memory.get("type"),
            "source": memory.get("source"),
            "text": memory.get("text"),
            "tags": deepcopy(memory.get("tags", [])),
            "confidence": memory.get("confidence"),
            "created_at": memory.get("created_at"),
        }

    def _score_memory(self, memory: Dict[str, Any], terms: List[str]) -> float:
        if memory.get("archived"):
            return -1.0

        score = float(memory.get("confidence", 0.5))
        memory_type = str(memory.get("type", ""))
        if memory_type in {"lesson", "warning", "preference", "calibration"}:
            score += 0.5

        if not terms:
            return score

        text = str(memory.get("text", "")).lower()
        tags = [str(tag).lower() for tag in memory.get("tags", [])]
        haystack = " ".join([text, memory_type, str(memory.get("source", "")).lower(), *tags])

        matched = 0
        for term in terms:
            if term in tags:
                score += 3.0
                matched += 1
            elif re.search(rf"\b{re.escape(term)}\b", haystack):
                score += 2.0
                matched += 1
            elif term in haystack:
                score += 0.75
                matched += 1

        if matched == 0:
            return 0.0
        return score + (matched / max(1, len(terms)))

    def _terms(self, query: str) -> List[str]:
        if not isinstance(query, str):
            return []
        terms = re.findall(r"[a-zA-Z0-9_]+", query.lower())
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "toward",
            "towards",
            "move",
            "robot",
            "please",
        }
        return [term for term in terms if len(term) > 2 and term not in stop_words][:20]

    def _validate_text(self, text: str) -> str:
        if not isinstance(text, str):
            raise MemoryStoreError("text must be a string")
        text = text.strip()
        if not text:
            raise MemoryStoreError("text is required")
        return text[: self.max_text_chars]

    def _validate_type(self, memory_type: str) -> str:
        if not isinstance(memory_type, str):
            raise MemoryStoreError("memory_type must be a string")
        memory_type = memory_type.strip().lower()
        if memory_type not in VALID_MEMORY_TYPES:
            allowed = ", ".join(sorted(VALID_MEMORY_TYPES))
            raise MemoryStoreError(f"memory_type must be one of: {allowed}")
        return memory_type

    def _validate_source(self, source: str) -> str:
        if not isinstance(source, str):
            raise MemoryStoreError("source must be a string")
        source = source.strip().lower()
        if not source:
            raise MemoryStoreError("source is required")
        return source[:40]

    def _normalize_tags(self, tags: Optional[List[str]]) -> List[str]:
        if tags is None:
            return []
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",")]
        if not isinstance(tags, list):
            raise MemoryStoreError("tags must be a list or comma-separated string")

        normalized = []
        for tag in tags:
            if not isinstance(tag, str):
                continue
            tag = re.sub(r"[^a-zA-Z0-9_-]+", "-", tag.strip().lower()).strip("-")
            if tag and tag not in normalized:
                normalized.append(tag[:32])
        return normalized[:12]

    def _normalize_confidence(self, confidence: float) -> float:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MemoryStoreError("confidence must be a number")
        return round(max(0.0, min(1.0, float(confidence))), 3)

    def _positive_int(self, value: Any, name: str, upper: int = 100000) -> int:
        try:
            if isinstance(value, bool):
                raise TypeError
            number = int(value)
        except (TypeError, ValueError):
            raise MemoryStoreError(f"{name} must be an integer")
        if number < 1:
            raise MemoryStoreError(f"{name} must be positive")
        return min(number, upper)
