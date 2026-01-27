"""
User Memory Manager for Telegram Bot

사용자별 매매일지와 대화 기록을 저장하는 지속적 기억 시스템.

Features:
- /journal 명령어로 매매일지 기록
- 단기기억 (1주일) / 장기기억 (그 이상) 분리
- /evaluate, /report 명령어에서도 기억 활용
- 답장으로 대화 이어가기 지원
- 사용자별 격리 (user_id 기반)
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class UserMemoryManager:
    """사용자별 기억 관리자"""

    # 기억 타입
    MEMORY_JOURNAL = 'journal'
    MEMORY_EVALUATION = 'evaluation'
    MEMORY_REPORT = 'report'
    MEMORY_CONVERSATION = 'conversation'

    # 압축 레이어 (기존 패턴 동일)
    LAYER_DETAILED = 1   # 0-7일: 전체 내용
    LAYER_SUMMARY = 2    # 8-30일: 요약
    LAYER_COMPRESSED = 3  # 31일+: 압축

    # 토큰 예산
    MAX_CONTEXT_TOKENS = 2000

    def __init__(self, db_path: str):
        """
        UserMemoryManager 초기화

        Args:
            db_path: SQLite 데이터베이스 경로
        """
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self):
        """테이블 존재 확인 및 생성"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # user_memories 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    ticker TEXT,
                    ticker_name TEXT,
                    market_type TEXT DEFAULT 'kr',
                    importance_score REAL DEFAULT 0.5,
                    compression_layer INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    command_source TEXT,
                    message_id INTEGER,
                    tags TEXT
                )
            """)

            # user_preferences 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    preferred_tone TEXT DEFAULT 'neutral',
                    investment_style TEXT,
                    favorite_tickers TEXT,
                    total_evaluations INTEGER DEFAULT 0,
                    total_journals INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_active_at TEXT
                )
            """)

            # 인덱스 생성
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_memories_type ON user_memories(user_id, memory_type)",
                "CREATE INDEX IF NOT EXISTS idx_memories_ticker ON user_memories(user_id, ticker)",
                "CREATE INDEX IF NOT EXISTS idx_memories_created ON user_memories(user_id, created_at DESC)",
            ]
            for idx_sql in indexes:
                cursor.execute(idx_sql)

            conn.commit()
            conn.close()
            logger.info("User memory tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize user memory tables: {e}")

    def _get_connection(self):
        """데이터베이스 연결 반환"""
        return sqlite3.connect(self.db_path)

    # =========================================================================
    # 핵심 메서드
    # =========================================================================

    def save_memory(
        self,
        user_id: int,
        memory_type: str,
        content: Dict[str, Any],
        ticker: Optional[str] = None,
        ticker_name: Optional[str] = None,
        market_type: str = 'kr',
        importance_score: float = 0.5,
        command_source: Optional[str] = None,
        message_id: Optional[int] = None,
        tags: Optional[List[str]] = None
    ) -> int:
        """
        기억 저장

        Args:
            user_id: 사용자 ID
            memory_type: 기억 타입 (journal, evaluation, report, conversation)
            content: 저장할 내용 (dict -> JSON)
            ticker: 종목 코드/티커
            ticker_name: 종목명
            market_type: 시장 타입 (kr, us)
            importance_score: 중요도 점수 (0.0 ~ 1.0)
            command_source: 명령어 출처
            message_id: 텔레그램 메시지 ID
            tags: 태그 리스트

        Returns:
            int: 생성된 기억 ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()
            content_json = json.dumps(content, ensure_ascii=False)
            tags_json = json.dumps(tags, ensure_ascii=False) if tags else None

            cursor.execute("""
                INSERT INTO user_memories (
                    user_id, memory_type, content, ticker, ticker_name,
                    market_type, importance_score, compression_layer,
                    created_at, last_accessed_at, command_source, message_id, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, memory_type, content_json, ticker, ticker_name,
                market_type, importance_score, self.LAYER_DETAILED,
                now, now, command_source, message_id, tags_json
            ))

            memory_id = cursor.lastrowid or 0
            conn.commit()

            # 사용자 통계 업데이트
            self._update_user_stats(user_id, memory_type)

            logger.info(f"Memory saved: user={user_id}, type={memory_type}, ticker={ticker}, id={memory_id}")
            return memory_id

        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_memories(
        self,
        user_id: int,
        memory_type: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 10,
        include_compressed: bool = True
    ) -> List[Dict[str, Any]]:
        """
        기억 조회

        Args:
            user_id: 사용자 ID
            memory_type: 기억 타입 (None이면 전체)
            ticker: 종목 코드/티커 (None이면 전체)
            limit: 최대 조회 개수
            include_compressed: 압축된 기억 포함 여부

        Returns:
            List[Dict]: 기억 목록
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            query = """
                SELECT id, user_id, memory_type, content, summary, ticker, ticker_name,
                       market_type, importance_score, compression_layer, created_at,
                       last_accessed_at, command_source, message_id, tags
                FROM user_memories
                WHERE user_id = ?
            """
            params: List[Any] = [user_id]

            if memory_type:
                query += " AND memory_type = ?"
                params.append(memory_type)

            if ticker:
                query += " AND ticker = ?"
                params.append(ticker)

            if not include_compressed:
                query += " AND compression_layer < ?"
                params.append(self.LAYER_COMPRESSED)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            memories = []
            for row in rows:
                memory = {
                    'id': row[0],
                    'user_id': row[1],
                    'memory_type': row[2],
                    'content': json.loads(row[3]) if row[3] else {},
                    'summary': row[4],
                    'ticker': row[5],
                    'ticker_name': row[6],
                    'market_type': row[7],
                    'importance_score': row[8],
                    'compression_layer': row[9],
                    'created_at': row[10],
                    'last_accessed_at': row[11],
                    'command_source': row[12],
                    'message_id': row[13],
                    'tags': json.loads(row[14]) if row[14] else []
                }
                memories.append(memory)

            # 접근 시간 업데이트
            if memories:
                memory_ids = [m['id'] for m in memories]
                self._update_access_time(memory_ids)

            return memories

        except Exception as e:
            logger.error(f"Failed to get memories: {e}")
            return []
        finally:
            conn.close()

    def build_llm_context(
        self,
        user_id: int,
        ticker: Optional[str] = None,
        max_tokens: int = 2000
    ) -> str:
        """
        LLM에 전달할 기억 컨텍스트 빌드

        Args:
            user_id: 사용자 ID
            ticker: 종목 코드/티커 (특정 종목에 대한 기억 우선)
            max_tokens: 최대 토큰 수

        Returns:
            str: 포맷팅된 기억 컨텍스트
        """
        parts = []
        tokens = 0

        # 토큰 추정 함수 (한글 기준 대략적 추정)
        def estimate_tokens(text: str) -> int:
            return len(text) // 2  # 한글은 대략 2글자당 1토큰

        # 우선순위 1: 해당 종목 저널 (최대 800 토큰)
        if ticker:
            journals = self.get_journals(user_id, ticker=ticker, limit=5)
            if journals:
                journal_text = self._format_journals(journals)
                journal_tokens = estimate_tokens(journal_text)
                if journal_tokens < 800:
                    parts.append(f"📝 {ticker} 관련 기록:\n{journal_text}")
                    tokens += journal_tokens

        # 우선순위 2: 해당 종목 과거 평가 (최대 500 토큰)
        if ticker and tokens < max_tokens - 500:
            evals = self.get_memories(user_id, self.MEMORY_EVALUATION, ticker=ticker, limit=3)
            if evals:
                eval_text = self._format_evaluations(evals)
                eval_tokens = estimate_tokens(eval_text)
                if tokens + eval_tokens < max_tokens:
                    parts.append(f"📊 과거 평가:\n{eval_text}")
                    tokens += eval_tokens

        # 우선순위 3: 최근 일반 저널 (남은 토큰)
        if tokens < max_tokens - 300:
            recent = self.get_journals(user_id, limit=3)
            # 이미 포함된 ticker 제외
            recent = [j for j in recent if j.get('ticker') != ticker]
            if recent:
                recent_text = self._format_journals(recent)
                recent_tokens = estimate_tokens(recent_text)
                if tokens + recent_tokens < max_tokens:
                    parts.append(f"💭 최근 생각:\n{recent_text}")

        return "\n\n".join(parts) if parts else ""

    # =========================================================================
    # 저널 전용 메서드
    # =========================================================================

    def save_journal(
        self,
        user_id: int,
        text: str,
        ticker: Optional[str] = None,
        ticker_name: Optional[str] = None,
        market_type: str = 'kr',
        message_id: Optional[int] = None
    ) -> int:
        """
        저널(투자 일기) 저장

        Args:
            user_id: 사용자 ID
            text: 저널 텍스트
            ticker: 종목 코드/티커
            ticker_name: 종목명
            market_type: 시장 타입
            message_id: 텔레그램 메시지 ID

        Returns:
            int: 생성된 기억 ID
        """
        content = {
            'text': text,
            'raw_input': text,
            'recorded_at': datetime.now().isoformat()
        }

        return self.save_memory(
            user_id=user_id,
            memory_type=self.MEMORY_JOURNAL,
            content=content,
            ticker=ticker,
            ticker_name=ticker_name,
            market_type=market_type,
            importance_score=0.7,  # 저널은 기본적으로 중요도 높음
            command_source='/journal',
            message_id=message_id
        )

    def get_journals(
        self,
        user_id: int,
        ticker: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        저널 조회

        Args:
            user_id: 사용자 ID
            ticker: 종목 코드/티커
            limit: 최대 조회 개수

        Returns:
            List[Dict]: 저널 목록
        """
        return self.get_memories(
            user_id=user_id,
            memory_type=self.MEMORY_JOURNAL,
            ticker=ticker,
            limit=limit
        )

    # =========================================================================
    # 압축 메서드
    # =========================================================================

    def compress_old_memories(
        self,
        layer1_days: int = 7,
        layer2_days: int = 30
    ) -> Dict[str, int]:
        """
        오래된 기억 압축 (야간 배치용)

        Args:
            layer1_days: Layer 1 -> Layer 2 전환 기준일 (기본 7일)
            layer2_days: Layer 2 -> Layer 3 전환 기준일 (기본 30일)

        Returns:
            Dict[str, int]: 압축 통계 {'layer2_count': n, 'layer3_count': n}
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        stats = {'layer2_count': 0, 'layer3_count': 0}

        try:
            now = datetime.now()
            layer2_cutoff = (now - timedelta(days=layer1_days)).isoformat()
            layer3_cutoff = (now - timedelta(days=layer2_days)).isoformat()

            # Layer 1 -> Layer 2 (7일 이상)
            cursor.execute("""
                SELECT id, content, ticker, ticker_name
                FROM user_memories
                WHERE compression_layer = 1
                AND created_at < ?
            """, (layer2_cutoff,))

            for row in cursor.fetchall():
                memory_id, content_json, ticker, ticker_name = row
                content = json.loads(content_json) if content_json else {}

                # 요약 생성
                summary = self._generate_summary(content, ticker, ticker_name)

                cursor.execute("""
                    UPDATE user_memories
                    SET compression_layer = 2, summary = ?
                    WHERE id = ?
                """, (summary, memory_id))
                stats['layer2_count'] += 1

            # Layer 2 -> Layer 3 (30일 이상)
            cursor.execute("""
                SELECT id, summary, ticker, ticker_name
                FROM user_memories
                WHERE compression_layer = 2
                AND created_at < ?
            """, (layer3_cutoff,))

            for row in cursor.fetchall():
                memory_id, summary, ticker, ticker_name = row

                # 한줄 압축 생성
                compressed = self._generate_compressed(summary, ticker, ticker_name)

                cursor.execute("""
                    UPDATE user_memories
                    SET compression_layer = 3, summary = ?
                    WHERE id = ?
                """, (compressed, memory_id))
                stats['layer3_count'] += 1

            conn.commit()
            logger.info(f"Memory compression completed: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Failed to compress memories: {e}")
            conn.rollback()
            return stats
        finally:
            conn.close()

    # =========================================================================
    # 사용자 선호 메서드
    # =========================================================================

    def get_user_preferences(self, user_id: int) -> Optional[Dict[str, Any]]:
        """사용자 선호 설정 조회"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT user_id, preferred_tone, investment_style, favorite_tickers,
                       total_evaluations, total_journals, created_at, last_active_at
                FROM user_preferences
                WHERE user_id = ?
            """, (user_id,))

            row = cursor.fetchone()
            if row:
                return {
                    'user_id': row[0],
                    'preferred_tone': row[1],
                    'investment_style': row[2],
                    'favorite_tickers': json.loads(row[3]) if row[3] else [],
                    'total_evaluations': row[4],
                    'total_journals': row[5],
                    'created_at': row[6],
                    'last_active_at': row[7]
                }
            return None
        except Exception as e:
            logger.error(f"Failed to get user preferences: {e}")
            return None
        finally:
            conn.close()

    def update_user_preferences(
        self,
        user_id: int,
        preferred_tone: Optional[str] = None,
        investment_style: Optional[str] = None,
        favorite_tickers: Optional[List[str]] = None
    ):
        """사용자 선호 설정 업데이트"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()

            # 기존 설정 확인
            cursor.execute("SELECT user_id FROM user_preferences WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone() is not None

            if exists:
                updates = []
                params = []

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
                    cursor.execute(f"""
                        UPDATE user_preferences
                        SET {', '.join(updates)}
                        WHERE user_id = ?
                    """, params)
            else:
                favorite_json = json.dumps(favorite_tickers, ensure_ascii=False) if favorite_tickers else None
                cursor.execute("""
                    INSERT INTO user_preferences (
                        user_id, preferred_tone, investment_style, favorite_tickers,
                        total_evaluations, total_journals, created_at, last_active_at
                    ) VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                """, (user_id, preferred_tone, investment_style, favorite_json, now, now))

            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update user preferences: {e}")
            conn.rollback()
        finally:
            conn.close()

    # =========================================================================
    # Private 헬퍼 메서드
    # =========================================================================

    def _update_user_stats(self, user_id: int, memory_type: str):
        """사용자 통계 업데이트"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()

            # 기존 설정 확인
            cursor.execute("SELECT user_id FROM user_preferences WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone() is not None

            if exists:
                if memory_type == self.MEMORY_JOURNAL:
                    cursor.execute("""
                        UPDATE user_preferences
                        SET total_journals = total_journals + 1, last_active_at = ?
                        WHERE user_id = ?
                    """, (now, user_id))
                elif memory_type == self.MEMORY_EVALUATION:
                    cursor.execute("""
                        UPDATE user_preferences
                        SET total_evaluations = total_evaluations + 1, last_active_at = ?
                        WHERE user_id = ?
                    """, (now, user_id))
                else:
                    cursor.execute("""
                        UPDATE user_preferences
                        SET last_active_at = ?
                        WHERE user_id = ?
                    """, (now, user_id))
            else:
                journals = 1 if memory_type == self.MEMORY_JOURNAL else 0
                evals = 1 if memory_type == self.MEMORY_EVALUATION else 0
                cursor.execute("""
                    INSERT INTO user_preferences (
                        user_id, total_evaluations, total_journals, created_at, last_active_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (user_id, evals, journals, now, now))

            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update user stats: {e}")
        finally:
            conn.close()

    def _update_access_time(self, memory_ids: List[int]):
        """기억 접근 시간 업데이트"""
        if not memory_ids:
            return

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            now = datetime.now().isoformat()
            placeholders = ','.join(['?' for _ in memory_ids])
            cursor.execute(f"""
                UPDATE user_memories
                SET last_accessed_at = ?
                WHERE id IN ({placeholders})
            """, [now] + memory_ids)
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update access time: {e}")
        finally:
            conn.close()

    def _format_journals(self, journals: List[Dict[str, Any]]) -> str:
        """저널을 포맷팅"""
        lines = []
        for j in journals:
            created = j.get('created_at', '')[:10]
            content = j.get('content', {})
            text = content.get('text', '')[:100]
            ticker = j.get('ticker', '')

            if ticker:
                lines.append(f"- [{created}] ({ticker}) \"{text}\"")
            else:
                lines.append(f"- [{created}] \"{text}\"")

        return '\n'.join(lines)

    def _format_evaluations(self, evals: List[Dict[str, Any]]) -> str:
        """평가를 포맷팅"""
        lines = []
        for e in evals:
            created = e.get('created_at', '')[:10]
            content = e.get('content', {})

            # 요약이 있으면 사용, 없으면 응답에서 추출
            summary = e.get('summary')
            if not summary:
                response = content.get('response_summary', '')
                summary = response[:100] + '...' if len(response) > 100 else response

            ticker = e.get('ticker', '')
            lines.append(f"- [{created}] {ticker}: {summary}")

        return '\n'.join(lines)

    def _generate_summary(
        self,
        content: Dict[str, Any],
        ticker: Optional[str],
        ticker_name: Optional[str]
    ) -> str:
        """기억 요약 생성 (Layer 2용)"""
        text = content.get('text', content.get('response_summary', ''))
        if not text:
            return ''

        # 간단한 요약 생성 (LLM 없이 규칙 기반)
        # 실제로는 LLM을 사용할 수 있지만, 비용 절감을 위해 규칙 기반 사용
        ticker_prefix = f"{ticker}: " if ticker else ""
        summary = text[:150].replace('\n', ' ').strip()

        return f"{ticker_prefix}{summary}"

    def _generate_compressed(
        self,
        summary: Optional[str],
        ticker: Optional[str],
        ticker_name: Optional[str]
    ) -> str:
        """한줄 압축 생성 (Layer 3용)"""
        if not summary:
            return ''

        # 한줄 압축 (최대 50자)
        ticker_prefix = f"{ticker} " if ticker else ""
        compressed = summary[:50].replace('\n', ' ').strip()

        return f"{ticker_prefix}{compressed}"

    def delete_memory(self, memory_id: int, user_id: int) -> bool:
        """
        특정 기억 삭제 (사용자 소유 확인)

        Args:
            memory_id: 기억 ID
            user_id: 사용자 ID (소유자 확인용)

        Returns:
            bool: 삭제 성공 여부
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                DELETE FROM user_memories
                WHERE id = ? AND user_id = ?
            """, (memory_id, user_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete memory: {e}")
            return False
        finally:
            conn.close()

    def get_memory_stats(self, user_id: int) -> Dict[str, Any]:
        """사용자 기억 통계 조회"""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 타입별 개수
            cursor.execute("""
                SELECT memory_type, COUNT(*) as count
                FROM user_memories
                WHERE user_id = ?
                GROUP BY memory_type
            """, (user_id,))
            type_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # 압축 레이어별 개수
            cursor.execute("""
                SELECT compression_layer, COUNT(*) as count
                FROM user_memories
                WHERE user_id = ?
                GROUP BY compression_layer
            """, (user_id,))
            layer_counts = {f"layer_{row[0]}": row[1] for row in cursor.fetchall()}

            # 종목별 개수
            cursor.execute("""
                SELECT ticker, COUNT(*) as count
                FROM user_memories
                WHERE user_id = ? AND ticker IS NOT NULL
                GROUP BY ticker
                ORDER BY count DESC
                LIMIT 10
            """, (user_id,))
            ticker_counts = {row[0]: row[1] for row in cursor.fetchall()}

            return {
                'by_type': type_counts,
                'by_layer': layer_counts,
                'by_ticker': ticker_counts,
                'total': sum(type_counts.values())
            }
        except Exception as e:
            logger.error(f"Failed to get memory stats: {e}")
            return {}
        finally:
            conn.close()
