"""Active Memory Pointer 与不可变 Snapshot Chain 仓储。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.memory.operations import EMPTY_CORE, MemoryDocument, MemoryOperation
from app.ai_chat.memory.run_bundles import RunBundle
from app.ai_chat.models import (
    AiChatConversationMemory,
    AiChatConversationMemorySnapshot,
    utcnow_iso,
)


class MemoryRepository:
    """在调用方事务内维护唯一的规范 Snapshot Chain。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_pointer(
        self, conversation_id: int
    ) -> AiChatConversationMemory | None:
        result = await self._session.execute(
            select(AiChatConversationMemory).where(
                AiChatConversationMemory.conversation_id == conversation_id
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self, conversation_id: int
    ) -> tuple[AiChatConversationMemory, AiChatConversationMemorySnapshot]:
        pointer = await self.get_pointer(conversation_id)
        if pointer is not None:
            active = await self._session.get(
                AiChatConversationMemorySnapshot, pointer.active_snapshot_id
            )
            if active is None:
                raise RuntimeError("memory pointer references a missing snapshot")
            return pointer, active
        root = AiChatConversationMemorySnapshot(
            conversation_id=conversation_id,
            parent_snapshot_id=None,
            source_run_id=None,
            source_bundle_hash=None,
            covered_through_sequence=0,
            operations=[],
            core=dict(EMPTY_CORE),
            other={},
            memory_token_count=0,
        )
        self._session.add(root)
        await self._session.flush()
        pointer = AiChatConversationMemory(
            conversation_id=conversation_id,
            active_snapshot_id=root.id,
        )
        self._session.add(pointer)
        await self._session.flush()
        return pointer, root

    async def snapshot(self, snapshot_id: int) -> AiChatConversationMemorySnapshot | None:
        return await self._session.get(AiChatConversationMemorySnapshot, snapshot_id)

    async def chain_from(
        self, snapshot_id: int
    ) -> list[AiChatConversationMemorySnapshot]:
        """返回给定节点之后的连续唯一子链。"""
        chain: list[AiChatConversationMemorySnapshot] = []
        parent_id = snapshot_id
        while True:
            result = await self._session.execute(
                select(AiChatConversationMemorySnapshot).where(
                    AiChatConversationMemorySnapshot.parent_snapshot_id == parent_id
                )
            )
            child = result.scalar_one_or_none()
            if child is None:
                return chain
            chain.append(child)
            parent_id = child.id

    async def create_child(
        self,
        *,
        parent: AiChatConversationMemorySnapshot,
        bundle: RunBundle,
        operations: list[MemoryOperation],
        document: MemoryDocument,
        memory_token_count: int,
    ) -> AiChatConversationMemorySnapshot:
        """在 Parent 后追加一个已完整校验的不可变节点。"""
        if bundle.last_sequence <= parent.covered_through_sequence:
            raise ValueError("snapshot waterline must move forward")
        row = AiChatConversationMemorySnapshot(
            conversation_id=parent.conversation_id,
            parent_snapshot_id=parent.id,
            source_run_id=bundle.run_id,
            source_bundle_hash=bundle.stable_hash(),
            covered_through_sequence=bundle.last_sequence,
            operations=[operation.model_dump(mode="json") for operation in operations],
            core=document.core_json(),
            other=document.other,
            memory_token_count=memory_token_count,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def acquire_lease(
        self,
        conversation_id: int,
        owner: str,
        *,
        ttl_seconds: int = 120,
    ) -> bool:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        result = await self._session.execute(
            update(AiChatConversationMemory)
            .where(
                AiChatConversationMemory.conversation_id == conversation_id,
                or_(
                    AiChatConversationMemory.lease_owner.is_(None),
                    AiChatConversationMemory.lease_expires_at < now.isoformat(),
                    AiChatConversationMemory.lease_owner == owner,
                ),
            )
            .values(lease_owner=owner, lease_expires_at=expires, updated_at=utcnow_iso())
        )
        await self._session.flush()
        return result.rowcount == 1

    async def release_lease(self, conversation_id: int, owner: str) -> None:
        await self._session.execute(
            update(AiChatConversationMemory)
            .where(
                AiChatConversationMemory.conversation_id == conversation_id,
                AiChatConversationMemory.lease_owner == owner,
            )
            .values(lease_owner=None, lease_expires_at=None, updated_at=utcnow_iso())
        )
        await self._session.flush()

    async def promote(
        self,
        *,
        conversation_id: int,
        expected_active_id: int,
        target: AiChatConversationMemorySnapshot,
    ) -> bool:
        """CAS 前移 Active，并在同一事务 Rebase 已覆盖祖先。"""
        ancestors: list[int] = []
        cursor = target
        while cursor.parent_snapshot_id is not None:
            parent_id = cursor.parent_snapshot_id
            ancestors.append(parent_id)
            if parent_id == expected_active_id:
                break
            parent = await self.snapshot(parent_id)
            if parent is None or parent.conversation_id != conversation_id:
                return False
            cursor = parent
        else:
            return False
        result = await self._session.execute(
            update(AiChatConversationMemory)
            .where(
                AiChatConversationMemory.conversation_id == conversation_id,
                AiChatConversationMemory.active_snapshot_id == expected_active_id,
            )
            .values(active_snapshot_id=target.id, updated_at=utcnow_iso())
        )
        if result.rowcount != 1:
            return False
        target.parent_snapshot_id = None
        await self._session.flush()
        await self._session.execute(
            delete(AiChatConversationMemorySnapshot).where(
                AiChatConversationMemorySnapshot.id.in_(ancestors)
            )
        )
        await self._session.flush()
        return True

    async def delete_descendants_after(
        self, snapshot_id: int, *, keep: int
    ) -> None:
        chain = await self.chain_from(snapshot_id)
        if len(chain) <= keep:
            return
        await self._session.execute(
            delete(AiChatConversationMemorySnapshot).where(
                AiChatConversationMemorySnapshot.id == chain[keep].id
            )
        )
        await self._session.flush()
