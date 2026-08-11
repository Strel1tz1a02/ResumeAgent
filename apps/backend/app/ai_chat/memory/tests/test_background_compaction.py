"""Outbox、ARQ 投递与独立 Memory Worker 契约测试。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, func, select

from app.ai_chat.memory import worker as memory_worker
from app.ai_chat.repositories import RepositoryFactory
from app.background_jobs.dispatcher import OutboxDispatcher, OutboxRoute
from app.background_jobs.models import BackgroundJobOutbox
from app.background_jobs.repository import OutboxRepository
from app.scripts.migrate_ai_chat_memory_background import migrate


async def _conversation(isolated_db) -> int:  # type: ignore[no-untyped-def]
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).conversations.create(
            adapter="TestAdapter",
            subject={"type": "experience", "id": "1"},
            scope={"field": "background"},
            language="zh",
        )
        await session.commit()
        return row.id


async def _running_run(isolated_db, conversation_id: int) -> int:  # type: ignore[no-untyped-def]
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).runs.create(
            conversation_id=conversation_id,
            kind="user_turn",
            tools_enabled=True,
        )
        await session.commit()
        return row.id


async def test_terminal_run_and_outbox_share_one_transaction(isolated_db) -> None:  # type: ignore[no-untyped-def]
    conversation_id = await _conversation(isolated_db)
    run_id = await _running_run(isolated_db, conversation_id)

    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        assert await repositories.runs.transition(
            run_id,
            from_statuses={"running"},
            to_status="completed",
        )
        assert await session.scalar(
            select(func.count()).select_from(BackgroundJobOutbox)
        ) == 1
        await session.rollback()

    async with isolated_db.session() as session:
        run = await RepositoryFactory().create(session).runs.get(run_id)
        assert run is not None and run.status == "running"
        assert await session.scalar(
            select(func.count()).select_from(BackgroundJobOutbox)
        ) == 0

    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        assert await repositories.runs.transition(
            run_id,
            from_statuses={"running"},
            to_status="completed",
        )
        await session.commit()

    async with isolated_db.session() as session:
        event = (
            await session.execute(select(BackgroundJobOutbox))
        ).scalar_one()
        assert event.topic == "memory.compact"
        assert event.dedupe_key == f"memory.compact:{run_id}"
        assert event.payload == {"run_id": run_id}


async def test_non_history_terminal_run_does_not_enqueue_memory(isolated_db) -> None:  # type: ignore[no-untyped-def]
    conversation_id = await _conversation(isolated_db)
    run_id = await _running_run(isolated_db, conversation_id)

    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        assert await repositories.runs.transition(
            run_id,
            from_statuses={"running"},
            to_status="cancelled",
        )
        await session.commit()
    async with isolated_db.session() as session:
        assert await session.scalar(
            select(func.count()).select_from(BackgroundJobOutbox)
        ) == 0


async def test_dispatcher_publishes_stable_arq_job(isolated_db) -> None:  # type: ignore[no-untyped-def]
    class _Redis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        async def enqueue_job(
            self,
            function: str,
            *args: Any,
            **kwargs: Any,
        ) -> object:
            self.calls.append((function, args, kwargs))
            return object()

    async with isolated_db.session() as session:
        event = await OutboxRepository(session).enqueue(
            topic="memory.compact",
            dedupe_key="memory.compact:41",
            payload={"run_id": 41},
        )
        await session.commit()

    redis = _Redis()
    dispatcher = OutboxDispatcher(
        {
            "memory.compact": OutboxRoute(
                function="compact_memory",
                queue_name="ai-chat:memory",
            )
        }
    )
    assert await dispatcher.dispatch_once(redis) == 1
    assert redis.calls == [
        (
            "compact_memory",
            (event.id, {"run_id": 41}),
            {
                "_job_id": "memory.compact:41",
                "_queue_name": "ai-chat:memory",
            },
        )
    ]
    async with isolated_db.session() as session:
        stored = await OutboxRepository(session).get(event.id)
        assert stored is not None and stored.status == "published"


async def test_dispatcher_keeps_event_when_redis_is_unavailable(isolated_db) -> None:  # type: ignore[no-untyped-def]
    class _Redis:
        async def enqueue_job(self, *_args: Any, **_kwargs: Any) -> None:
            raise ConnectionError("redis unavailable")

    async with isolated_db.session() as session:
        event = await OutboxRepository(session).enqueue(
            topic="memory.compact",
            dedupe_key="memory.compact:unavailable",
            payload={"run_id": 44},
        )
        await session.commit()

    dispatcher = OutboxDispatcher(
        {
            "memory.compact": OutboxRoute(
                function="compact_memory",
                queue_name="ai-chat:memory",
            )
        }
    )
    assert await dispatcher.dispatch_once(_Redis()) == 0

    async with isolated_db.session() as session:
        stored = await OutboxRepository(session).get(event.id)
        assert stored is not None
        assert stored.status == "pending"
        assert stored.publish_attempts == 1
        assert stored.last_error == "redis unavailable"


async def test_worker_marks_outbox_processed_after_snapshot(
    isolated_db,  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    calls: list[int] = []

    class _MemoryService:
        async def compact_run(self, run_id: int) -> bool:
            calls.append(run_id)
            return True

    class _Lock:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Redis:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def lock(self, key: str, **_kwargs: Any) -> _Lock:
            self.keys.append(key)
            return _Lock()

    async def conversation_id(_run_id: int) -> int:
        return 9

    monkeypatch.setattr(memory_worker, "MemoryService", _MemoryService)
    monkeypatch.setattr(memory_worker, "_conversation_id", conversation_id)
    async with isolated_db.session() as session:
        event = await OutboxRepository(session).enqueue(
            topic="memory.compact",
            dedupe_key="memory.compact:42",
            payload={"run_id": 42},
        )
        await session.commit()

    redis = _Redis()
    await memory_worker.compact_memory(
        {"redis": redis},
        event.id,
        {"run_id": 42},
    )

    assert calls == [42]
    assert redis.keys == ["ai-chat:memory:conversation:9"]
    async with isolated_db.session() as session:
        stored = await OutboxRepository(session).get(event.id)
        assert stored is not None and stored.status == "processed"


def test_memory_background_migration_upgrades_failed_placeholder(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """旧 failed 占位变回 pending，并获得可恢复的 Outbox 事件。"""
    path = tmp_path / "legacy-memory.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ai_chat_runs (id INTEGER PRIMARY KEY,status VARCHAR(16))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE ai_chat_run_memories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER UNIQUE NOT NULL,"
            "status VARCHAR(16) NOT NULL,core JSON NOT NULL,other JSON NOT NULL,"
            "memory_token_count INTEGER NOT NULL,error_message TEXT,"
            "CONSTRAINT ck_ai_chat_run_memory_status CHECK "
            "(status IN ('pending','completed','failed')))"
        )
        connection.exec_driver_sql(
            "INSERT INTO ai_chat_runs (id,status) VALUES (7,'completed')"
        )
        connection.exec_driver_sql(
            "INSERT INTO ai_chat_run_memories "
            "(run_id,status,core,other,memory_token_count,error_message) "
            "VALUES (7,'failed','{}','{}',0,'old failure')"
        )

    migrate(engine)

    with engine.connect() as connection:
        table_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='ai_chat_run_memories'"
        ).scalar_one()
        memory_status = connection.exec_driver_sql(
            "SELECT status FROM ai_chat_run_memories WHERE run_id=7"
        ).scalar_one()
        outbox = connection.exec_driver_sql(
            "SELECT topic,dedupe_key,status FROM background_job_outbox"
        ).one()

    assert "'skipped'" in table_sql
    assert memory_status == "pending"
    assert outbox == ("memory.compact", "memory.compact:7", "pending")
    engine.dispose()
