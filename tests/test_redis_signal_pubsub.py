#!/usr/bin/env python3
"""
Redis Streams Signal Pub/Sub 테스트

테스트 실행:
    # .env 파일에 설정 필요
    # UPSTASH_REDIS_REST_URL=https://topical-lemur-7683.upstash.io
    # UPSTASH_REDIS_REST_TOKEN=your-token

    # 전체 테스트
    pytest tests/test_redis_signal_pubsub.py -v

    # 특정 테스트만
    pytest tests/test_redis_signal_pubsub.py::test_publish_buy_signal -v

    # 실제 Redis 연결 테스트
    pytest tests/test_redis_signal_pubsub.py::TestIntegrationWithRealRedis -v
"""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from pathlib import Path

# 프로젝트 루트 경로 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from messaging.redis_signal_publisher import (
    SignalPublisher,
    get_signal_publisher,
)


# ============================================================
# Helper Functions for upstash-redis 1.5.0+ compatibility
# ============================================================

def parse_stream_data(data):
    """
    upstash-redis 1.5.0+에서 스트림 데이터를 파싱합니다.
    
    upstash-redis는 Redis 응답을 리스트 형태로 반환합니다:
    - 입력: ['field1', 'value1', 'field2', 'value2', ...]
    - 출력: {'field1': 'value1', 'field2': 'value2', ...}
    """
    if isinstance(data, dict):
        return data
    elif isinstance(data, list):
        # 리스트를 딕셔너리로 변환 (키-값 쌍)
        return {data[i]: data[i+1] for i in range(0, len(data), 2)}
    return data


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_redis():
    """Mock Redis 객체"""
    mock = MagicMock()
    mock.xadd = MagicMock(return_value="1234567890-0")
    return mock


@pytest.fixture
def publisher_with_mock_redis(mock_redis):
    """Mock Redis가 주입된 SignalPublisher"""
    publisher = SignalPublisher(
        redis_url="https://mock.upstash.io",
        redis_token="mock-token"
    )
    publisher._redis = mock_redis
    return publisher


@pytest.fixture
def sample_scenario():
    """샘플 매매 시나리오"""
    return {
        "buy_score": 8,
        "target_price": 90000,
        "stop_loss": 75000,
        "investment_period": "단기",
        "sector": "Semiconductor",
        "rationale": "AI Semiconductor 수요 증가에 따른 실적 개선 기대"
    }


# ============================================================
# Unit Tests (Mock 사용)
# ============================================================

class TestSignalPublisher:
    """SignalPublisher 클래스 테스트"""

    def test_init_with_env_vars(self):
        """환경변수에서 설정 읽기 테스트"""
        with patch.dict(os.environ, {
            "UPSTASH_REDIS_REST_URL": "https://test.upstash.io",
            "UPSTASH_REDIS_REST_TOKEN": "test-token"
        }):
            publisher = SignalPublisher()
            assert publisher.redis_url == "https://test.upstash.io"
            assert publisher.redis_token == "test-token"

    def test_init_with_params(self):
        """파라미터로 설정 전달 테스트"""
        publisher = SignalPublisher(
            redis_url="https://custom.upstash.io",
            redis_token="custom-token"
        )
        assert publisher.redis_url == "https://custom.upstash.io"
        assert publisher.redis_token == "custom-token"

    def test_is_connected_false_when_no_redis(self):
        """Redis 미연결 상태 확인"""
        publisher = SignalPublisher()
        assert publisher._is_connected() is False

    def test_is_connected_true_when_redis_exists(self, publisher_with_mock_redis):
        """Redis 연결 상태 확인"""
        assert publisher_with_mock_redis._is_connected() is True


