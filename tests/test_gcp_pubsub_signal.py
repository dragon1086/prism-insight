#!/usr/bin/env python3
"""
GCP Pub/Sub Signal Publisher 테스트

테스트 실행:
    # .env 파일에 설정 필요
    # GCP_PROJECT_ID=your-project-id
    # GCP_PUBSUB_TOPIC_ID=prism-trading-signals
    # GCP_CREDENTIALS_PATH=/path/to/service-account-key.json

    # 전체 테스트
    pytest tests/test_gcp_pubsub_signal.py -v

    # 특정 테스트만
    pytest tests/test_gcp_pubsub_signal.py::test_publish_buy_signal -v

    # 실제 GCP 연결 테스트
    pytest tests/test_gcp_pubsub_signal.py::TestIntegrationWithRealPubSub -v
"""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from pathlib import Path

# 프로젝트 루트 경로 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from messaging.gcp_pubsub_signal_publisher import (
    SignalPublisher,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_publisher_client():
    """Mock GCP Publisher 객체"""
    mock = MagicMock()
    mock_future = MagicMock()
    mock_future.result = MagicMock(return_value="test-message-id-123")
    mock.publish = MagicMock(return_value=mock_future)
    mock.topic_path = MagicMock(return_value="projects/test-project/topics/test-topic")
    return mock


@pytest.fixture
def publisher_with_mock(mock_publisher_client):
    """Mock Publisher가 주입된 SignalPublisher"""
    publisher = SignalPublisher(
        project_id="test-project",
        topic_id="test-topic"
    )
    publisher._publisher = mock_publisher_client
    publisher._topic_path = "projects/test-project/topics/test-topic"
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
            "GCP_PROJECT_ID": "test-project",
            "GCP_PUBSUB_TOPIC_ID": "test-topic"
        }):
            publisher = SignalPublisher()
            assert publisher.project_id == "test-project"
            assert publisher.topic_id == "test-topic"

    def test_init_with_params(self):
        """파라미터로 설정 전달 테스트"""
        publisher = SignalPublisher(
            project_id="custom-project",
            topic_id="custom-topic"
        )
        assert publisher.project_id == "custom-project"
        assert publisher.topic_id == "custom-topic"

    def test_is_connected_false_when_no_publisher(self):
        """Publisher 미연결 상태 확인"""
        publisher = SignalPublisher()
        assert publisher._is_connected() is False

    def test_is_connected_true_when_publisher_exists(self, publisher_with_mock):
        """Publisher 연결 상태 확인"""
        assert publisher_with_mock._is_connected() is True


class TestPublishSignal:
    """시그널 발행 테스트"""

    @pytest.mark.asyncio
    async def test_publish_signal_success(self, publisher_with_mock, mock_publisher_client):
        """시그널 발행 성공 테스트"""
        result = await publisher_with_mock.publish_signal(
            signal_type="BUY",
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            source="AI분석"
        )

        assert result == "test-message-id-123"
        mock_publisher_client.publish.assert_called_once()

        # publish 호출 인자 확인
        call_args = mock_publisher_client.publish.call_args
        topic_path = call_args[0][0]
        message_bytes = call_args[0][1]

        assert topic_path == "projects/test-project/topics/test-topic"

        # JSON 파싱하여 내용 확인
        signal_data = json.loads(message_bytes.decode("utf-8"))
        assert signal_data["type"] == "BUY"
        assert signal_data["ticker"] == "005930"
        assert signal_data["company_name"] == "Samsung Electronics"
        assert signal_data["price"] == 82000

    @pytest.mark.asyncio
    async def test_publish_signal_with_scenario(self, publisher_with_mock, mock_publisher_client, sample_scenario):
        """시나리오 포함 시그널 발행 테스트"""
        await publisher_with_mock.publish_signal(
            signal_type="BUY",
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            source="AI분석",
            scenario=sample_scenario
        )

        call_args = mock_publisher_client.publish.call_args
        message_bytes = call_args[0][1]
        signal_data = json.loads(message_bytes.decode("utf-8"))

        assert signal_data["target_price"] == 90000
        assert signal_data["stop_loss"] == 75000
        assert signal_data["sector"] == "Semiconductor"

    @pytest.mark.asyncio
    async def test_publish_signal_skip_when_not_connected(self):
        """Publisher 미연결 시 스킵 테스트"""
        publisher = SignalPublisher()
        publisher._publisher = None

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
    async def test_publish_buy_signal(self, publisher_with_mock, mock_publisher_client, sample_scenario):
        """매수 시그널 발행 테스트"""
        trade_result = {"success": True, "message": "Buy completed"}

        result = await publisher_with_mock.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            scenario=sample_scenario,
            trade_result=trade_result
        )

        assert result == "test-message-id-123"

        call_args = mock_publisher_client.publish.call_args
        message_bytes = call_args[0][1]
        signal_data = json.loads(message_bytes.decode("utf-8"))

        assert signal_data["type"] == "BUY"
        assert signal_data["trade_success"] is True
        assert signal_data["trade_message"] == "Buy completed"


