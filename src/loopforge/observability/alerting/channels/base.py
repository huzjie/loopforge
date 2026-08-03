"""告警通道抽象基类。"""

from __future__ import annotations

import abc

from loopforge.core.event_types import AlertPayload

__all__ = ["AlertChannel"]


class AlertChannel(abc.ABC):
    """告警投递通道接口。

    子类实现 :meth:`deliver`，负责把 :class:`AlertPayload` 送到外部系统
    （Webhook / Slack / 飞书 / 企业微信）。投递失败必须被捕获并记录，
    不允许向上抛出打断评估主流程。
    """

    #: 通道类型名（webhook / slack / feishu / wecom）。
    kind: str = "base"

    @abc.abstractmethod
    async def deliver(self, payload: AlertPayload) -> bool:
        """投递一条告警。

        :param payload: 告警负载。
        :returns: 是否投递成功。
        """
        raise NotImplementedError