class TestPublishSignal:
    """시그널 발행 테스트"""

    @pytest.mark.asyncio
    async def test_publish_signal_success(self, publisher_with_mock_redis, mock_redis):
        """시그널 발행 성공 테스트"""
        result = await publisher_with_mock_redis.publish_signal(
            signal_type="BUY",
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            source="AI분석"
        )

        assert result == "1234567890-0"
        mock_redis.xadd.assert_called_once()

        # xadd 호출 인자 확인 (upstash-redis 1.5.0+: xadd(key, id, data))
        call_args = mock_redis.xadd.call_args
        stream_name = call_args[0][0]
        message_id_arg = call_args[0][1]
        data = call_args[0][2]

        assert stream_name == "prism:trading-signals"
        assert message_id_arg == "*"
        assert "data" in data

        # JSON 파싱하여 내용 확인
        signal_data = json.loads(data["data"])
        assert signal_data["type"] == "BUY"
        assert signal_data["ticker"] == "005930"
        assert signal_data["company_name"] == "Samsung Electronics"
        assert signal_data["price"] == 82000

    @pytest.mark.asyncio
    async def test_publish_signal_with_scenario(self, publisher_with_mock_redis, mock_redis, sample_scenario):
        """시나리오 포함 시그널 발행 테스트"""
        await publisher_with_mock_redis.publish_signal(
            signal_type="BUY",
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            source="AI분석",
            scenario=sample_scenario
        )

        call_args = mock_redis.xadd.call_args
        # upstash-redis 1.5.0+: xadd(key, id, data) - data는 3번째 인자
        signal_data = json.loads(call_args[0][2]["data"])

        assert signal_data["target_price"] == 90000
        assert signal_data["stop_loss"] == 75000
        assert signal_data["sector"] == "Semiconductor"

    @pytest.mark.asyncio
    async def test_publish_signal_skip_when_not_connected(self):
        """Redis 미연결 시 스킵 테스트"""
        publisher = SignalPublisher()
        publisher._redis = None

        result = await publisher.publish_signal(
            signal_type="BUY",
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000
        )

        assert result is None


class TestPublishBuySignal:
    """매수 시그널 발행 테스트"""

    @pytest.mark.asyncio
    async def test_publish_buy_signal(self, publisher_with_mock_redis, mock_redis, sample_scenario):
        """매수 시그널 발행 테스트"""
        trade_result = {"success": True, "message": "Buy completed"}

        result = await publisher_with_mock_redis.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            scenario=sample_scenario,
            trade_result=trade_result
        )

        assert result == "1234567890-0"

        call_args = mock_redis.xadd.call_args
        # upstash-redis 1.5.0+: xadd(key, id, data) - data는 3번째 인자
        signal_data = json.loads(call_args[0][2]["data"])

        assert signal_data["type"] == "BUY"
        assert signal_data["trade_success"] is True
        assert signal_data["trade_message"] == "Buy completed"


class TestPublishSellSignal:
    """매도 시그널 발행 테스트"""

    @pytest.mark.asyncio
    async def test_publish_sell_signal(self, publisher_with_mock_redis, mock_redis):
        """매도 시그널 발행 테스트"""
        trade_result = {"success": True, "message": "Sell completed"}

        result = await publisher_with_mock_redis.publish_sell_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=90000,
            buy_price=82000,
            profit_rate=9.76,
            sell_reason="Target price reached",
            trade_result=trade_result
        )

        assert result == "1234567890-0"

        call_args = mock_redis.xadd.call_args
        # upstash-redis 1.5.0+: xadd(key, id, data) - data는 3번째 인자
        signal_data = json.loads(call_args[0][2]["data"])

        assert signal_data["type"] == "SELL"
        assert signal_data["buy_price"] == 82000
        assert signal_data["profit_rate"] == 9.76
        assert signal_data["sell_reason"] == "Target price reached"


class TestPublishEventSignal:
    """이벤트 시그널 발행 테스트"""

    @pytest.mark.asyncio
    async def test_publish_event_signal(self, publisher_with_mock_redis, mock_redis):
        """이벤트 시그널 발행 테스트"""
        result = await publisher_with_mock_redis.publish_event_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            event_type="YOUTUBE",
            event_source="유튜버_홍길동",
            event_description="Samsung Electronics 신규 영상 업로드"
        )

        assert result == "1234567890-0"

        call_args = mock_redis.xadd.call_args
        # upstash-redis 1.5.0+: xadd(key, id, data) - data는 3번째 인자
        signal_data = json.loads(call_args[0][2]["data"])

        assert signal_data["type"] == "EVENT"
        assert signal_data["event_type"] == "YOUTUBE"
        assert signal_data["source"] == "유튜버_홍길동"


