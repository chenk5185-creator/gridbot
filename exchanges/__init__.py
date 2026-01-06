"""
交易所适配器模块
===============

本模块提供统一的交易所接口，支持多个交易平台：
- StandX: 去中心化永续合约交易所（主要支持）
- Binance: 币安（待开发）
- Bybit: Bybit（待开发）

使用方法：
    from exchanges import create_adapter

    adapter = create_adapter('standx', config)
    await adapter.initialize()
"""

from .base import (
    ExchangeAdapter,
    Balance,
    Position,
    Order,
    Ticker,
    OrderSide,
    OrderType,
    OrderStatus
)
from .standx import StandXAdapter

# 交易所适配器注册表
# 添加新交易所时，在此注册
EXCHANGE_ADAPTERS = {
    'standx': StandXAdapter,
    # 'binance': BinanceAdapter,  # 待开发
    # 'bybit': BybitAdapter,      # 待开发
}


def create_adapter(exchange_name: str, config: dict) -> ExchangeAdapter:
    """
    创建交易所适配器实例

    Args:
        exchange_name: 交易所名称 (standx, binance, bybit)
        config: 配置字典，包含 API 密钥等信息

    Returns:
        ExchangeAdapter: 交易所适配器实例

    Raises:
        ValueError: 不支持的交易所

    Example:
        >>> adapter = create_adapter('standx', {'jwt_token': 'xxx'})
        >>> await adapter.initialize()
        >>> ticker = await adapter.get_ticker('BTC-USD')
    """
    adapter_class = EXCHANGE_ADAPTERS.get(exchange_name.lower())

    if not adapter_class:
        supported = ', '.join(EXCHANGE_ADAPTERS.keys())
        raise ValueError(f"不支持的交易所: {exchange_name}，支持的交易所: {supported}")

    return adapter_class(config)


__all__ = [
    # 基类和数据类型
    'ExchangeAdapter',
    'Balance',
    'Position',
    'Order',
    'Ticker',
    'OrderSide',
    'OrderType',
    'OrderStatus',
    # 适配器
    'StandXAdapter',
    # 工厂函数
    'create_adapter',
    'EXCHANGE_ADAPTERS',
]
