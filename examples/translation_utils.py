#!/usr/bin/env python3
"""
Dashboard Data Translation Utilities
AI 기반 대시보드 데이터 번역 유틸리티
"""
import asyncio
import json
import logging
from typing import Dict, Any, List

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

logger = logging.getLogger(__name__)


class DashboardTranslator:
    """대시보드 데이터 번역 클래스"""
    
    def __init__(self, model: str = "gpt-5-nano"):
        """
        번역기 초기화
        
        Args:
            model: 사용할 OpenAI 모델 (기본: gpt-5-nano)
        """
        self.model = model
        
        # 번역 캐시 (동일한 텍스트 재번역 방지)
        self.translation_cache = {}
        
        # 번역 에이전트 생성
        self.translation_agent = Agent(
            name="translation_agent",
            instruction="""You are a professional Korean-to-English translator specializing in financial and stock market terminology.

Your task:
1. Translate Korean text to natural, professional English
2. Maintain accuracy of financial terms (PER, PBR, EPS, etc.)
3. Keep the original tone and meaning
4. Use clear, investor-friendly language
5. For technical/financial jargon, use standard English financial terminology

Guidelines:
- Translate naturally, not word-by-word
- Keep numbers, percentages, and dates unchanged
- Maintain the level of formality
- Avoid overly literal translations
- Use proper financial English conventions

Return ONLY the translated text without explanations or comments.
"""
        )
    
    async def translate_text(self, text: str, from_lang: str = "ko", to_lang: str = "en") -> str:
        """
        단일 텍스트 번역
        
        Args:
            text: 번역할 텍스트
            from_lang: 원본 언어
            to_lang: 대상 언어
            
        Returns:
            번역된 텍스트
        """
        if not text or not isinstance(text, str) or len(text.strip()) == 0:
            return text
        
        # 캐시 확인
        cache_key = f"{from_lang}_{to_lang}_{text}"
        if cache_key in self.translation_cache:
            logger.debug(f"캐시에서 번역 반환: {text[:30]}...")
            return self.translation_cache[cache_key]
        
        try:
            llm = await self.translation_agent.attach_llm(OpenAIAugmentedLLM)
            translated = await llm.generate_str(
                message=f"Translate the following Korean text to English:\n\n{text}",
                request_params=RequestParams(
                    model=self.model,
                    maxTokens=100000,
                    max_iterations=1
                )
            )
            
            translated = translated.strip()
            
            # 캐시 저장
            self.translation_cache[cache_key] = translated
            
            logger.debug(f"번역 완료: {text[:30]}... -> {translated[:30]}...")
            return translated
            
        except Exception as e:
            logger.error(f"번역 중 오류: {str(e)}")
            return text  # 오류 시 원본 반환
    
    async def translate_batch(self, texts: List[str], from_lang: str = "ko", to_lang: str = "en") -> List[str]:
        """
        배치 번역 (여러 텍스트를 한 번에 번역 - 토큰 절약)
        
        Args:
            texts: 번역할 텍스트 리스트
            from_lang: 원본 언어
            to_lang: 대상 언어
            
        Returns:
            번역된 텍스트 리스트
        """
        if not texts:
            return []
        
        # 빈 텍스트나 None 필터링
        valid_indices = []
        valid_texts = []
        for i, t in enumerate(texts):
            if t and isinstance(t, str) and len(t.strip()) > 0:
                valid_indices.append(i)
                valid_texts.append(t)
        
        if not valid_texts:
            return texts
        
        try:
            # JSON 형태로 묶어서 한 번에 번역
            batch_input = []
            for i, text in enumerate(valid_texts):
                batch_input.append(f"[{i+1}] {text}")
            
            batch_text = "\n\n".join(batch_input)
            
            llm = await self.translation_agent.attach_llm(OpenAIAugmentedLLM)
            translated_batch = await llm.generate_str(
                message=f"""Translate the following numbered Korean texts to English.
Maintain the numbering format [1], [2], etc. in your response.

{batch_text}

Return the translations in the same numbered format.""",
                request_params=RequestParams(
                    model=self.model,
                    maxTokens=100000,
                    max_iterations=1
                )
            )
            
            # 번호 기반으로 파싱
            import re
            pattern = r'\[(\d+)\]\s*(.*?)(?=\[\d+\]|$)'
            matches = re.findall(pattern, translated_batch, re.DOTALL)
            
            translated_dict = {}
            for num, content in matches:
                translated_dict[int(num)] = content.strip()
            
            # 결과 검증 및 구성
            result = list(texts)  # 원본 복사
            for i, valid_idx in enumerate(valid_indices):
                if (i + 1) in translated_dict:
                    result[valid_idx] = translated_dict[i + 1]
                else:
                    logger.warning(f"번역 결과 누락: 인덱스 {i+1}")
            
            return result
            
        except Exception as e:
            logger.error(f"배치 번역 중 오류: {str(e)}. 개별 번역으로 폴백합니다.")
            # 개별 번역으로 폴백
            result = []
            for text in texts:
                if text and isinstance(text, str) and len(text.strip()) > 0:
                    translated = await self.translate_text(text, from_lang, to_lang)
                    result.append(translated)
                else:
                    result.append(text)
            return result
    
    async def translate_dashboard_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        대시보드 전체 데이터 번역
        
        Args:
            data: 원본 대시보드 데이터
            
        Returns:
            번역된 대시보드 데이터
        """
        logger.info("대시보드 데이터 번역 시작...")
        
        # 딥카피로 원본 보존
        import copy
        translated_data = copy.deepcopy(data)
        
        # 1. 고정 값 매핑 (섹터, 기간 등)
        translated_data = self._translate_fixed_values(translated_data)
        
        # 2. 자유 텍스트 필드 번역 (scenario의 rationale 등)
        translated_data = await self._translate_free_text_fields(translated_data)
        
        logger.info("대시보드 데이터 번역 완료!")
        return translated_data
    
    def _translate_fixed_values(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """고정 값 매핑 (섹터, 기간 등)"""

        # 정적 매핑 테이블
        STATIC_MAPPINGS = {
            # 섹터 (산업군)
            "자동차/완성차": "Automotive/Complete Vehicles",
            "반도체": "Semiconductor",
            "실전투자": "Live Trading",
            "IT/소프트웨어": "IT/Software",
            "바이오/제약": "Bio/Pharma",
            "화학": "Chemical",
            "금융": "Finance",
            "유통": "Retail",
            "건설": "Construction",
            "철강/금속": "Steel/Metal",
            "전기전자": "Electronics",
            "기계": "Machinery",
            "운송": "Transportation",
            "서비스": "Service",
            "미디어/엔터": "Media/Entertainment",
            "제지/포장재": "Paper/Packaging",
            "섬유/의류": "Textile/Apparel",
            "식품/음료": "Food/Beverage",
            "에너지": "Energy",
            "통신": "Telecom",
            "기타": "Others",

            # 투자 기간
            "단기": "Short-term",
            "중기": "Mid-term",
            "장기": "Long-term",
            "해당없음": "N/A",

            # 결정 타입
            "매수": "Buy",
            "진입": "Entry",
            "매도": "Sell",
            "보류": "Hold",
            "관망": "Watch",

            # 시장 상태
            "횡보": "Sideways",
            "상승": "Uptrend",
            "하락": "Downtrend",
            "변동성 확대": "High Volatility",
            "안정": "Stable",
            "과매수": "Overbought",
            "과매도": "Oversold",

            # 기술적 추세 (holding_decisions용)
            "상승 - 강": "Uptrend - Strong",
            "상승 - 약": "Uptrend - Weak",
            "하락 - 강": "Downtrend - Strong",
            "하락 - 약": "Downtrend - Weak",
            "횡보 - 강": "Sideways - Strong",
            "횡보 - 약": "Sideways - Weak",
        }

        def replace_in_dict(obj, parent=None, parent_key=None):
            """재귀적으로 딕셔너리 탐색하며 값과 키를 모두 교체"""
            if isinstance(obj, dict):
                # 먼저 키를 교체할 새 딕셔너리 생성
                new_dict = {}
                for key, value in obj.items():
                    # 키 교체
                    new_key = STATIC_MAPPINGS.get(key, key)

                    # 값이 문자열이면 교체
                    if isinstance(value, str):
                        new_value = STATIC_MAPPINGS.get(value, value)
                    elif isinstance(value, (dict, list)):
                        # 재귀적으로 처리
                        new_value = replace_in_dict(value)
                    else:
                        new_value = value

                    new_dict[new_key] = new_value

                # 부모가 있으면 부모의 해당 키를 업데이트
                if parent is not None and parent_key is not None:
                    parent[parent_key] = new_dict

                return new_dict

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    if isinstance(item, str):
                        # 리스트 항목도 교체
                        obj[i] = STATIC_MAPPINGS.get(item, item)
                    elif isinstance(item, (dict, list)):
                        obj[i] = replace_in_dict(item)
                return obj

            return obj

        # 데이터 전체를 재귀적으로 처리
        return replace_in_dict(data)
    
    async def _translate_free_text_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """자유 텍스트 필드 AI 번역"""
        
        # 번역할 필드 목록 (구조화된 데이터)
        FREE_TEXT_FIELDS = [
            # scenario 내부 필드
            'rationale',
            'skip_reason',
            'sell_reason',
            'adjustment_reason',
            'portfolio_analysis',
            'valuation_analysis',
            'sector_outlook',
            'market_condition',
            'decision',
            'max_portfolio_size',  # "7~8개" 같은 값
            # trading_scenarios 내부
            'portfolio_context',
            'volume_baseline',  # "20일 평균 거래량의 2배" 같은 값
            'primary_support',
            'secondary_support',
            'primary_resistance',
            'secondary_resistance',
            # holding_decisions 필드 (AI 매도 판단)
            'technical_trend',  # 기술적 추세 (일부는 STATIC_MAPPINGS에서 처리)
            'volume_analysis',  # 거래량 분석
            'market_condition_impact',  # 시장 상황 영향
            'time_factor',  # 시간 요소
            'reason',  # portfolio_adjustment.reason 등
            # 최상위 필드
            'company_name',
            'name',  # real_portfolio의 name
            'sector',
        ]
        
        # trading_scenarios의 리스트형 필드들
        LIST_FIELDS_IN_SCENARIOS = [
            'sell_triggers',
            'hold_conditions',
        ]
        
        # 모든 자유 텍스트 수집
        texts_to_translate = []
        text_locations = []  # (참조 객체, 키) 튜플
        
        def collect_texts(obj, path=""):
            """재귀적으로 번역할 텍스트 수집"""
            if isinstance(obj, dict):
                for key, value in obj.items():
                    current_path = f"{path}.{key}" if path else key
                    
                    # 1. 일반 문자열 필드
                    if key in FREE_TEXT_FIELDS and isinstance(value, str) and value.strip():
                        texts_to_translate.append(value)
                        text_locations.append((obj, key))
                    
                    # 2. trading_scenarios 내부의 리스트 필드들
                    elif key in LIST_FIELDS_IN_SCENARIOS and isinstance(value, list):
                        for i, item in enumerate(value):
                            if isinstance(item, str) and item.strip():
                                texts_to_translate.append(item)
                                text_locations.append((value, i))  # 리스트와 인덱스 저장
                    
                    # 3. 재귀 탐색
                    elif isinstance(value, (dict, list)):
                        collect_texts(value, current_path)
                        
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    collect_texts(item, f"{path}[{i}]")
        
        # 텍스트 수집
        collect_texts(data)
        
        logger.info(f"번역할 자유 텍스트 {len(texts_to_translate)}개 발견")
        
        if not texts_to_translate:
            return data
        
        # 배치 번역 (한 번에 너무 많으면 나눠서 처리)
        BATCH_SIZE = 50  # 한 번에 최대 50개씩
        all_translated = []
        
        for i in range(0, len(texts_to_translate), BATCH_SIZE):
            batch = texts_to_translate[i:i+BATCH_SIZE]
            logger.info(f"배치 번역 중 ({i+1}~{min(i+BATCH_SIZE, len(texts_to_translate))}/{len(texts_to_translate)})")
            translated_batch = await self.translate_batch(batch)
            all_translated.extend(translated_batch)
        
        # 번역된 텍스트 적용
        for (obj, key), translated in zip(text_locations, all_translated):
            if isinstance(key, int):
                # 리스트 항목
                obj[key] = translated
            else:
                # 딕셔너리 항목
                obj[key] = translated
        
        return data


def create_translation_mapping_file():
    """정적 매핑 테이블을 JSON 파일로 생성 (참고용)"""
    mappings = {
        "ko": {
            "sector": {
                "자동차/완성차": "자동차/완성차",
                "반도체": "반도체",
                "실전투자": "실전투자",
                # ... more
            },
            "period": {
                "단기": "단기",
                "중기": "중기",
                "장기": "장기"
            }
        },
        "en": {
            "sector": {
                "자동차/완성차": "Automotive/Complete Vehicles",
                "반도체": "Semiconductor",
                "실전투자": "Live Trading",
                # ... more
            },
            "period": {
                "단기": "Short-term",
                "중기": "Mid-term",
                "장기": "Long-term"
            }
        }
    }
    
    with open("translation_mapping.json", "w", encoding="utf-8") as f:
        json.dump(mappings, f, ensure_ascii=False, indent=2)
    
    logger.info("translation_mapping.json 파일 생성 완료")


if __name__ == "__main__":
    # 테스트 코드
    import os
    
    async def test():
        translator = DashboardTranslator(model="gpt-5-nano")
        
        # 단일 번역 테스트
        result = await translator.translate_text("자동차 산업의 전망이 밝습니다.")
        print(f"단일 번역: {result}")
        
        # 배치 번역 테스트
        texts = [
            "삼성전자는 반도체 업계의 선두주자입니다.",
            "현대차의 전기차 사업이 성장하고 있습니다.",
            "SK하이닉스는 메모리 반도체에 강점이 있습니다."
        ]
        results = await translator.translate_batch(texts)
        print(f"배치 번역: {results}")
    
    asyncio.run(test())
