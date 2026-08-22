"""统一 LLM 输出预算配置测试。"""

from app.config import Settings


class TestLLMMaxTokensSetting:
    def test_default_is_32768(self) -> None:
        assert Settings.model_fields["llm_max_tokens"].default == 32_768

    def test_clamps_below_minimum(self) -> None:
        assert Settings(llm_max_tokens=100).llm_max_tokens == 1_024

    def test_clamps_above_maximum(self) -> None:
        assert Settings(llm_max_tokens=999_999).llm_max_tokens == 131_072

    def test_blank_or_invalid_value_uses_default(self) -> None:
        assert Settings(llm_max_tokens="").llm_max_tokens == 32_768
        assert Settings(llm_max_tokens="invalid").llm_max_tokens == 32_768