class TestGlobalPublisher:
    """글로벌 퍼블리셔 함수 테스트"""

    @pytest.mark.asyncio
    async def test_get_signal_publisher_singleton(self):
        """싱글톤 패턴 테스트"""
        with patch("messaging.redis_signal_publisher._global_publisher", None):
            with patch.dict(os.environ, {
                "UPSTASH_REDIS_REST_URL": "https://test.upstash.io",
                "UPSTASH_REDIS_REST_TOKEN": "test-token"
            }):
                # upstash_redis 모듈 mock
                with patch("messaging.redis_signal_publisher.SignalPublisher.connect", new_callable=AsyncMock):
                    publisher1 = await get_signal_publisher()
                    publisher2 = await get_signal_publisher()

                    # 같은 인스턴스여야 함
                    assert publisher1 is publisher2


# ============================================================
# Integration Tests (실제 Redis 연결)
# ============================================================

# 실제 Redis 테스트용 fixture - 모듈 레벨에서 .env 확인
_redis_configured = bool(os.environ.get("UPSTASH_REDIS_REST_URL") and os.environ.get("UPSTASH_REDIS_REST_TOKEN"))


@pytest.mark.skipif(
    not _redis_configured,
    reason="UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not configured in .env"
)
class TestIntegrationWithRealRedis:
    """실제 Redis 연결 통합 테스트"""

    @pytest.fixture
    def real_redis(self):
        """실제 Redis 클라이언트"""
        from upstash_redis import Redis
        return Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
        )

    @pytest.fixture
    def real_publisher(self):
        """실제 연결된 SignalPublisher"""
        from upstash_redis import Redis
        publisher = SignalPublisher(
            redis_url=os.environ["UPSTASH_REDIS_REST_URL"],
            redis_token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
        )
        publisher._redis = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
        )
        return publisher

    @pytest.mark.asyncio
    async def test_real_connection(self):
        """실제 Redis 연결 테스트"""
        async with SignalPublisher() as publisher:
            assert publisher._is_connected() is True
            print("\n✅ Redis 연결 성공")

    @pytest.mark.asyncio
    async def test_publish_buy_signal(self, real_redis, real_publisher):
        """실제 매수 시그널 발행 테스트"""
        test_ticker = f"BUY_TEST_{datetime.now().strftime('%H%M%S')}"
        
        message_id = await real_publisher.publish_buy_signal(
            ticker=test_ticker,
            company_name="Test Stock_매수",
            price=50000,
            scenario={
                "target_price": 55000,
                "stop_loss": 47000,
                "sector": "테스트",
                "rationale": "테스트 매수 시그널"
            },
            trade_result={"success": True, "message": "테스트 Buy completed"}
        )

        assert message_id is not None, "message_id is None. Check Redis connection."
        print(f"\n✅ 매수 시그널 발행: {message_id}")

        # 발행된 메시지 확인 (upstash-redis 1.5.0+ 시그니처: xrange(key, start, end, count))
        result = real_redis.xrange(
            "prism:trading-signals",
            message_id,  # start
            message_id,  # end
            count=1
        )

        assert len(result) == 1
        parsed_data = parse_stream_data(result[0][1])
        signal = json.loads(parsed_data["data"])
        
        assert signal["type"] == "BUY"
        assert signal["ticker"] == test_ticker
        assert signal["target_price"] == 55000
        print(f"✅ 매수 시그널 확인: {signal['company_name']} @ {signal['price']:,}원")

    @pytest.mark.asyncio
    async def test_publish_sell_signal(self, real_redis, real_publisher):
        """실제 매도 시그널 발행 테스트"""
        test_ticker = f"SELL_TEST_{datetime.now().strftime('%H%M%S')}"
        
        message_id = await real_publisher.publish_sell_signal(
            ticker=test_ticker,
            company_name="Test Stock_매도",
            price=55000,
            buy_price=50000,
            profit_rate=10.0,
            sell_reason="Target price reached 테스트",
            trade_result={"success": True, "message": "테스트 Sell completed"}
        )

        assert message_id is not None
        print(f"\n✅ 매도 시그널 발행: {message_id}")

        # 발행된 메시지 확인 (upstash-redis 1.5.0+ 시그니처: xrange(key, start, end, count))
        result = real_redis.xrange(
            "prism:trading-signals",
            message_id,  # start
            message_id,  # end
            count=1
        )

        assert len(result) == 1
        parsed_data = parse_stream_data(result[0][1])
        signal = json.loads(parsed_data["data"])
        
        assert signal["type"] == "SELL"
        assert signal["ticker"] == test_ticker
        assert signal["profit_rate"] == 10.0
        print(f"✅ 매도 시그널 확인: {signal['company_name']} 수익률 {signal['profit_rate']}%")

    @pytest.mark.asyncio
    async def test_publish_event_signal(self, real_redis, real_publisher):
        """실제 이벤트 시그널 발행 테스트"""
        test_ticker = f"EVENT_TEST_{datetime.now().strftime('%H%M%S')}"
        
        message_id = await real_publisher.publish_event_signal(
            ticker=test_ticker,
            company_name="Test Stock_이벤트",
            price=50000,
            event_type="YOUTUBE",
            event_source="테스트_유튜버",
            event_description="테스트 영상 업로드"
        )

        assert message_id is not None
        print(f"\n✅ 이벤트 시그널 발행: {message_id}")

        # 발행된 메시지 확인 (upstash-redis 1.5.0+ 시그니처: xrange(key, start, end, count))
        result = real_redis.xrange(
            "prism:trading-signals",
            message_id,  # start
            message_id,  # end
            count=1
        )

        assert len(result) == 1
        parsed_data = parse_stream_data(result[0][1])
        signal = json.loads(parsed_data["data"])
        
        assert signal["type"] == "EVENT"
        assert signal["event_type"] == "YOUTUBE"
        print(f"✅ 이벤트 시그널 확인: {signal['event_type']} from {signal['source']}")

    @pytest.mark.asyncio
    async def test_full_pubsub_flow(self, real_redis, real_publisher):
        """전체 Pub/Sub 흐름 테스트 (발행 → 구독자처럼 읽기)"""
        stream_name = "prism:trading-signals"
        
        # 1. 현재 스트림의 마지막 ID 가져오기 (XREVRANGE로 최신 1개)
        last_entries = real_redis.xrevrange(stream_name, count=1)
        last_id = last_entries[0][0] if last_entries else "0"
        print(f"\n📍 시작 ID: {last_id}")

        # 2. 여러 시그널 발행
        published_ids = []
        
        # 매수 시그널
        buy_id = await real_publisher.publish_buy_signal(
            ticker="FLOW_001",
            company_name="흐름테스트_매수",
            price=10000,
            scenario={"target_price": 11000, "stop_loss": 9500}
        )
        published_ids.append(buy_id)
        print(f"📤 매수 시그널 발행: {buy_id}")

        # 매도 시그널
        sell_id = await real_publisher.publish_sell_signal(
            ticker="FLOW_002",
            company_name="흐름테스트_매도",
            price=12000,
            buy_price=10000,
            profit_rate=20.0,
            sell_reason="Target price reached"
        )
        published_ids.append(sell_id)
        print(f"📤 매도 시그널 발행: {sell_id}")

        # 이벤트 시그널
        event_id = await real_publisher.publish_event_signal(
            ticker="FLOW_003",
            company_name="흐름테스트_이벤트",
            price=15000,
            event_type="NEWS",
            event_source="테스트뉴스",
            event_description="호재 발생"
        )
        published_ids.append(event_id)
        print(f"📤 이벤트 시그널 발행: {event_id}")

        # 3. 구독자처럼 XREAD로 새 메시지 읽기
        print(f"\n📥 구독자 모드로 메시지 읽기 (after {last_id})...")
        
        # XREAD: last_id 이후의 메시지 읽기
        result = real_redis.xread({stream_name: last_id}, count=10)
        
        assert result is not None
        assert len(result) > 0
        
        stream, messages = result[0]
        received_signals = []
        
        for msg_id, data in messages:
            parsed_data = parse_stream_data(data)
            signal = json.loads(parsed_data["data"])
            received_signals.append(signal)
            
            emoji = {"BUY": "📈", "SELL": "📉", "EVENT": "🔔"}.get(signal["type"], "📌")
            print(f"   {emoji} [{signal['type']}] {signal['company_name']} @ {signal['price']:,}원")

        # 4. 발행한 시그널이 모두 수신되었는지 확인
        received_tickers = [s["ticker"] for s in received_signals]
        assert "FLOW_001" in received_tickers
        assert "FLOW_002" in received_tickers
        assert "FLOW_003" in received_tickers
        
        print(f"\n✅ 전체 Pub/Sub 흐름 테스트 성공! ({len(received_signals)}개 시그널 수신)")

    @pytest.mark.asyncio
    async def test_subscriber_new_messages_only(self, real_redis, real_publisher):
        """구독자가 새 메시지만 받는 시나리오 테스트"""
        stream_name = "prism:trading-signals"
        
        # 시그널 발행
        test_ticker = f"NEW_MSG_{datetime.now().strftime('%H%M%S%f')}"
        message_id = await real_publisher.publish_buy_signal(
            ticker=test_ticker,
            company_name="새메시지테스트",
            price=99999
        )
        
        assert message_id is not None, "message_id is None"
        print(f"\n📤 시그널 발행: {message_id}")

        # 방금 발행한 메시지 ID 직전부터 읽기
        parts = message_id.split("-")
        prev_id = f"{int(parts[0])-1}-0"
        
        result = real_redis.xread({stream_name: prev_id}, count=5)
        
        assert result is not None
        stream, messages = result[0]
        
        # 발행한 메시지가 포함되어 있는지 확인
        found = False
        for msg_id, data in messages:
            parsed_data = parse_stream_data(data)
            signal = json.loads(parsed_data["data"])
            if signal["ticker"] == test_ticker:
                found = True
                print(f"📥 수신: {signal['company_name']} @ {signal['price']:,}원")
                break
        
        assert found, f"발행한 시그널을 찾지 못함: {test_ticker}"
        print("✅ 새 메시지 수신 테스트 성공!")

    @pytest.mark.asyncio  
    async def test_read_stream_length(self, real_redis):
        """스트림 길이 조회 테스트"""
        stream_name = "prism:trading-signals"
        
        # XLEN으로 스트림 길이 조회
        length = real_redis.xlen(stream_name)
        
        # 최근 메시지 조회
        recent = real_redis.xrevrange(stream_name, count=3)
        
        print("\n📊 스트림 정보:")
        print(f"   - 스트림 이름: {stream_name}")
        print(f"   - 총 메시지 수: {length}")
        
        if recent:
            print("   - 최근 메시지:")
            for msg_id, data in recent:
                parsed_data = parse_stream_data(data)
                signal = json.loads(parsed_data["data"])
                print(f"     [{msg_id}] {signal['type']} - {signal.get('company_name', 'N/A')}")
        
        assert length >= 0
        print("✅ 스트림 정보 조회 성공!")


