from backend.app.config import GRADING_MODEL_ID
from backend.libs.config import GRADING_MODEL_CONFIG, ChatModelConfig


def test_deepseek_v4_pro_non_thinking_request_options():
    config = ChatModelConfig(
        provider="deepseek",
        model="deepseek-v4-pro",
        thinking=False,
        json_mode=False,
    )
    assert config.request_options() == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }


def test_qwen_max_non_thinking_request_options():
    config = ChatModelConfig(
        provider="bailian",
        model="qwen3.8-max",
        thinking=False,
        json_mode=True,
    )
    assert config.request_options() == {
        "response_format": {"type": "json_object"},
        "extra_body": {"enable_thinking": False},
    }


def test_shared_model_identity_matches_runtime_configuration():
    assert GRADING_MODEL_CONFIG.provider == "bailian"
    assert GRADING_MODEL_CONFIG.model == "qwen3.8-max"
    assert GRADING_MODEL_CONFIG.thinking is False
    assert GRADING_MODEL_CONFIG.json_mode is True
    assert GRADING_MODEL_CONFIG.model == GRADING_MODEL_ID
