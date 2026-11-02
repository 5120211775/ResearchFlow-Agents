"""Project memory, with provenance to approved artifacts only."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.memory_chunk import EMBEDDING_DIMENSIONS
from app.models.types import UuidType


class ProjectMemoryItem(Base):
    """One retrievable chunk of approved knowledge. Wholly derived from artifacts."""

    __tablename__ = "project_memory_items"

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    # Vectors from different models are not comparable even at equal width, so retrieval
    # filters on the model that produced them — the rule `memory.retrieve` already enforces.
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    # SHA-256 over the ordered set of source artifact hashes. Makes re-ingestion idempotent
    # without a group-by over the link table; recomputable from `project_memory_provenance`.
    provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "embedding_model",
            "provenance_digest",
            "chunk_index",
            name="uq_memory_chunk",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_memory_chunk"),
        CheckConstraint("length(provenance_digest) = 64", name="ck_memory_digest"),
    )


class ProjectMemoryProvenance(Base):
    """Which approved artifact(s) a memory item derives from.

    The only artifact-side foreign key in the memory subsystem, and therefore the boundary
    itself. Every durable memory item must reach at least one approved ResearchArtifact
    through this table — a rule the database cannot express ("a child row must exist"), so
    that remainder is application-enforced.
    """

    __tablename__ = "project_memory_provenance"

    memory_item_id: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("project_memory_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("research_artifacts.id", ondelete="CASCADE"),
        primary_key=True,
    )