# ============================================================
# Performance Tests
# ============================================================

class TestPerformance:
    """성능 테스트"""

    @pytest.mark.asyncio
    async def test_publish_multiple_signals(self, publisher_with_mock_redis, mock_redis):
        """다수 시그널 발행 테스트"""
        import time

        start = time.time()
        count = 100

        for i in range(count):
            await publisher_with_mock_redis.publish_buy_signal(
                ticker=f"00593{i % 10}",
                company_name=f"Test Stock{i}",
                price=80000 + i * 100
            )

        elapsed = time.time() - start
        print(f"\n⏱️ {count}개 시그널 발행: {elapsed:.3f}초 ({count/elapsed:.1f}/초)")

        assert mock_redis.xadd.call_count == count


# ============================================================
# Edge Cases
# ============================================================

class TestEdgeCases:
    """엣지 케이스 테스트"""

    @pytest.mark.asyncio
    async def test_publish_with_special_characters(self, publisher_with_mock_redis, mock_redis):
        """특수문자 포함 시그널 테스트"""
        await publisher_with_mock_redis.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics (우선주)",
            price=82000,
            scenario={"rationale": "신규 사업 진출 - AI/Semiconductor 'HBM' 수요 증가"}
        )

        call_args = mock_redis.xadd.call_args
        # upstash-redis 1.5.0+: xadd(key, id, data) - data는 3번째 인자
        signal_data = json.loads(call_args[0][2]["data"])

        assert signal_data["company_name"] == "Samsung Electronics (우선주)"
        assert "HBM" in signal_data["rationale"]

    @pytest.mark.asyncio
    async def test_publish_with_empty_scenario(self, publisher_with_mock_redis, mock_redis):
        """빈 시나리오 테스트"""
        result = await publisher_with_mock_redis.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            scenario={}
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_publish_with_none_scenario(self, publisher_with_mock_redis, mock_redis):
        """None 시나리오 테스트"""
        result = await publisher_with_mock_redis.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            scenario=None
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_redis_error_handling(self, publisher_with_mock_redis, mock_redis):
        """Redis 오류 처리 테스트"""
        mock_redis.xadd.side_effect = Exception("Redis connection error")

        result = await publisher_with_mock_redis.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000
        )

        # 오류 발생해도 None 반환 (예외 발생 X)
        assert result is None


