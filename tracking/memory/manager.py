"""
UserMemoryManager — Memory V2 facade.

Preserves the V1 API surface (signatures unchanged) and adds:
  - search_memories(user_id, query, k=10) -> List[Dict]
  - get_facts(user_id, category=None, k=10) -> List[Dict]
  - derive_profile(user_id) -> Dict

Behavior:
  - On every save_memory: row → user_memories, FTS5 index entry, embedding
    written into the embedding column. Async fact extraction is fired only
    when MEMORY_V2 is enabled for the user AND an anthropic client was given.
  - build_llm_context: when V2 is enabled for the user, returns the new
    facts + hybrid retrieval output. Otherwise defers to the V1 logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from tracking.memory import compress as compress_mod
from tracking.memory import extract as extract_mod
from tracking.memory import facts as facts_mod
from tracking.memory import retrieve as retrieve_mod
from tracking.memory import schema as schema_mod
from tracking.memory.embed import (
    cosine,
    from_blob,
    get_default_provider,
    to_blob,
)
from tracking.memory.feature_flags import _v2_enabled_for

logger = logging.getLogger(__name__)


class UserMemoryManager:
    """Memory V2-aware user memory manager (V1-compatible)."""

    # Memory types — preserved verbatim from V1.
    MEMORY_JOURNAL = "journal"
    MEMORY_EVALUATION = "evaluation"
    MEMORY_REPORT = "report"
    MEMORY_CONVERSATION = "conversation"

    # Compression layers — preserved.
    LAYER_DETAILED = 1
    LAYER_SUMMARY = 2
    LAYER_COMPRESSED = 3

    MAX_CONTEXT_TOKENS = 2000

    def __init__(self, db_path: str, anthropic_client: Any = None):
        self.db_path = db_path
        self.anthropic_client = anthropic_client
        # In-memory DBs do not persist state across connections, so we hold one
        # shared connection for the lifetime of the manager.
        self._shared_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._shared_conn = sqlite3.connect(self.db_path)
        self._ensure_tables()
        # anthropic_client stays as-is (possibly None). Lazy resolution happens
        # on first call to _ensure_anthropic_client() inside _maybe_spawn_extraction.

    @staticmethod
    def _maybe_default_anthropic_client() -> Any:
        import os
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        try:
            import anthropic  # type: ignore
        except ImportError:
            return None
        try:
            return anthropic.AsyncAnthropic()
        except Exception:  # pragma: no cover — never break boot on client init
            logger.warning("memory.anthropic.lazy_init_failed", exc_info=True)
            return None

    # =========================================================================
    # Init / connection
    # =========================================================================
    def _get_connection(self) -> sqlite3.Connection:
        if self._shared_conn is not None:
            return self._shared_conn
        return sqlite3.connect(self.db_path)

    def _close_if_owned(self, conn: sqlite3.Connection) -> None:
        """Close a connection unless it is the shared one (for :memory: DBs)."""
        if self._shared_conn is not None and conn is self._shared_conn:
            return
        try:
            conn.close()
        except Exception:
            pass

    def _ensure_tables(self) -> None:
        try:
            conn = self._get_connection()
            try:
                schema_mod.run_migrations(conn)
            finally:
                self._close_if_owned(conn)
        except Exception as e:
            logger.error("memory.manager.init_failed err=%s", e)

    # =========================================================================
    # Internal helpers
    # =========================================================================
    @staticmethod
    def _content_text(content: Dict[str, Any]) -> str:
        for key in ("text", "raw_input", "response_summary", "summary"):
            v = (content or {}).get(key)
            if isinstance(v, str) and v:
                return v
        return ""

    def _embed_text_safely(self, text: str):
        try:
            provider = get_default_provider()
            return provider.embed(text or ""), provider.name
        except Exception as e:
            logger.warning("memory.manager.embed_failed err=%s", e)
            return None, None

    def _ensure_anthropic_client(self) -> Any:
        """Lazy-init the Anthropic client on first use (Task 6)."""
        if self.anthropic_client is None:
            self.anthropic_client = self._maybe_default_anthropic_client()
        return self.anthropic_client

    def _maybe_spawn_extraction(self, memory_id: int, user_id: int) -> None:
        """Fire-and-forget fact extraction. Silent no-op when disabled / no loop."""
        if not _v2_enabled_for(user_id):
            return
        client = self._ensure_anthropic_client()
        if client is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop — silently skip extraction
        try:
            asyncio.create_task(
                extract_mod.run_extraction_after_save(
                    memory_id=memory_id,
                    db_path=self.db_path,
                    anthropic_client=client,
                    user_id=user_id,
                )
            )
        except Exception as e:
            logger.warning("memory.manager.spawn_extraction_failed err=%s", e)

    # =========================================================================
    # Core: save_memory
    # =========================================================================
    def save_memory(
        self,
        user_id: int,
        memory_type: str,
        content: Dict[str, Any],
        ticker: Optional[str] = None,
        ticker_name: Optional[str] = None,
        market_type: str = "kr",
        importance_score: float = 0.5,
        command_source: Optional[str] = None,
        message_id: Optional[int] = None,
        tags: Optional[List[str]] = None,
    ) -> int:
        conn = self._get_connection()
        try:
            now = datetime.now().isoformat()
            content_json = json.dumps(content, ensure_ascii=False)
            tags_json = json.dumps(tags, ensure_ascii=False) if tags else None

            # Embed the content text — best-effort.
            text = self._content_text(content)
            emb, emb_model = self._embed_text_safely(text)
            emb_blob = to_blob(emb) if emb is not None else None

            cur = conn.execute(
                """
                INSERT INTO user_memories (
                    user_id, memory_type, content, ticker, ticker_name,
                    market_type, importance_score, compression_layer,
                    created_at, last_accessed_at, command_source, message_id, tags,
                    embedding, embedding_model, fact_extracted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    user_id, memory_type, content_json, ticker, ticker_name,
                    market_type, importance_score, self.LAYER_DETAILED,
                    now, now, command_source, message_id, tags_json,
                    emb_blob, emb_model,
                ),
            )
            memory_id = int(cur.lastrowid or 0)
            conn.commit()

            # FTS5 index entry.
            try:
                schema_mod.fts_insert(
                    conn,
                    memory_id=memory_id,
                    content_text=text,
                    ticker=ticker or "",
                    ticker_name=ticker_name or "",
                )
            except Exception as e:
                logger.warning("memory.manager.fts_insert_failed err=%s", e)

            # Invalidate in-memory vector cache so next search picks up the new row.
            retrieve_mod.reset_vector_cache()

            # Update user statistics row.
            self._update_user_stats(conn, user_id, memory_type)

            logger.info(
                "memory.saved user=%d type=%s ticker=%s id=%d",
                user_id, memory_type, ticker, memory_id,
            )
        except Exception as e:
            logger.error("memory.save_failed err=%s", e)
            conn.rollback()
            self._close_if_owned(conn)
            raise
        else:
            self._close_if_owned(conn)

        # Async fact extraction (V2 only).
        self._maybe_spawn_extraction(memory_id, user_id)
        return memory_id

    # =========================================================================
    # Core: get_memories
    # =========================================================================
    def get_memories(
        self,
        user_id: int,
        memory_type: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 10,
        include_compressed: bool = True,
    ) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            sql = """
                SELECT id, user_id, memory_type, content, summary, ticker, ticker_name,
                       market_type, importance_score, compression_layer, created_at,
                       last_accessed_at, command_source, message_id, tags
                FROM user_memories
                WHERE user_id = ?
            """
            params: List[Any] = [user_id]
            if memory_type:
                sql += " AND memory_type = ?"
                params.append(memory_type)
            if ticker:
                sql += " AND ticker = ?"
                params.append(ticker)
            if not include_compressed:
                sql += " AND compression_layer < ?"
                params.append(self.LAYER_COMPRESSED)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(sql, params)
            rows = cur.fetchall()

            memories: List[Dict[str, Any]] = []
            for row in rows:
                memories.append({
                    "id": row[0],
                    "user_id": row[1],
                    "memory_type": row[2],
                    "content": json.loads(row[3]) if row[3] else {},
                    "summary": row[4],
                    "ticker": row[5],
                    "ticker_name": row[6],
                    "market_type": row[7],
                    "importance_score": row[8],
                    "compression_layer": row[9],
                    "created_at": row[10],
                    "last_accessed_at": row[11],
                    "command_source": row[12],
                    "message_id": row[13],
                    "tags": json.loads(row[14]) if row[14] else [],
                })

            if memories:
                self._update_access_time(conn, [m["id"] for m in memories])
            return memories
        except Exception as e:
            logger.error("memory.get_failed err=%s", e)
            return []
        finally:
            self._close_if_owned(conn)

    # =========================================================================
    # Journal helpers (V1)
    # =========================================================================
    def save_journal(
        self,
        user_id: int,
        text: str,
        ticker: Optional[str] = None,
        ticker_name: Optional[str] = None,
        market_type: str = "kr",
        message_id: Optional[int] = None,
    ) -> int:
        content = {
            "text": text,
            "raw_input": text,
            "recorded_at": datetime.now().isoformat(),
        }
        return self.save_memory(
            user_id=user_id,
            memory_type=self.MEMORY_JOURNAL,
            content=content,
            ticker=ticker,
            ticker_name=ticker_name,
            market_type=market_type,
            importance_score=0.7,
            command_source="/journal",
            message_id=message_id,
        )

    def get_journals(
        self,
        user_id: int,
        ticker: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        return self.get_memories(
            user_id=user_id,
            memory_type=self.MEMORY_JOURNAL,
            ticker=ticker,
            limit=limit,
        )

    # =========================================================================
    # New V2 methods
    # =========================================================================
    def search_memories(
        self,
        user_id: int,
        query: str,
        k: int = 10,
        ticker: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid BM25+vector+recent retrieval merged with RRF."""
        conn = self._get_connection()
        try:
            qv = None
            try:
                qv, _ = self._embed_text_safely(query or "")
            except Exception:
                qv = None
            return retrieve_mod.hybrid_search(
                conn,
                user_id=user_id,
                query=query,
                query_vec=qv,
                ticker=ticker,
                k=k,
            )
        except Exception as e:
            logger.warning("memory.search_failed err=%s", e)
            return []
        finally:
            self._close_if_owned(conn)

    def get_facts(
        self,
        user_id: int,
        category: Optional[str] = None,
        k: int = 10,
    ) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            return facts_mod.get_facts(
                conn, user_id=user_id, category=category, active=1, limit=k
            )
        finally:
            self._close_if_owned(conn)

    def derive_profile(self, user_id: int) -> Dict[str, Any]:
        """Aggregate active facts into a profile and persist to user_preferences."""
        conn = self._get_connection()
        profile: Dict[str, Any] = {
            "investment_style": None,
            "risk_tolerance": None,
            "favorite_tickers": [],
            "aversions": [],
            "goals": [],
        }
        try:
            all_facts = facts_mod.get_facts(conn, user_id=user_id, active=1, limit=200)
            by_cat: Dict[str, List[Dict[str, Any]]] = {}
            for f in all_facts:
                by_cat.setdefault(f["category"], []).append(f)

            def _join(items: List[Dict[str, Any]], n: int = 3) -> Optional[str]:
                if not items:
                    return None
                items = sorted(items, key=lambda x: x.get("confidence", 0), reverse=True)
                return "; ".join(x["fact"] for x in items[:n])

            profile["investment_style"] = _join(by_cat.get("style", []))
            profile["risk_tolerance"] = _join(by_cat.get("risk", []))
            profile["aversions"] = [x["fact"] for x in by_cat.get("aversion", [])][:5]
            profile["goals"] = [x["fact"] for x in by_cat.get("goal", [])][:5]

            # Top mentioned tickers from recent journals.
            cur = conn.execute(
                """
                SELECT ticker, COUNT(*) AS c
                FROM user_memories
                WHERE user_id = ? AND ticker IS NOT NULL
                GROUP BY ticker
                ORDER BY c DESC
                LIMIT 5
                """,
                (user_id,),
            )
            profile["favorite_tickers"] = [r[0] for r in cur.fetchall()]
        finally:
            self._close_if_owned(conn)

        # Persist to user_preferences (best-effort).
        try:
            self.update_user_preferences(
                user_id,
                investment_style=profile["investment_style"],
                favorite_tickers=profile["favorite_tickers"] or None,
            )
        except Exception as e:
            logger.warning("memory.derive_profile.persist_failed err=%s", e)
        return profile

    # =========================================================================
    # build_llm_context — V2 if flag, else V1 verbatim.
    # =========================================================================
    def build_llm_context(
        self,
        user_id: int,
        ticker: Optional[str] = None,
        max_tokens: int = 4000,
        user_message: Optional[str] = None,
    ) -> str:
        try:
            if _v2_enabled_for(user_id):
                return self._v2_build_llm_context(user_id, ticker, max_tokens, user_message)
            return self._v1_build_llm_context(user_id, ticker, max_tokens, user_message)
        except Exception as e:
            logger.warning("memory.build_context.failed err=%s", e)
            return ""

    def _v2_build_llm_context(
        self,
        user_id: int,
        ticker: Optional[str],
        max_tokens: int,
        user_message: Optional[str],
    ) -> str:
        def estimate_tokens(t: str) -> int:
            return len(t) // 2

        parts: List[str] = []
        tokens = 0

        # Top facts (≤ 5).
        conn = self._get_connection()
        try:
            top_facts = facts_mod.get_top_by_category(conn, user_id, k=5, min_confidence=0.6)
        finally:
            self._close_if_owned(conn)
        if top_facts:
            fact_lines = [f"- {f['fact']} ({f['category']})" for f in top_facts]
            block = "🧠 사용자 특성:\n" + "\n".join(fact_lines)
            block_tokens = estimate_tokens(block)
            if block_tokens < max_tokens:
                parts.append(block)
                tokens += block_tokens

        # Hybrid retrieval — pick query.
        query = user_message or ticker or ""
        related = self.search_memories(user_id, query=query, k=8, ticker=ticker)
        if related:
            lines = []
            for m in related:
                created = (m.get("created_at") or "")[:10]
                content = m.get("content") or {}
                text = (content.get("text") or content.get("response_summary") or "")[:400]
                t = m.get("ticker") or ""
                tn = m.get("ticker_name") or ""
                if tn and t:
                    lines.append(f"- [{created}] {tn}({t}): {text}")
                elif t:
                    lines.append(f"- [{created}] ({t}): {text}")
                else:
                    lines.append(f"- [{created}] {text}")
            block = "📝 관련 기억:\n" + "\n".join(lines)
            block_tokens = estimate_tokens(block)
            if tokens + block_tokens < max_tokens:
                parts.append(block)
                tokens += block_tokens

        return "\n\n".join(parts)

    # =========================================================================
    # V1 build_llm_context — preserved verbatim.
    # =========================================================================
    def _v1_build_llm_context(
        self,
        user_id: int,
        ticker: Optional[str] = None,
        max_tokens: int = 4000,
        user_message: Optional[str] = None,
    ) -> str:
        parts = []
        tokens = 0
        loaded_tickers = set()

        def estimate_tokens(text: str) -> int:
            return len(text) // 2

        if ticker:
            journals = self.get_journals(user_id, ticker=ticker, limit=10)
            if journals:
                journal_text = self._format_journals(journals)
                journal_tokens = estimate_tokens(journal_text)
                if journal_tokens < 1200:
                    parts.append(f"📝 {ticker} Related Records:\n{journal_text}")
                    tokens += journal_tokens
                    loaded_tickers.add(ticker)

        if ticker and tokens < max_tokens - 800:
            evals = self.get_memories(user_id, self.MEMORY_EVALUATION, ticker=ticker, limit=5)
            if evals:
                eval_text = self._format_evaluations(evals)
                eval_tokens = estimate_tokens(eval_text)
                if tokens + eval_tokens < max_tokens:
                    parts.append(f"📊 Past Evaluations:\n{eval_text}")
                    tokens += eval_tokens

        if user_message and tokens < max_tokens - 1000:
            mentioned = self._extract_tickers_from_text(user_message, user_id)
            for mt in mentioned[:3]:
                if mt in loaded_tickers:
                    continue
                if tokens >= max_tokens - 500:
                    break
                tj = self.get_journals(user_id, ticker=mt, limit=5)
                if tj:
                    txt = self._format_journals(tj)
                    tk = estimate_tokens(txt)
                    if tokens + tk < max_tokens:
                        parts.append(f"📝 {mt} Related Records:\n{txt}")
                        tokens += tk
                        loaded_tickers.add(mt)

        if not ticker and tokens < max_tokens - 1000:
            recent = self.get_journals(user_id, limit=20)
            counts: Dict[str, int] = {}
            for j in recent:
                t = j.get("ticker")
                if t and t not in loaded_tickers:
                    counts[t] = counts.get(t, 0) + 1
            for mt, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]:
                if tokens >= max_tokens - 500:
                    break
                tj = self.get_journals(user_id, ticker=mt, limit=5)
                if tj:
                    txt = self._format_journals(tj)
                    tk = estimate_tokens(txt)
                    if tokens + tk < max_tokens:
                        parts.append(f"📝 {mt} Related Records ({count} mentions):\n{txt}")
                        tokens += tk
                        loaded_tickers.add(mt)

        if tokens < max_tokens - 500:
            recent = self.get_journals(user_id, limit=10)
            recent = [j for j in recent if j.get("ticker") not in loaded_tickers]
            if recent:
                rt = self._format_journals(recent[:10])
                if tokens + estimate_tokens(rt) < max_tokens:
                    parts.append(f"💭 Recent Thoughts:\n{rt}")

        return "\n\n".join(parts) if parts else ""

    # =========================================================================
    # _extract_tickers_from_text — preserved (still used in V1 path / tests)
    # =========================================================================
    def _extract_tickers_from_text(self, text: str, user_id: int) -> List[str]:
        tickers: List[str] = []
        kr_pattern = r"\b(\d{6})\b"
        tickers.extend(re.findall(kr_pattern, text))

        us_pattern = r"\b([A-Z]{1,5})\b"
        excluded = {
            "I", "A", "AN", "THE", "IN", "ON", "AT", "TO", "FOR", "OF",
            "AND", "OR", "IS", "IT", "AI", "AM", "PM", "VS", "OK", "NO",
            "PER", "PBR", "ROE", "ROA", "EPS", "BPS", "PSR", "PCR",
            "HBM", "DRAM", "NAND", "SSD", "GPU", "CPU", "AP", "PC",
        }
        for t in re.findall(us_pattern, text):
            if t not in excluded and t not in tickers:
                tickers.append(t)

        try:
            past = self.get_journals(user_id, limit=50)
            known = {}
            for j in past:
                tk, nm = j.get("ticker"), j.get("ticker_name")
                if tk and nm:
                    known[nm] = tk
            for nm, tk in known.items():
                if nm and nm in text and tk not in tickers:
                    tickers.append(tk)
        except Exception:
            pass
        return tickers

    # =========================================================================
    # Compression
    # =========================================================================
    def compress_old_memories(
        self,
        layer1_days: int = 7,
        layer2_days: int = 30,
    ) -> Dict[str, int]:
        """V1-compatible signature; uses Haiku-powered backend with truncate fallback."""
        conn = self._get_connection()
        try:
            return compress_mod.compress_all_sync(
                conn,
                anthropic_client=self.anthropic_client,
                layer1_days=layer1_days,
                layer2_days=layer2_days,
            )
        finally:
            self._close_if_owned(conn)

    # =========================================================================
    # User preferences (V1 verbatim)
    # =========================================================================
    def get_user_preferences(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT user_id, preferred_tone, investment_style, favorite_tickers,
                       total_evaluations, total_journals, created_at, last_active_at
                FROM user_preferences
                WHERE user_id = ?
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "preferred_tone": row[1],
                    "investment_style": row[2],
                    "favorite_tickers": json.loads(row[3]) if row[3] else [],
                    "total_evaluations": row[4],
                    "total_journals": row[5],
                    "created_at": row[6],
                    "last_active_at": row[7],
                }
            return None
        except Exception as e:
            logger.error("memory.get_prefs_failed err=%s", e)
            return None
        finally:
            self._close_if_owned(conn)

    def update_user_preferences(
        self,
        user_id: int,
        preferred_tone: Optional[str] = None,
        investment_style: Optional[str] = None,
        favorite_tickers: Optional[List[str]] = None,
    ) -> None:
        conn = self._get_connection()
        try:
            now = datetime.now().isoformat()
            cur = conn.execute(
                "SELECT user_id FROM user_preferences WHERE user_id = ?", (user_id,)
            )
            exists = cur.fetchone() is not None

            if exists:
                updates: List[str] = []
                params: List[Any] = []
                if preferred_tone is not None:
                    updates.append("preferred_tone = ?")
                    params.append(preferred_tone)
                if investment_style is not None:
                    updates.append("investment_style = ?")
                    params.append(investment_style)
                if favorite_tickers is not None:
                    updates.append("favorite_tickers = ?")
                    params.append(json.dumps(favorite_tickers, ensure_ascii=False))
                updates.append("last_active_at = ?")
                params.append(now)
                params.append(user_id)
                if updates:
                    conn.execute(
                        f"UPDATE user_preferences SET {', '.join(updates)} WHERE user_id = ?",
                        params,
                    )
            else:
                fav_json = json.dumps(favorite_tickers, ensure_ascii=False) if favorite_tickers else None
                conn.execute(
                    """
                    INSERT INTO user_preferences (
                        user_id, preferred_tone, investment_style, favorite_tickers,
                        total_evaluations, total_journals, created_at, last_active_at
                    ) VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (user_id, preferred_tone, investment_style, fav_json, now, now),
                )
            conn.commit()
        except Exception as e:
            logger.error("memory.update_prefs_failed err=%s", e)
        finally:
            self._close_if_owned(conn)

    # =========================================================================
    # Stats / delete (V1)
    # =========================================================================
    def get_memory_stats(self, user_id: int) -> Dict[str, Any]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT memory_type, COUNT(*) FROM user_memories WHERE user_id = ? GROUP BY memory_type",
                (user_id,),
            )
            type_counts = {row[0]: row[1] for row in cur.fetchall()}
            cur = conn.execute(
                "SELECT compression_layer, COUNT(*) FROM user_memories WHERE user_id = ? GROUP BY compression_layer",
                (user_id,),
            )
            layer_counts = {f"layer_{row[0]}": row[1] for row in cur.fetchall()}
            cur = conn.execute(
                """
                SELECT ticker, COUNT(*) FROM user_memories
                WHERE user_id = ? AND ticker IS NOT NULL
                GROUP BY ticker ORDER BY COUNT(*) DESC LIMIT 10
                """,
                (user_id,),
            )
            ticker_counts = {row[0]: row[1] for row in cur.fetchall()}
            return {
                "by_type": type_counts,
                "by_layer": layer_counts,
                "by_ticker": ticker_counts,
                "total": sum(type_counts.values()),
            }
        except Exception as e:
            logger.error("memory.stats_failed err=%s", e)
            return {}
        finally:
            self._close_if_owned(conn)

    def delete_memory(self, memory_id: int, user_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM user_memories WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.error("memory.delete_failed err=%s", e)
            return False
        finally:
            self._close_if_owned(conn)

    # =========================================================================
    # Private helpers
    # =========================================================================
    def _update_user_stats(
        self, conn: sqlite3.Connection, user_id: int, memory_type: str
    ) -> None:
        try:
            now = datetime.now().isoformat()
            cur = conn.execute(
                "SELECT user_id FROM user_preferences WHERE user_id = ?", (user_id,)
            )
            exists = cur.fetchone() is not None
            if exists:
                if memory_type == self.MEMORY_JOURNAL:
                    conn.execute(
                        "UPDATE user_preferences SET total_journals = total_journals + 1, last_active_at = ? WHERE user_id = ?",
                        (now, user_id),
                    )
                elif memory_type == self.MEMORY_EVALUATION:
                    conn.execute(
                        "UPDATE user_preferences SET total_evaluations = total_evaluations + 1, last_active_at = ? WHERE user_id = ?",
                        (now, user_id),
                    )
                else:
                    conn.execute(
                        "UPDATE user_preferences SET last_active_at = ? WHERE user_id = ?",
                        (now, user_id),
                    )
            else:
                journals = 1 if memory_type == self.MEMORY_JOURNAL else 0
                evals = 1 if memory_type == self.MEMORY_EVALUATION else 0
                conn.execute(
                    """
                    INSERT INTO user_preferences (
                        user_id, total_evaluations, total_journals, created_at, last_active_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, evals, journals, now, now),
                )
            conn.commit()
        except Exception as e:
            logger.warning("memory._update_user_stats.failed err=%s", e)

    def _update_access_time(
        self, conn: sqlite3.Connection, memory_ids: List[int]
    ) -> None:
        if not memory_ids:
            return
        try:
            now = datetime.now().isoformat()
            placeholders = ",".join(["?"] * len(memory_ids))
            conn.execute(
                f"UPDATE user_memories SET last_accessed_at = ? WHERE id IN ({placeholders})",
                [now] + list(memory_ids),
            )
            conn.commit()
        except Exception as e:
            logger.warning("memory._update_access_time.failed err=%s", e)

    @staticmethod
    def _format_journals(journals: List[Dict[str, Any]]) -> str:
        lines = []
        for j in journals:
            created = (j.get("created_at") or "")[:10]
            content = j.get("content") or {}
            text = (content.get("text") or "")[:500]
            ticker = j.get("ticker") or ""
            ticker_name = j.get("ticker_name") or ""
            if ticker and ticker_name:
                lines.append(f"- [{created}] {ticker_name}({ticker}): {text}")
            elif ticker:
                lines.append(f"- [{created}] ({ticker}): {text}")
            else:
                lines.append(f"- [{created}] {text}")
        return "\n".join(lines)

    @staticmethod
    def _format_evaluations(evals: List[Dict[str, Any]]) -> str:
        lines = []
        for e in evals:
            created = (e.get("created_at") or "")[:10]
            content = e.get("content") or {}
            summary = e.get("summary")
            if not summary:
                response = content.get("response_summary", "")
                summary = response[:300] + "..." if len(response) > 300 else response
            ticker = e.get("ticker") or ""
            ticker_name = e.get("ticker_name") or ""
            if ticker_name:
                lines.append(f"- [{created}] {ticker_name}({ticker}): {summary}")
            else:
                lines.append(f"- [{created}] {ticker}: {summary}")
        return "\n".join(lines)
