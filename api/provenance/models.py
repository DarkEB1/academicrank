"""SQLAlchemy 2.0 models.

Node-name convention is dictated by the engine, not by us -- see DECISIONS.md D1.2/D1.6.
Papers are `U` nodes; authors/institutions/topics/venues are `B` nodes. The helpers at
the bottom are the single source of truth for that mapping.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, Float, ForeignKey,
    ForeignKeyConstraint, Index, Integer, SmallInteger, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

class Work(Base):
    __tablename__ = "works"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # e.g. W2963757046
    title: Mapped[Optional[str]] = mapped_column(Text)
    abstract: Mapped[Optional[str]] = mapped_column(Text)
    year: Mapped[Optional[int]] = mapped_column(SmallInteger, index=True)
    publication_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    cited_by_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    doi: Mapped[Optional[str]] = mapped_column(Text)
    type: Mapped[Optional[str]] = mapped_column(String(48))
    language: Mapped[Optional[str]] = mapped_column(String(8))
    is_oa: Mapped[bool] = mapped_column(Boolean, default=False)
    # A stub is a work referenced by fewer than 3 corpus papers: we keep id/title/year
    # so citation edges don't dangle, but it has no authorships/topics of its own.
    is_stub: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id", ondelete="SET NULL"), index=True)
    # in-corpus reference count, denormalised for cheap stats/UI
    ref_count: Mapped[int] = mapped_column(Integer, default=0)
    in_corpus_cited_by: Mapped[int] = mapped_column(Integer, default=0, index=True)
    raw: Mapped[Optional[dict]] = mapped_column(JSONB)
    tsv: Mapped[Optional[str]] = mapped_column(TSVECTOR)
    # 'openalex' for corpus records (including works fetched from OpenAlex during
    # an upload confirm -- those are ordinary corpus rows); 'user_upload' ONLY for
    # UL... local works that exist solely because a user uploaded them. The
    # include_user_uploads visibility filter keys on this.
    source: Mapped[str] = mapped_column(Text, server_default="openalex", default="openalex")

    venue = relationship("Venue", lazy="joined")

    __table_args__ = (
        Index("ix_works_tsv", "tsv", postgresql_using="gin"),
        Index("ix_works_title_trgm", "title", postgresql_using="gin",
              postgresql_ops={"title": "gin_trgm_ops"}),
    )


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text)
    works_count: Mapped[int] = mapped_column(Integer, default=0)
    cited_by_count: Mapped[int] = mapped_column(BigInteger, default=0)
    orcid: Mapped[Optional[str]] = mapped_column(Text)
    # number of corpus papers this author is on -- drives hub damping
    corpus_degree: Mapped[int] = mapped_column(Integer, default=0, index=True)


class Institution(Base):
    __tablename__ = "institutions"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text)
    ror: Mapped[Optional[str]] = mapped_column(Text)
    country_code: Mapped[Optional[str]] = mapped_column(String(8))
    corpus_degree: Mapped[int] = mapped_column(Integer, default=0, index=True)


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text)
    subfield: Mapped[Optional[str]] = mapped_column(Text)
    field: Mapped[Optional[str]] = mapped_column(Text)
    domain: Mapped[Optional[str]] = mapped_column(Text)
    corpus_degree: Mapped[int] = mapped_column(Integer, default=0, index=True)
    # log(N/df): a niche subfield is worth far more than "Mathematics"
    idf: Mapped[float] = mapped_column(Float, default=1.0)


class Venue(Base):
    __tablename__ = "venues"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text)
    type: Mapped[Optional[str]] = mapped_column(String(32))
    issn_l: Mapped[Optional[str]] = mapped_column(String(16))
    publisher: Mapped[Optional[str]] = mapped_column(Text)
    corpus_degree: Mapped[int] = mapped_column(Integer, default=0, index=True)


class WorkAuthor(Base):
    __tablename__ = "work_authors"
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, default=0)


class WorkInstitution(Base):
    __tablename__ = "work_institutions"
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), primary_key=True)


class WorkTopic(Base):
    __tablename__ = "work_topics"
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)


class Citation(Base):
    __tablename__ = "citations"
    src_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    dst_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    __table_args__ = (Index("ix_citations_dst", "dst_id"),)


# --------------------------------------------------------------------------
# Derived graph
# --------------------------------------------------------------------------

class GraphMeta(Base):
    """Singleton row holding the persisted graph generation counter.

    Bumped on every graph mutation (build_graph.py reload, upload confirm/undo) and
    mixed into every score-cache key. See graphmeta.py for why max(graph_edges.id)
    was not good enough.
    """
    __tablename__ = "graph_meta"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    __table_args__ = (CheckConstraint("id = 1", name="ck_graph_meta_singleton"),)


class GraphEdge(Base):
    """Materialised edge list.

    This is what gets pushed to MeritRank via mr_bulk_load_edges, and it is also what
    /explain walks to reconstruct contributing paths -- the engine will not hand back
    paths, so we reconstruct them over exactly the same edge data. Keeping one table
    for both guarantees the explanation matches the graph that produced the score.
    """
    __tablename__ = "graph_edges"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    src: Mapped[str] = mapped_column(String(32), index=True)   # engine node name
    dst: Mapped[str] = mapped_column(String(32), index=True)
    weight: Mapped[float] = mapped_column(Float)
    context: Mapped[str] = mapped_column(String(32), index=True)
    relation: Mapped[str] = mapped_column(String(32), index=True)
    __table_args__ = (
        UniqueConstraint("src", "dst", "context", name="uq_graph_edge"),
    )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # context weights + exposed decay params
    params: Mapped[Optional[dict]] = mapped_column(JSONB)
    warmed_at: Mapped[Optional[dt.datetime]] = mapped_column()

    @property
    def node(self) -> str:
        return profile_node(self.id)


class Trust(Base):
    __tablename__ = "trust"
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    # 1..5 for trust; distrust carries its own flag (see DECISIONS.md on negative edges)
    strength: Mapped[int] = mapped_column(SmallInteger, default=3)
    is_distrust: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ReadMark(Base):
    __tablename__ = "read_marks"
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)


# --------------------------------------------------------------------------
# Uploads (PDF bibliography -> trust seeding; spec 2026-07-29)
# --------------------------------------------------------------------------

class Upload(Base):
    """One uploaded PDF of the user's own paper. A draft holds parsed references
    only; works/citations/graph rows appear at confirm, never before."""
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # uuid hex
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    filename: Mapped[Optional[str]] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256, dedupes re-uploads
    # The uploaded paper's own title: parsed from the PDF, editable in review.
    title: Mapped[Optional[str]] = mapped_column(Text)
    # Draft-time resolution of the paper itself (works row created only at confirm).
    resolved_openalex_id: Mapped[Optional[str]] = mapped_column(String(24))
    resolved_work_id: Mapped[Optional[str]] = mapped_column(String(24))
    work_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("works.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(
        String(16), default="draft")  # draft | applying | engine_pending | confirmed
    n_parsed: Mapped[int] = mapped_column(Integer, default=0)
    n_matched: Mapped[int] = mapped_column(Integer, default=0)
    n_added: Mapped[int] = mapped_column(Integer, default=0)
    n_unresolved: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("profile_id", "content_hash", name="uq_upload_dedupe"),
    )


class UploadReference(Base):
    """One parsed bibliography entry of an upload, with its match state.

    `resolved_openalex_id` records an OpenAlex resolution WITHOUT creating a works
    row (spec B6: nothing lands in the corpus before confirm). `work_id` points at
    an EXISTING corpus work when matching found one.
    """
    __tablename__ = "upload_references"
    upload_id: Mapped[str] = mapped_column(
        ForeignKey("uploads.id", ondelete="CASCADE"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw: Mapped[str] = mapped_column(Text)
    parsed_title: Mapped[Optional[str]] = mapped_column(Text)
    parsed_doi: Mapped[Optional[str]] = mapped_column(Text)
    parsed_year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    resolved_openalex_id: Mapped[Optional[str]] = mapped_column(String(24))
    work_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("works.id", ondelete="SET NULL"))
    match_method: Mapped[str] = mapped_column(
        String(16), default="none")  # doi | arxiv | trigram | openalex | manual | none
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    decision: Mapped[str] = mapped_column(
        String(16), default="pending")  # pending | accept | reject
    # Per-entry trust strength used at confirm; default 3/5 (spec B1), promotable.
    strength: Mapped[int] = mapped_column(SmallInteger, default=3)
    # Labelled, included, tickable like anything else (spec: self-citations).
    is_self_citation: Mapped[bool] = mapped_column(Boolean, default=False)
    # OpenAlex was unreachable when this entry needed it: "couldn't check" (our
    # failure), never displayed as "not found" (a claim about the paper).
    couldnt_check: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when the confirm INSERTED the citations row (vs it already existing:
    # a corpus paper uploaded by its author may already carry the citation, and
    # undo must not delete corpus data it did not create).
    created_citation: Mapped[bool] = mapped_column(Boolean, default=False)


class TrustSource(Base):
    """Which upload(s) put a work into a profile's trust set. The trust row
    itself survives until its last source row is gone AND it was not hand-added
    (survivorship handled in the undo path, Phase 3b)."""
    __tablename__ = "trust_sources"
    profile_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    work_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    upload_id: Mapped[str] = mapped_column(
        ForeignKey("uploads.id", ondelete="CASCADE"), primary_key=True, index=True)
    # True when THIS upload's confirm created the trust row. Undo deletes the
    # trust row only when no source rows survive AND one of the removed sources
    # created it -- a hand-added row survives its uploads (spec survivorship).
    created_trust: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "work_id"],
            ["trust.profile_id", "trust.work_id"],
            ondelete="CASCADE",
        ),
    )


# --------------------------------------------------------------------------
# Engine node-name mapping (single source of truth)
# --------------------------------------------------------------------------
# The engine derives node kind from the FIRST CHARACTER of the name, and rejects
# NonUser->NonUser edges outright. Papers must therefore be `U`. See DECISIONS.md D1.6.

def work_node(work_id: str) -> str:
    return f"U{work_id}"


def profile_node(profile_id: str) -> str:
    return f"Uprofile_{profile_id}"


def author_node(author_id: str) -> str:
    return f"BA{author_id[1:]}" if author_id.startswith("A") else f"BA{author_id}"


def institution_node(inst_id: str) -> str:
    return f"BI{inst_id[1:]}" if inst_id.startswith("I") else f"BI{inst_id}"


def topic_node(topic_id: str) -> str:
    return f"BT{topic_id[1:]}" if topic_id.startswith("T") else f"BT{topic_id}"


def venue_node(venue_id: str) -> str:
    return f"BS{venue_id[1:]}" if venue_id.startswith("S") else f"BS{venue_id}"


_WORK_NODE_RE = re.compile(r"^U[WL]\d+$")


def node_to_work_id(node: str) -> str | None:
    """Inverse of work_node(); None if the node is not a paper.

    Must be strict. Every ego is a `U` node too -- profiles (`Uprofile_*`) but also
    the scratch egos used for leave-one-out, simulation and the global-merit
    reference. A loose prefix test lets those leak into rankings as phantom papers
    (the ego scores itself highest, so it lands at rank 1 with no title).

    `UL\\d+` is a user-uploaded local work (work_local_id_seq): a real paper node,
    subject to the per-profile visibility filter downstream.
    """
    return node[1:] if _WORK_NODE_RE.match(node) else None