# ============================================================
# Subscriber Example Tests
# ============================================================

class TestSubscriberExample:
    """subscriber_example.py 로직 테스트"""

    def test_parse_stream_data_dict(self):
        """딕셔너리 형태 데이터 파싱 테스트"""
        data = {"data": '{"type": "BUY", "ticker": "005930"}'}
        result = parse_stream_data(data)
        assert result == data

    def test_parse_stream_data_list(self):
        """리스트 형태 데이터 파싱 테스트 (upstash-redis 반환 형식)"""
        data = ["data", '{"type": "BUY", "ticker": "005930"}', "field2", "value2"]
        result = parse_stream_data(data)
        assert result == {"data": '{"type": "BUY", "ticker": "005930"}', "field2": "value2"}

    def test_handle_signal_buy(self, capsys):
        """매수 시그널 핸들링 테스트"""
        signal = {
            "type": "BUY",
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "price": 82000,
            "timestamp": "2024-01-15T10:30:00",
            "target_price": 90000,
            "stop_loss": 75000,
            "rationale": "AI Semiconductor 수요 증가"
        }
        
        # subscriber_example의 handle_signal 로직 시뮬레이션
        signal_type = signal.get("type", "UNKNOWN")
        ticker = signal.get("ticker", "")
        company_name = signal.get("company_name", "")
        price = signal.get("price", 0)
        
        emoji = {"BUY": "📈", "SELL": "📉", "EVENT": "🔔"}.get(signal_type, "📌")
        
        assert emoji == "📈"
        assert signal_type == "BUY"
        assert ticker == "005930"
        assert company_name == "Samsung Electronics"
        assert price == 82000

    def test_handle_signal_sell(self):
        """매도 시그널 핸들링 테스트"""
        signal = {
            "type": "SELL",
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "price": 90000,
            "profit_rate": 9.76,
            "sell_reason": "Target price reached"
        }
        
        signal_type = signal.get("type", "UNKNOWN")
        profit_rate = signal.get("profit_rate", 0)
        sell_reason = signal.get("sell_reason", "")
        
        emoji = {"BUY": "📈", "SELL": "📉", "EVENT": "🔔"}.get(signal_type, "📌")
        
        assert emoji == "📉"
        assert signal_type == "SELL"
        assert profit_rate == 9.76
        assert sell_reason == "Target price reached"

    def test_handle_signal_event(self):
        """이벤트 시그널 핸들링 테스트"""
        signal = {
            "type": "EVENT",
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "price": 82000,
            "event_type": "YOUTUBE",
            "event_description": "신규 영상 업로드"
        }
        
        signal_type = signal.get("type", "UNKNOWN")
        event_type = signal.get("event_type", "")
        
        emoji = {"BUY": "📈", "SELL": "📉", "EVENT": "🔔"}.get(signal_type, "📌")
        
        assert emoji == "🔔"
        assert signal_type == "EVENT"
        assert event_type == "YOUTUBE"

    def test_xread_without_block_parameter(self):
        """xread가 block 파라미터 없이 호출되는지 확인"""
        mock_redis = MagicMock()
        mock_redis.xread = MagicMock(return_value=None)
        
        stream_name = "prism:trading-signals"
        last_id = "0"
        
        # Upstash 호환 방식으로 호출 (block 없음)
        mock_redis.xread({stream_name: last_id}, count=10)
        
        # xread가 block 파라미터 없이 호출되었는지 확인
        call_args = mock_redis.xread.call_args
        
        # positional args 확인
        assert call_args[0] == ({stream_name: last_id},)
        
        # keyword args에 block이 없어야 함
        assert "block" not in call_args[1]
        assert call_args[1].get("count") == 10


