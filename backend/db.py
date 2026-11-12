"""Task persistence behind a swappable repository.

`InMemoryRepository` is the default so the app, the tests and the demo run with
no cloud setup. `FirestoreRepository` is used automatically when a service
account JSON exists at FIREBASE_CREDENTIALS_PATH; firebase-admin is imported
lazily inside that class so the in-memory path needs neither the package nor a
project.

Task ids are uuid4 hex (no dashes), created by models.new_task(); repositories
store whatever id the task carries.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable

from models import Task

COLLECTION = "tasks"
DEFAULT_CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "secrets" / "serviceAccountKey.json"


def _credentials_path() -> Optional[Path]:
    """Credentials file to use, or None when the local path does not exist."""
    raw = (os.environ.get("FIREBASE_CREDENTIALS_PATH") or "").strip()
    path = Path(raw).expanduser() if raw else DEFAULT_CREDENTIALS_PATH
    if not path.is_absolute():
        path = Path.cwd() / path
    return path if path.is_file() else None


@runtime_checkable
class TaskRepository(Protocol):
    """Storage contract used by the API layer."""

    def create(self, task: Task) -> Task: ...

    def get(self, task_id: str) -> Optional[Task]: ...

    def list(self) -> List[Task]: ...

    def update(self, task: Task) -> Task: ...


class InMemoryRepository:
    """Dict-backed store. Thread-safe; hands out deep copies, not live objects."""

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, task: Task) -> Task:
        with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"task {task.id} already exists")
            self._tasks[task.id] = task.model_copy(deep=True)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            stored = self._tasks.get(task_id)
            return stored.model_copy(deep=True) if stored is not None else None

    def list(self) -> List[Task]:
        """Newest first."""
        with self._lock:
            tasks = [stored.model_copy(deep=True) for stored in self._tasks.values()]
        return sorted(tasks, key=lambda task: task.created_at, reverse=True)

    def update(self, task: Task) -> Task:
        with self._lock:
            if task.id not in self._tasks:
                raise KeyError(f"task {task.id} does not exist")
            self._tasks[task.id] = task.model_copy(deep=True)
        return task


class FirestoreRepository:
    """Firebase Firestore store, one document per task in the `tasks` collection."""

    def __init__(self) -> None:
        path = _credentials_path()
        if path is None:
            raise RuntimeError("Firebase credentials file not found")

        # Imported here so the in-memory path never needs firebase-admin installed.
        import firebase_admin
        from firebase_admin import credentials, firestore

        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(credentials.Certificate(str(path)))

        self._db = firestore.client()
        self._collection = self._db.collection(COLLECTION)

    def create(self, task: Task) -> Task:
        document = self._collection.document(task.id)
        if document.get().exists:
            raise ValueError(f"task {task.id} already exists")
        document.set(self._serialize(task))
        return task

    def get(self, task_id: str) -> Optional[Task]:
        snapshot = self._collection.document(task_id).get()
        if not snapshot.exists:
            return None
        return self._deserialize(snapshot.to_dict() or {})

    def list(self) -> List[Task]:
        """Newest first."""
        tasks = [self._deserialize(item.to_dict() or {}) for item in self._collection.stream()]
        return sorted(tasks, key=lambda task: task.created_at, reverse=True)

    def update(self, task: Task) -> Task:
        document = self._collection.document(task.id)
        if not document.get().exists:
            raise KeyError(f"task {task.id} does not exist")
        document.set(self._serialize(task))
        return task

    @staticmethod
    def _serialize(task: Task) -> dict:
        # mode="json" keeps datetimes/enums JSON-safe; by_alias keeps "pass" in AIVerdict.
        return task.model_dump(mode="json", by_alias=True)

    @staticmethod
    def _deserialize(document: dict) -> Task:
        return Task.model_validate(document)


_repository: Optional[TaskRepository] = None


def get_repository() -> TaskRepository:
    """Process-wide repository: Firestore when credentials exist, else in-memory."""
    global _repository
    if _repository is not None:
        return _repository

    if _credentials_path() is None:
        print(
            "[db] No Firebase credentials at FIREBASE_CREDENTIALS_PATH - "
            "using the in-memory store (data is lost on restart)."
        )
        _repository = InMemoryRepository()
        return _repository

    try:
        _repository = FirestoreRepository()
        print(f"[db] Using Firestore collection '{COLLECTION}'.")
    except Exception as exc:  # missing package, bad key, no network/permissions
        print(f"[db] Firestore init failed ({type(exc).__name__}) - using the in-memory store.")
        _repository = InMemoryRepository()
    return _repository
