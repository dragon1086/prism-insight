#!/usr/bin/env python3
"""
Dashboard Data Translation Utilities
AI-based dashboard data translation utilities
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
    """Dashboard data translation class"""
    
    def __init__(self, model: str = "gpt-5.6-luna"):
        """
        Initialize translator

        Args:
            model: OpenAI model to use (default: gpt-5.4-nano)
        """
        self.model = model

        # Translation cache (prevent re-translating identical text)
        self.translation_cache = {}

        # Create translation agent
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
        Translate single text

        Args:
            text: Text to translate
            from_lang: Source language
            to_lang: Target language

        Returns:
            Translated text
        """
        if not text or not isinstance(text, str) or len(text.strip()) == 0:
            return text

        # Check cache
        cache_key = f"{from_lang}_{to_lang}_{text}"
        if cache_key in self.translation_cache:
            logger.debug(f"Returning translation from cache: {text[:30]}...")
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

            # Save to cache
            self.translation_cache[cache_key] = translated

            logger.debug(f"Translation completed: {text[:30]}... -> {translated[:30]}...")
            return translated

        except Exception as e:
            logger.error(f"Error during translation: {str(e)}")
            return text  # Return original on error

    async def translate_batch(self, texts: List[str], from_lang: str = "ko", to_lang: str = "en") -> List[str]:
        """
        Batch translation (translate multiple texts at once - saves tokens)

        Args:
            texts: List of texts to translate
            from_lang: Source language
            to_lang: Target language

        Returns:
            List of translated texts
        """
        if not texts:
            return []

        # Filter out empty texts or None
        valid_indices = []
        valid_texts = []
        for i, t in enumerate(texts):
            if t and isinstance(t, str) and len(t.strip()) > 0:
                valid_indices.append(i)
                valid_texts.append(t)
        
        if not valid_texts:
            return texts

        try:
            # Bundle in JSON format for single translation
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

            # Parse based on numbering
            import re
            pattern = r'\[(\d+)\]\s*(.*?)(?=\[\d+\]|$)'
            matches = re.findall(pattern, translated_batch, re.DOTALL)

            translated_dict = {}
            for num, content in matches:
                translated_dict[int(num)] = content.strip()

            # Validate and construct result
            result = list(texts)  # Copy original
            for i, valid_idx in enumerate(valid_indices):
                if (i + 1) in translated_dict:
                    result[valid_idx] = translated_dict[i + 1]
                else:
                    logger.warning(f"Translation result missing: index {i+1}")

            return result

        except Exception as e:
            logger.error(f"Error during batch translation: {str(e)}. Falling back to individual translation.")
            # Fallback to individual translation
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
        Translate entire dashboard data

        Args:
            data: Original dashboard data

        Returns:
            Translated dashboard data
        """
        logger.info("Starting dashboard data translation...")

        # Preserve original with deep copy
        import copy
        translated_data = copy.deepcopy(data)

        # 1. Fixed value mapping (sectors, periods, etc.)
        translated_data = self._translate_fixed_values(translated_data)

        # 2. Free text field translation (rationale in scenario, etc.)
        translated_data = await self._translate_free_text_fields(translated_data)

        logger.info("Dashboard data translation completed!")
        return translated_data

    def _translate_fixed_values(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Fixed value mapping (sectors, periods, etc.)"""

        # Static mapping table
        STATIC_MAPPINGS = {
            # Sectors (industry groups)
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

            # Investment period
            "단기": "Short-term",
            "중기": "Mid-term",
            "장기": "Long-term",
            "해당없음": "N/A",

            # Decision types
            "매수": "Buy",
            "진입": "Entry",
            "매도": "Sell",
            "보류": "Hold",
            "관망": "Watch",

            # Market conditions
            "횡보": "Sideways",
            "상승": "Uptrend",
            "하락": "Downtrend",
            "변동성 확대": "High Volatility",
            "안정": "Stable",
            "과매수": "Overbought",
            "과매도": "Oversold",

            # Technical trends (for holding_decisions)
            "상승 - 강": "Uptrend - Strong",
            "상승 - 약": "Uptrend - Weak",
            "하락 - 강": "Downtrend - Strong",
            "하락 - 약": "Downtrend - Weak",
            "횡보 - 강": "Sideways - Strong",
            "횡보 - 약": "Sideways - Weak",
        }

        def replace_in_dict(obj, parent=None, parent_key=None):
            """Recursively traverse dictionary and replace both values and keys"""
            if isinstance(obj, dict):
                # First create new dictionary to replace keys
                new_dict = {}
                for key, value in obj.items():
                    # Replace key
                    new_key = STATIC_MAPPINGS.get(key, key)

                    # If value is string, replace it
                    if isinstance(value, str):
                        new_value = STATIC_MAPPINGS.get(value, value)
                    elif isinstance(value, (dict, list)):
                        # Process recursively
                        new_value = replace_in_dict(value)
                    else:
                        new_value = value

                    new_dict[new_key] = new_value

                # If parent exists, update the corresponding key in parent
                if parent is not None and parent_key is not None:
                    parent[parent_key] = new_dict

                return new_dict

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    if isinstance(item, str):
                        # Replace list items too
                        obj[i] = STATIC_MAPPINGS.get(item, item)
                    elif isinstance(item, (dict, list)):
                        obj[i] = replace_in_dict(item)
                return obj

            return obj

        # Process entire data recursively
        return replace_in_dict(data)

    async def _translate_free_text_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """AI translation of free text fields"""

        # List of fields to translate (structured data)
        FREE_TEXT_FIELDS = [
            # Fields inside scenario
            'rationale',
            'skip_reason',
            'sell_reason',
            'adjustment_reason',
            'portfolio_analysis',
            'valuation_analysis',
            'sector_outlook',
            'market_condition',
            'decision',
            'max_portfolio_size',  # Values like "7~8 stocks"
            # Inside trading_scenarios
            'portfolio_context',
            'volume_baseline',  # Values like "2x the 20-day average volume"
            'primary_support',
            'secondary_support',
            'primary_resistance',
            'secondary_resistance',
            # holding_decisions fields (AI sell decision)
            'technical_trend',  # Technical trend (some handled in STATIC_MAPPINGS)
            'volume_analysis',  # Volume analysis
            'market_condition_impact',  # Market condition impact
            'time_factor',  # Time factor
            'reason',  # portfolio_adjustment.reason etc.
            # Top-level fields
            'company_name',
            'name',  # name in real_portfolio
            'sector',
        ]

        # List-type fields in trading_scenarios
        LIST_FIELDS_IN_SCENARIOS = [
            'sell_triggers',
            'hold_conditions',
        ]

        # Collect all free text
        texts_to_translate = []
        text_locations = []  # (reference object, key) tuple

        def collect_texts(obj, path=""):
            """Recursively collect texts to translate"""
            if isinstance(obj, dict):
                for key, value in obj.items():
                    current_path = f"{path}.{key}" if path else key

                    # 1. Regular string fields
                    if key in FREE_TEXT_FIELDS and isinstance(value, str) and value.strip():
                        texts_to_translate.append(value)
                        text_locations.append((obj, key))

                    # 2. List fields inside trading_scenarios
                    elif key in LIST_FIELDS_IN_SCENARIOS and isinstance(value, list):
                        for i, item in enumerate(value):
                            if isinstance(item, str) and item.strip():
                                texts_to_translate.append(item)
                                text_locations.append((value, i))  # Store list and index

                    # 3. Recursive search
                    elif isinstance(value, (dict, list)):
                        collect_texts(value, current_path)

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    collect_texts(item, f"{path}[{i}]")

        # Collect texts
        collect_texts(data)

        logger.info(f"Found {len(texts_to_translate)} free text fields to translate")

        if not texts_to_translate:
            return data

        # Batch translation (split if too many at once)
        BATCH_SIZE = 50  # Maximum 50 at a time
        all_translated = []

        for i in range(0, len(texts_to_translate), BATCH_SIZE):
            batch = texts_to_translate[i:i+BATCH_SIZE]
            logger.info(f"Translating batch ({i+1}~{min(i+BATCH_SIZE, len(texts_to_translate))}/{len(texts_to_translate)})")
            translated_batch = await self.translate_batch(batch)
            all_translated.extend(translated_batch)

        # Apply translated texts
        for (obj, key), translated in zip(text_locations, all_translated):
            if isinstance(key, int):
                # List item
                obj[key] = translated
            else:
                # Dictionary item
                obj[key] = translated

        return data


def create_translation_mapping_file():
    """Generate static mapping table as JSON file (for reference)"""
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

    logger.info("translation_mapping.json file created")


if __name__ == "__main__":
    # Test code
    import os

    async def test():
        translator = DashboardTranslator(model="gpt-5.6-luna")

        # Single translation test
        result = await translator.translate_text("The automotive industry outlook is bright.")
        print(f"Single translation: {result}")

        # Batch translation test
        texts = [
            "Samsung Electronics is a leader in the semiconductor industry.",
            "Hyundai Motor's electric vehicle business is growing.",
            "SK Hynix has strengths in memory semiconductors."
        ]
        results = await translator.translate_batch(texts)
        print(f"Batch translation: {results}")
    
    asyncio.run(test())