class TestPublishSellSignal:
    """매도 시그널 발행 테스트"""

    @pytest.mark.asyncio
    async def test_publish_sell_signal(self, publisher_with_mock, mock_publisher_client):
        """매도 시그널 발행 테스트"""
        trade_result = {"success": True, "message": "Sell completed"}

        result = await publisher_with_mock.publish_sell_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=90000,
            buy_price=82000,
            profit_rate=9.76,
            sell_reason="Target price reached",
            trade_result=trade_result
        )

        assert result == "test-message-id-123"

        call_args = mock_publisher_client.publish.call_args
        message_bytes = call_args[0][1]
        signal_data = json.loads(message_bytes.decode("utf-8"))

        assert signal_data["type"] == "SELL"
        assert signal_data["buy_price"] == 82000
        assert signal_data["profit_rate"] == 9.76
        assert signal_data["sell_reason"] == "Target price reached"


class TestPublishEventSignal:
    """이벤트 시그널 발행 테스트"""

    @pytest.mark.asyncio
    async def test_publish_event_signal(self, publisher_with_mock, mock_publisher_client):
        """이벤트 시그널 발행 테스트"""
        result = await publisher_with_mock.publish_event_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            event_type="YOUTUBE",
            event_source="유튜버_홍길동",
            event_description="Samsung Electronics 신규 영상 업로드"
        )

        assert result == "test-message-id-123"

        call_args = mock_publisher_client.publish.call_args
        message_bytes = call_args[0][1]
        signal_data = json.loads(message_bytes.decode("utf-8"))

        assert signal_data["type"] == "EVENT"
        assert signal_data["event_type"] == "YOUTUBE"
        assert signal_data["source"] == "유튜버_홍길동"


# ============================================================
# Integration Tests (실제 GCP 연결)
# ============================================================

_gcp_configured = bool(
    os.environ.get("GCP_PROJECT_ID") and 
    os.environ.get("GCP_PUBSUB_TOPIC_ID")
)