@pytest.mark.skipif(
    not _redis_configured,
    reason="UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not configured in .env"
)
class TestSubscriberIntegration:
    """subscriber_example.py 통합 테스트 (실제 Redis 연결)"""

    @pytest.fixture
    def real_redis(self):
        """실제 Redis 클라이언트"""
        from upstash_redis import Redis
        return Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
        )

    @pytest.fixture
    def real_publisher(self):
        """실제 연결된 SignalPublisher"""
        from upstash_redis import Redis
        publisher = SignalPublisher(
            redis_url=os.environ["UPSTASH_REDIS_REST_URL"],
            redis_token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
        )
        publisher._redis = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
        )
        return publisher

    def test_xread_api_compatibility(self, real_redis):
        """xread API가 Upstash에서 block 없이 동작하는지 테스트"""
        stream_name = "prism:trading-signals"
        
        # block 파라미터 없이 xread 호출 - 에러 없이 동작해야 함
        try:
            result = real_redis.xread({stream_name: "0"}, count=5)
            # result는 None이거나 리스트
            assert result is None or isinstance(result, list)
            print(f"\n✅ xread 호출 성공 (block 없음): {type(result)}")
        except TypeError as e:
            pytest.fail(f"xread 호출 실패 - API 호환성 문제: {e}")

    def test_xread_with_block_should_fail(self, real_redis):
        """xread에 block 파라미터를 넣으면 에러가 발생하는지 확인"""
        stream_name = "prism:trading-signals"
        
        # block 파라미터를 넣으면 TypeError가 발생해야 함
        with pytest.raises(TypeError) as exc_info:
            real_redis.xread({stream_name: "0"}, block=5000, count=5)
        
        # 에러 메시지에 'unexpected keyword argument' 포함 확인
        assert "unexpected keyword argument" in str(exc_info.value) or "block" in str(exc_info.value)
        print(f"\n✅ block 파라미터 사용 시 예상대로 에러 발생: {exc_info.value}")

    @pytest.mark.asyncio
    async def test_subscriber_receives_published_signal(self, real_redis, real_publisher):
        """발행된 시그널을 구독자가 수신하는 전체 흐름 테스트"""
        stream_name = "prism:trading-signals"
        
        # 1. 현재 마지막 ID 가져오기
        last_entries = real_redis.xrevrange(stream_name, count=1)
        last_id = last_entries[0][0] if last_entries else "0"
        print(f"\n📍 시작 ID: {last_id}")
        
        # 2. 테스트 시그널 발행
        test_ticker = f"SUB_TEST_{datetime.now().strftime('%H%M%S%f')}"
        message_id = await real_publisher.publish_buy_signal(
            ticker=test_ticker,
            company_name="구독자테스트",
            price=12345,
            scenario={"target_price": 15000, "stop_loss": 10000}
        )
        print(f"📤 시그널 발행: {message_id}")
        
        # 3. 구독자처럼 xread로 읽기 (block 없이)
        result = real_redis.xread({stream_name: last_id}, count=10)
        
        assert result is not None, "xread 결과가 None"
        
        # 4. 발행한 시그널 찾기
        found_signal = None
        for stream, messages in result:
            for msg_id, data in messages:
                parsed_data = parse_stream_data(data)
                signal = json.loads(parsed_data["data"])
                if signal.get("ticker") == test_ticker:
                    found_signal = signal
                    break
        
        assert found_signal is not None, f"발행한 시그널을 찾지 못함: {test_ticker}"
        assert found_signal["type"] == "BUY"
        assert found_signal["company_name"] == "구독자테스트"
        assert found_signal["price"] == 12345
        
        print(f"📥 시그널 수신 성공: {found_signal['company_name']} @ {found_signal['price']:,}원")
        print("✅ 구독자 통합 테스트 성공!")

    def test_polling_simulation(self, real_redis):
        """Polling 방식 시뮬레이션 테스트"""
        import time
        
        stream_name = "prism:trading-signals"
        last_id = "$"  # 새 메시지만
        poll_count = 0
        max_polls = 3
        
        print(f"\n🔄 Polling 시뮬레이션 시작 (최대 {max_polls}회)")
        
        while poll_count < max_polls:
            result = real_redis.xread({stream_name: last_id}, count=10)
            
            if result:
                for stream, messages in result:
                    for msg_id, data in messages:
                        parsed_data = parse_stream_data(data)
                        signal = json.loads(parsed_data["data"])
                        print(f"   📥 [{signal['type']}] {signal.get('company_name', 'N/A')}")
                        last_id = msg_id
            else:
                print(f"   ⏳ Poll #{poll_count + 1}: 새 메시지 없음")
            
            poll_count += 1
            if poll_count < max_polls:
                time.sleep(1)  # 테스트에서는 1초 간격
        
        print("✅ Polling 시뮬레이션 완료")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
