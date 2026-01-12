"""
Memory Compression Manager

Handles hierarchical compression of trading journal entries.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import json
import logging
import re
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class CompressionManager:
    """Manages trading memory compression operations."""

    def __init__(self, cursor, conn, language: str = "ko", enable_journal: bool = False):
        """
        Initialize CompressionManager.

        Args:
            cursor: SQLite cursor
            conn: SQLite connection
            language: Language code (ko/en)
            enable_journal: Whether journal feature is enabled
        """
        self.cursor = cursor
        self.conn = conn
        self.language = language
        self.enable_journal = enable_journal

    async def compress_old_entries(
        self,
        layer1_age_days: int = 7,
        layer2_age_days: int = 30,
        min_entries: int = 3
    ) -> Dict[str, Any]:
        """
        Compress old trading journal entries.

        Implements hierarchical memory compression:
        - Layer 1 -> Layer 2: Entries older than layer1_age_days
        - Layer 2 -> Layer 3: Entries older than layer2_age_days

        Args:
            layer1_age_days: Days after which to compress Layer 1 -> 2
            layer2_age_days: Days after which to compress Layer 2 -> 3
            min_entries: Minimum entries required for compression

        Returns:
            Dict: Compression results with statistics
        """
        if not self.enable_journal:
            return {"skipped": True, "reason": "journal_disabled"}

        try:
            from cores.agents.memory_compressor_agent import create_memory_compressor_agent
            from mcp_agent.workflows.llm.augmented_llm import RequestParams
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            results = {
                "layer1_to_layer2": {"processed": 0, "compressed": 0},
                "layer2_to_layer3": {"processed": 0, "compressed": 0},
                "intuitions_generated": 0,
                "errors": []
            }

            cutoff_layer1 = (datetime.now() - timedelta(days=layer1_age_days)).strftime("%Y-%m-%d")
            cutoff_layer2 = (datetime.now() - timedelta(days=layer2_age_days)).strftime("%Y-%m-%d")

            # Layer 1 -> Layer 2
            self.cursor.execute("""
                SELECT id, ticker, company_name, trade_date, profit_rate,
                       situation_analysis, judgment_evaluation, lessons,
                       pattern_tags, one_line_summary, buy_scenario
                FROM trading_journal
                WHERE compression_layer = 1 AND trade_date < ?
                ORDER BY trade_date ASC
            """, (cutoff_layer1,))
            layer1_entries = [dict(zip([d[0] for d in self.cursor.description], row))
                              for row in self.cursor.fetchall()]

            if len(layer1_entries) >= min_entries:
                logger.info(f"Compressing {len(layer1_entries)} Layer 1 entries")
                result = await self._compress_to_layer2(layer1_entries)
                results["layer1_to_layer2"] = result

            # Layer 2 -> Layer 3
            self.cursor.execute("""
                SELECT id, ticker, company_name, trade_date, profit_rate,
                       compressed_summary, pattern_tags, buy_scenario
                FROM trading_journal
                WHERE compression_layer = 2 AND trade_date < ?
                ORDER BY trade_date ASC
            """, (cutoff_layer2,))
            layer2_entries = [dict(zip([d[0] for d in self.cursor.description], row))
                              for row in self.cursor.fetchall()]

            if len(layer2_entries) >= min_entries:
                logger.info(f"Compressing {len(layer2_entries)} Layer 2 entries")
                result = await self._compress_to_layer3(layer2_entries)
                results["layer2_to_layer3"] = result
                results["intuitions_generated"] = result.get("intuitions_generated", 0)

            return results

        except Exception as e:
            logger.error(f"Error during compression: {e}")
            traceback.print_exc()
            return {"error": str(e)}

    async def _compress_to_layer2(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compress Layer 1 entries to Layer 2 (summary format)."""
        try:
            from cores.agents.memory_compressor_agent import create_memory_compressor_agent
            from mcp_agent.workflows.llm.augmented_llm import RequestParams
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            results = {"processed": len(entries), "compressed": 0, "errors": []}

            compressor_agent = create_memory_compressor_agent(self.language)

            async with compressor_agent:
                llm = await compressor_agent.attach_llm(OpenAIAugmentedLLM)

                entries_text = self._format_entries_for_compression(entries)
                prompt = self._build_layer2_prompt(entries_text, len(entries))

                response = await llm.generate_str(
                    message=prompt,
                    request_params=RequestParams(model="gpt-5.2", maxTokens=8000)
                )

            compression_data = self._parse_response(response)

            compressed_entries = compression_data.get('compressed_entries', [])
            for comp_entry in compressed_entries:
                original_ids = comp_entry.get('original_ids', [])
                compressed_summary = comp_entry.get('compressed_summary', '')
                key_lessons = json.dumps(comp_entry.get('key_lessons', []), ensure_ascii=False)

                for entry_id in original_ids:
                    self.cursor.execute("""
                        UPDATE trading_journal
                        SET compression_layer = 2, compressed_summary = ?,
                            lessons = ?, last_compressed_at = ?
                        WHERE id = ?
                    """, (compressed_summary, key_lessons,
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entry_id))
                    results["compressed"] += 1

            if not compressed_entries:
                for entry in entries:
                    summary = self._generate_simple_summary(entry)
                    self.cursor.execute("""
                        UPDATE trading_journal
                        SET compression_layer = 2, compressed_summary = ?,
                            last_compressed_at = ?
                        WHERE id = ?
                    """, (summary, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entry['id']))
                    results["compressed"] += 1

            self.conn.commit()
            return results

        except Exception as e:
            logger.error(f"Error in Layer 2 compression: {e}")
            return {"processed": len(entries), "compressed": 0, "errors": [str(e)]}

    async def _compress_to_layer3(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compress Layer 2 entries to Layer 3 and extract intuitions."""
        try:
            from cores.agents.memory_compressor_agent import create_memory_compressor_agent
            from mcp_agent.workflows.llm.augmented_llm import RequestParams
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            results = {"processed": len(entries), "compressed": 0, "intuitions_generated": 0, "errors": []}

            compressor_agent = create_memory_compressor_agent(self.language)

            async with compressor_agent:
                llm = await compressor_agent.attach_llm(OpenAIAugmentedLLM)

                entries_text = self._format_entries_for_intuition(entries)
                prompt = self._build_layer3_prompt(entries_text, len(entries))

                response = await llm.generate_str(
                    message=prompt,
                    request_params=RequestParams(model="gpt-5.2", maxTokens=8000)
                )

            compression_data = self._parse_response(response)

            new_intuitions = compression_data.get('new_intuitions', [])
            source_ids = [e['id'] for e in entries]
            for intuition in new_intuitions:
                if self._save_intuition(intuition, source_ids):
                    results["intuitions_generated"] += 1

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for entry in entries:
                self.cursor.execute("""
                    UPDATE trading_journal SET compression_layer = 3, last_compressed_at = ?
                    WHERE id = ?
                """, (now, entry['id']))
                results["compressed"] += 1

            self.conn.commit()
            return results

        except Exception as e:
            logger.error(f"Error in Layer 3 compression: {e}")
            return {"processed": len(entries), "compressed": 0, "intuitions_generated": 0, "errors": [str(e)]}

    def _format_entries_for_compression(self, entries: List[Dict[str, Any]]) -> str:
        """Format entries for LLM compression."""
        formatted = []
        for entry in entries:
            try:
                lessons = json.loads(entry.get('lessons', '[]')) if entry.get('lessons') else []
                lessons_str = ", ".join([l.get('action', '') for l in lessons[:3] if isinstance(l, dict)])
            except:
                lessons_str = ""

            try:
                tags = json.loads(entry.get('pattern_tags', '[]')) if entry.get('pattern_tags') else []
                tags_str = ", ".join(tags)
            except:
                tags_str = ""

            profit_emoji = "✅" if entry.get('profit_rate', 0) > 0 else "❌"
            formatted.append(
                f"[ID:{entry['id']}] {entry.get('company_name', '')}({entry.get('ticker', '')}) "
                f"{profit_emoji} {entry.get('profit_rate', 0):.1f}% | "
                f"요약: {entry.get('one_line_summary', 'N/A')} | 교훈: {lessons_str} | 태그: {tags_str}"
            )
        return "\n".join(formatted)

    def _format_entries_for_intuition(self, entries: List[Dict[str, Any]]) -> str:
        """Format entries for intuition extraction."""
        formatted = []
        for entry in entries:
            try:
                scenario = json.loads(entry.get('buy_scenario', '{}')) if entry.get('buy_scenario') else {}
                sector = scenario.get('sector', '알 수 없음')
            except:
                sector = '알 수 없음'

            try:
                tags = json.loads(entry.get('pattern_tags', '[]')) if entry.get('pattern_tags') else []
                tags_str = ", ".join(tags)
            except:
                tags_str = ""

            profit_emoji = "✅" if entry.get('profit_rate', 0) > 0 else "❌"
            formatted.append(
                f"[ID:{entry['id']}] {entry.get('company_name', '')} | 섹터: {sector} | "
                f"{profit_emoji} {entry.get('profit_rate', 0):.1f}% | "
                f"요약: {entry.get('compressed_summary', 'N/A')} | 태그: {tags_str}"
            )
        return "\n".join(formatted)

    def _generate_simple_summary(self, entry: Dict[str, Any]) -> str:
        """Generate simple summary without LLM."""
        try:
            scenario = json.loads(entry.get('buy_scenario', '{}')) if entry.get('buy_scenario') else {}
            sector = scenario.get('sector', '')
        except:
            sector = ''

        profit = entry.get('profit_rate', 0)
        result = "수익" if profit > 0 else "손실"
        summary = entry.get('one_line_summary', '')
        if summary:
            return summary[:100]
        return f"{sector} {result} {abs(profit):.1f}%"

    def _build_layer2_prompt(self, entries_text: str, count: int) -> str:
        """Build prompt for Layer 2 compression."""
        if self.language == "ko":
            return f"""
다음 매매일지 항목들을 Layer 2 (요약) 형식으로 압축해주세요.

## 압축 대상 ({count}건)
{entries_text}

## 요청사항
1. 각 항목을 "{{섹터}} + {{트리거}} → {{행동}} → {{결과}}" 형식으로 요약
2. 유사 패턴 그룹화
3. 반복 교훈 식별
4. 섹터별 통계 계산

JSON으로 응답해주세요.
"""
        else:
            return f"""
Compress these entries to Layer 2 (summary) format.

## Entries ({count})
{entries_text}

## Requirements
1. Summarize each as "{{sector}} + {{trigger}} → {{action}} → {{result}}"
2. Group similar patterns
3. Identify recurring lessons
4. Calculate sector stats

Respond in JSON.
"""

    def _build_layer3_prompt(self, entries_text: str, count: int) -> str:
        """Build prompt for Layer 3 / intuition extraction."""
        if self.language == "ko":
            return f"""
다음 압축된 기록에서 직관(Intuition)을 추출해주세요.

## 압축 기록 ({count}건)
{entries_text}

## 요청사항
1. 2회+ 반복 패턴에서 직관 추출
2. "{{조건}} = {{원칙}}" 형식으로 직관 생성
3. 신뢰도/적중률 계산
4. 섹터별/시장별/패턴별 분류
5. 실패/성공 패턴 모두 포함

JSON으로 응답해주세요.
"""
        else:
            return f"""
Extract intuitions from these compressed records.

## Records ({count})
{entries_text}

## Requirements
1. Extract from patterns appearing 2+ times
2. Generate as "{{condition}} = {{principle}}"
3. Calculate confidence/success rate
4. Categorize by sector/market/pattern
5. Include failure and success patterns

Respond in JSON.
"""

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse compression response."""
        try:
            markdown_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', response, re.DOTALL)
            if markdown_match:
                return json.loads(markdown_match.group(1))

            json_match = re.search(r'({[\s\S]*})', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                return json.loads(json_str)

            try:
                import json_repair
                repaired = json_repair.repair_json(response)
                return json.loads(repaired)
            except:
                pass

            return {"compressed_entries": [], "new_intuitions": []}

        except Exception as e:
            logger.warning(f"Failed to parse compression response: {e}")
            return {"compressed_entries": [], "new_intuitions": []}

    def _save_intuition(self, intuition: Dict[str, Any], source_ids: List[int]) -> bool:
        """Save intuition to database."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self.cursor.execute("""
                SELECT id FROM trading_intuitions
                WHERE condition = ? AND insight = ?
            """, (intuition.get('condition', ''), intuition.get('insight', '')))

            existing = self.cursor.fetchone()
            if existing:
                self.cursor.execute("""
                    UPDATE trading_intuitions
                    SET supporting_trades = supporting_trades + ?,
                        confidence = (confidence + ?) / 2,
                        success_rate = (success_rate + ?) / 2,
                        source_journal_ids = ?,
                        last_validated_at = ?
                    WHERE id = ?
                """, (
                    intuition.get('supporting_trades', 1),
                    intuition.get('confidence', 0.5),
                    intuition.get('success_rate', 0.5),
                    json.dumps(source_ids),
                    now, existing[0]
                ))
            else:
                self.cursor.execute("""
                    INSERT INTO trading_intuitions
                    (category, subcategory, condition, insight, confidence,
                     supporting_trades, success_rate, source_journal_ids,
                     created_at, last_validated_at, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    intuition.get('category', 'pattern'),
                    intuition.get('subcategory', ''),
                    intuition.get('condition', ''),
                    intuition.get('insight', ''),
                    intuition.get('confidence', 0.5),
                    intuition.get('supporting_trades', 1),
                    intuition.get('success_rate', 0.5),
                    json.dumps(source_ids), now, now, 1
                ))

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error saving intuition: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get compression statistics."""
        if not self.enable_journal:
            return {"enabled": False}

        try:
            stats = {"enabled": True}

            self.cursor.execute("""
                SELECT compression_layer, COUNT(*) as count
                FROM trading_journal GROUP BY compression_layer
            """)
            layer_counts = {}
            for row in self.cursor.fetchall():
                layer_counts[row[0]] = row[1]

            stats['entries_by_layer'] = {
                'layer1_detailed': layer_counts.get(1, 0),
                'layer2_summarized': layer_counts.get(2, 0),
                'layer3_compressed': layer_counts.get(3, 0)
            }

            self.cursor.execute("SELECT COUNT(*) FROM trading_intuitions WHERE is_active = 1")
            stats['active_intuitions'] = self.cursor.fetchone()[0]

            self.cursor.execute("""
                SELECT MIN(trade_date) FROM trading_journal WHERE compression_layer = 1
            """)
            result = self.cursor.fetchone()
            stats['oldest_uncompressed'] = result[0] if result and result[0] else None

            self.cursor.execute("""
                SELECT AVG(confidence), AVG(success_rate)
                FROM trading_intuitions WHERE is_active = 1
            """)
            result = self.cursor.fetchone()
            if result:
                stats['avg_intuition_confidence'] = result[0] or 0
                stats['avg_intuition_success_rate'] = result[1] or 0

            return stats

        except Exception as e:
            logger.error(f"Error getting compression stats: {e}")
            return {}

    def cleanup_stale_data(
        self,
        max_principles: int = 50,
        max_intuitions: int = 50,
        min_confidence: float = 0.3,
        stale_days: int = 90,
        archive_days: int = 365,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """Clean up stale and low-quality data."""
        if not self.enable_journal:
            return {"skipped": True, "reason": "journal_disabled"}

        try:
            stats = {"principles_deactivated": 0, "intuitions_deactivated": 0,
                     "journal_entries_archived": 0, "dry_run": dry_run,
                     "low_confidence_principles": 0, "stale_principles": 0,
                     "excess_principles": 0, "low_confidence_intuitions": 0,
                     "old_layer3_entries": 0}

            now = datetime.now()
            stale_cutoff = (now - timedelta(days=stale_days)).strftime("%Y-%m-%d")
            archive_cutoff = (now - timedelta(days=archive_days)).strftime("%Y-%m-%d")

            # Low confidence principles
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_principles
                WHERE is_active = 1 AND confidence < ?
            """, (min_confidence,))
            low_conf = self.cursor.fetchone()[0]
            stats["low_confidence_principles"] = low_conf

            if not dry_run and low_conf > 0:
                self.cursor.execute("""
                    UPDATE trading_principles SET is_active = 0
                    WHERE is_active = 1 AND confidence < ?
                """, (min_confidence,))
                stats["principles_deactivated"] += low_conf

            # Stale principles
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_principles
                WHERE is_active = 1
                  AND (last_validated_at IS NULL OR last_validated_at < ?)
                  AND created_at < ?
            """, (stale_cutoff, stale_cutoff))
            stale = self.cursor.fetchone()[0]

            if not dry_run and stale > 0:
                self.cursor.execute("""
                    UPDATE trading_principles SET is_active = 0
                    WHERE is_active = 1
                      AND (last_validated_at IS NULL OR last_validated_at < ?)
                      AND created_at < ?
                """, (stale_cutoff, stale_cutoff))
                stats["principles_deactivated"] += stale

            # Enforce max_principles
            self.cursor.execute("SELECT COUNT(*) FROM trading_principles WHERE is_active = 1")
            active = self.cursor.fetchone()[0]
            if active > max_principles:
                excess = active - max_principles
                if not dry_run:
                    self.cursor.execute("""
                        UPDATE trading_principles SET is_active = 0
                        WHERE id IN (
                            SELECT id FROM trading_principles WHERE is_active = 1
                            ORDER BY confidence ASC LIMIT ?
                        )
                    """, (excess,))
                    stats["principles_deactivated"] += excess

            # Low confidence intuitions
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_intuitions
                WHERE is_active = 1 AND confidence < ?
            """, (min_confidence,))
            low_conf = self.cursor.fetchone()[0]

            if not dry_run and low_conf > 0:
                self.cursor.execute("""
                    UPDATE trading_intuitions SET is_active = 0
                    WHERE is_active = 1 AND confidence < ?
                """, (min_confidence,))
                stats["intuitions_deactivated"] += low_conf

            # Archive old Layer 3
            self.cursor.execute("""
                SELECT COUNT(*) FROM trading_journal
                WHERE compression_layer = 3 AND trade_date < ?
            """, (archive_cutoff,))
            old = self.cursor.fetchone()[0]

            if not dry_run and old > 0:
                self.cursor.execute("""
                    DELETE FROM trading_journal
                    WHERE compression_layer = 3 AND trade_date < ?
                """, (archive_cutoff,))
                stats["journal_entries_archived"] = old

            if not dry_run:
                self.conn.commit()

            logger.info(
                f"Cleanup {'(dry-run) ' if dry_run else ''}complete: "
                f"principles={stats['principles_deactivated']}, "
                f"intuitions={stats['intuitions_deactivated']}, "
                f"archived={stats['journal_entries_archived']}"
            )

            return stats

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return {"error": str(e)}