@pytest.mark.skipif(
    not _gcp_configured,
    reason="GCP_PROJECT_ID or GCP_PUBSUB_TOPIC_ID not configured in .env"
)
class TestIntegrationWithRealPubSub:
    """실제 GCP Pub/Sub 연결 통합 테스트"""

    @pytest.mark.asyncio
    async def test_real_connection(self):
        """실제 GCP Pub/Sub 연결 테스트"""
        async with SignalPublisher() as publisher:
            assert publisher._is_connected() is True
            print("\n✅ GCP Pub/Sub 연결 성공")

    @pytest.mark.asyncio
    async def test_publish_buy_signal(self):
        """실제 매수 시그널 발행 테스트"""
        test_ticker = f"BUY_TEST_{datetime.now().strftime('%H%M%S')}"
        
        async with SignalPublisher() as publisher:
            message_id = await publisher.publish_buy_signal(
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

            assert message_id is not None
            print(f"\n✅ 매수 시그널 발행: {message_id}")

    @pytest.mark.asyncio
    async def test_publish_sell_signal(self):
        """실제 매도 시그널 발행 테스트"""
        test_ticker = f"SELL_TEST_{datetime.now().strftime('%H%M%S')}"
        
        async with SignalPublisher() as publisher:
            message_id = await publisher.publish_sell_signal(
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

    @pytest.mark.asyncio
    async def test_publish_event_signal(self):
        """실제 이벤트 시그널 발행 테스트"""
        test_ticker = f"EVENT_TEST_{datetime.now().strftime('%H%M%S')}"
        
        async with SignalPublisher() as publisher:
            message_id = await publisher.publish_event_signal(
                ticker=test_ticker,
                company_name="Test Stock_이벤트",
                price=50000,
                event_type="YOUTUBE",
                event_source="테스트_유튜버",
                event_description="테스트 영상 업로드"
            )

            assert message_id is not None
            print(f"\n✅ 이벤트 시그널 발행: {message_id}")

    @pytest.mark.asyncio
    async def test_multiple_signals(self):
        """다수 시그널 발행 테스트"""
        async with SignalPublisher() as publisher:
            message_ids = []
            
            # 매수
            buy_id = await publisher.publish_buy_signal(
                ticker="MULTI_001",
                company_name="다수테스트_매수",
                price=10000,
                scenario={"target_price": 11000}
            )
            message_ids.append(buy_id)
            
            # 매도
            sell_id = await publisher.publish_sell_signal(
                ticker="MULTI_002",
                company_name="다수테스트_매도",
                price=12000,
                buy_price=10000,
                profit_rate=20.0,
                sell_reason="Target price reached"
            )
            message_ids.append(sell_id)
            
            # 이벤트
            event_id = await publisher.publish_event_signal(
                ticker="MULTI_003",
                company_name="다수테스트_이벤트",
                price=15000,
                event_type="NEWS",
                event_source="테스트뉴스",
                event_description="호재 발생"
            )
            message_ids.append(event_id)
            
            assert all(msg_id is not None for msg_id in message_ids)
            print(f"\n✅ 다수 시그널 발행 성공: {len(message_ids)}개")


# ============================================================
# Edge Cases
# ============================================================

class TestEdgeCases:
    """엣지 케이스 테스트"""

    @pytest.mark.asyncio
    async def test_publish_with_special_characters(self, publisher_with_mock, mock_publisher_client):
        """특수문자 포함 시그널 테스트"""
        await publisher_with_mock.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics (우선주)",
            price=82000,
            scenario={"rationale": "신규 사업 진출 - AI/Semiconductor 'HBM' 수요 증가"}
        )

        call_args = mock_publisher_client.publish.call_args
        message_bytes = call_args[0][1]
        signal_data = json.loads(message_bytes.decode("utf-8"))

        assert signal_data["company_name"] == "Samsung Electronics (우선주)"
        assert "HBM" in signal_data["rationale"]

    @pytest.mark.asyncio
    async def test_publish_with_empty_scenario(self, publisher_with_mock):
        """빈 시나리오 테스트"""
        result = await publisher_with_mock.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            scenario={}
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_publish_with_none_scenario(self, publisher_with_mock):
        """None 시나리오 테스트"""
        result = await publisher_with_mock.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            scenario=None
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_pubsub_error_handling(self, publisher_with_mock, mock_publisher_client):
        """GCP Pub/Sub 오류 처리 테스트"""
        mock_publisher_client.publish.side_effect = Exception("Pub/Sub error")

        result = await publisher_with_mock.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000
        )

        # 오류 발생해도 None 반환 (예외 발생 X)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
