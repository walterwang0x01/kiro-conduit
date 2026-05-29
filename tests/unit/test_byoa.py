"""单元测试：BYOA 模型路由（M1.1 step 3）。

不调真 Kiro。验证 AcpClientConfig.model 字段以及 Implementor /
KiroSemanticReviewer 的 model 参数能正确穿透到 AcpClientConfig。
"""

from __future__ import annotations

from kiro_conduit.acp import AcpClientConfig
from kiro_conduit.roles.implementor import Implementor
from kiro_conduit.semantic import KiroSemanticReviewer


class TestAcpClientConfigModel:
    def test_default_none(self) -> None:
        cfg = AcpClientConfig()
        assert cfg.model is None

    def test_explicit_model(self) -> None:
        cfg = AcpClientConfig(model="claude-opus-4.7")
        assert cfg.model == "claude-opus-4.7"


class TestImplementorModel:
    def test_default_none(self) -> None:
        impl = Implementor()
        assert impl._model is None

    def test_explicit_model(self) -> None:
        impl = Implementor(model="claude-sonnet-4.7")
        assert impl._model == "claude-sonnet-4.7"


class TestKiroSemanticReviewerModel:
    def test_default_none(self) -> None:
        rv = KiroSemanticReviewer()
        assert rv._model is None

    def test_explicit_model(self) -> None:
        rv = KiroSemanticReviewer(model="claude-haiku-4")
        assert rv._model == "claude-haiku-4"
