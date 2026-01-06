"""
网格交易引擎
===========

核心网格交易逻辑，参考 ASTER/OKX 的网格策略实现。

主要功能：
1. 计算网格层级和价格
2. 批量创建网格订单
3. 监控订单成交并自动补单
4. 计算盈亏统计

网格交易原理：
--------------
1. 设定价格区间 [lower_price, upper_price]
2. 将区间划分为 N 个网格
3. 在每个网格价位挂买单或卖单
4. 当买单成交后，在上方挂卖单
5. 当卖单成交后，在下方挂买单
6. 循环往复，赚取价格波动利润

三种模式：
---------
- 中性网格 (NEUTRAL): 无初始仓位，双向挂单
- 做多网格 (LONG): 开初始多仓，低买高卖
- 做空网格 (SHORT): 开初始空仓，高卖低买
"""

import asyncio
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from exchanges import ExchangeAdapter, OrderSide, OrderType, OrderStatus
from database import (
    Database, db, GridConfig, GridOrder, Trade,
    GridMode, GridStatus, GridOrderStatus
)


@dataclass
class GridLevel:
    """
    网格层级

    表示一个网格价位的状态
    """
    level: int              # 层级编号（0 = 最低价）
    price: float            # 网格价格
    side: str               # 挂单方向 (buy/sell)
    quantity: float         # 挂单数量
    order_id: Optional[str] = None  # 交易所订单 ID
    status: str = "pending"  # 状态


class GridEngine:
    """
    网格交易引擎

    负责网格策略的核心逻辑：
    - 计算网格价格层级
    - 创建和管理网格订单
    - 监控成交并自动补单
    - 统计盈亏数据

    使用示例：
        # 创建引擎
        engine = GridEngine(exchange_adapter, database)

        # 启动网格
        grid_id = await engine.start_grid(config)

        # 获取网格状态
        status = await engine.get_grid_status(grid_id)

        # 停止网格
        await engine.stop_grid(grid_id)
    """

    def __init__(self, exchange: ExchangeAdapter, database: Database = None):
        """
        初始化网格引擎

        Args:
            exchange: 交易所适配器
            database: 数据库实例（可选，默认使用全局实例）
        """
        self.exchange = exchange
        self.db = database or db

        # 活跃网格字典 {grid_id: GridConfig}
        self.active_grids: Dict[int, GridConfig] = {}

        # 网格层级缓存 {grid_id: [GridLevel, ...]}
        self.grid_levels: Dict[int, List[GridLevel]] = {}

        # 监控任务
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

        # 回调函数（用于通知前端更新）
        self._on_order_filled: Optional[Callable] = None
        self._on_grid_updated: Optional[Callable] = None

    # ========== 网格计算 ==========

    def calculate_grid_prices(self, lower_price: float, upper_price: float,
                               grid_count: int) -> List[float]:
        """
        计算网格价格列表

        使用等差方式划分价格区间

        Args:
            lower_price: 下限价格
            upper_price: 上限价格
            grid_count: 网格数量

        Returns:
            List[float]: 从低到高的价格列表

        Example:
            >>> prices = engine.calculate_grid_prices(90000, 100000, 10)
            >>> print(prices)
            [90000, 91111, 92222, ..., 100000]
        """
        if grid_count < 2:
            raise ValueError("网格数量至少为 2")
        if lower_price >= upper_price:
            raise ValueError("下限价格必须小于上限价格")

        # 计算每格价差
        price_step = (upper_price - lower_price) / (grid_count - 1)

        # 生成价格列表
        prices = []
        for i in range(grid_count):
            price = lower_price + i * price_step
            prices.append(round(price, 8))  # 保留 8 位小数

        return prices

    def calculate_grid_quantity(self, per_grid_amount: float, price: float,
                                 leverage: int = 1) -> float:
        """
        计算每格下单数量

        Args:
            per_grid_amount: 每格投资金额 (DUSD)
            price: 当前价格
            leverage: 杠杆倍数

        Returns:
            float: 下单数量

        计算公式：
            数量 = (投资金额 × 杠杆) / 价格
        """
        return (per_grid_amount * leverage) / price

    def calculate_grid_levels(self, config: GridConfig,
                               current_price: float) -> List[GridLevel]:
        """
        计算网格层级和初始挂单方向

        根据网格模式和当前价格，确定每个层级应该挂买单还是卖单。

        规则：
        - 中性网格：当前价格以下挂买单，以上挂卖单
        - 做多网格：所有层级初始挂买单（在成交后挂卖单）
        - 做空网格：所有层级初始挂卖单（在成交后挂买单）

        Args:
            config: 网格配置
            current_price: 当前市场价格

        Returns:
            List[GridLevel]: 网格层级列表
        """
        # 计算网格价格
        prices = self.calculate_grid_prices(
            config.lower_price,
            config.upper_price,
            config.grid_count
        )

        levels = []
        mode = GridMode(config.mode)

        for i, price in enumerate(prices):
            # 计算该层级的下单数量
            quantity = self.calculate_grid_quantity(
                config.per_grid_amount,
                price,
                config.leverage
            )

            # 根据模式确定挂单方向
            if mode == GridMode.NEUTRAL:
                # 中性网格：价格低于当前价挂买单，高于当前价挂卖单
                side = "buy" if price < current_price else "sell"
            elif mode == GridMode.LONG:
                # 做多网格：初始全部挂买单
                side = "buy"
            else:  # SHORT
                # 做空网格：初始全部挂卖单
                side = "sell"

            level = GridLevel(
                level=i,
                price=price,
                side=side,
                quantity=quantity,
                status="pending"
            )
            levels.append(level)

        return levels

    # ========== 网格生命周期管理 ==========

    async def start_grid(self, config: GridConfig) -> int:
        """
        启动网格交易

        执行步骤：
        1. 保存网格配置到数据库
        2. 获取当前市场价格
        3. 计算网格层级
        4. 批量创建订单
        5. 开始监控

        Args:
            config: 网格配置

        Returns:
            int: 网格 ID

        Raises:
            ValueError: 配置参数错误
        """
        # 验证配置
        self._validate_config(config)

        # 获取当前价格
        ticker = await self.exchange.get_ticker(config.symbol)
        current_price = ticker.last_price

        # 检查当前价格是否在网格区间内
        if current_price < config.lower_price or current_price > config.upper_price:
            # 价格在区间外，可以启动但会有警告
            print(f"⚠️ 警告：当前价格 {current_price} 不在网格区间 "
                  f"[{config.lower_price}, {config.upper_price}] 内")

        # 保存到数据库
        config.status = GridStatus.RUNNING.value
        config.started_at = datetime.now().isoformat()
        grid_id = self.db.create_grid(config)
        config.id = grid_id

        # 计算网格层级
        levels = self.calculate_grid_levels(config, current_price)
        self.grid_levels[grid_id] = levels

        # 创建网格订单到数据库
        await self._create_grid_orders(grid_id, levels)

        # 提交订单到交易所
        await self._place_grid_orders(grid_id, levels)

        # 加入活跃网格
        self.active_grids[grid_id] = config

        # 确保监控任务在运行
        self._ensure_monitor_running()

        print(f"✅ 网格 #{grid_id} 已启动: {config.symbol} "
              f"[{config.lower_price} - {config.upper_price}] "
              f"共 {config.grid_count} 格")

        return grid_id

    async def stop_grid(self, grid_id: int, cancel_orders: bool = True) -> bool:
        """
        停止网格交易

        Args:
            grid_id: 网格 ID
            cancel_orders: 是否取消所有挂单

        Returns:
            bool: 是否停止成功
        """
        if grid_id not in self.active_grids:
            # 从数据库加载
            config = self.db.get_grid(grid_id)
            if not config:
                return False
        else:
            config = self.active_grids[grid_id]

        # 取消所有订单
        if cancel_orders:
            await self._cancel_all_grid_orders(grid_id)

        # 更新状态
        self.db.update_grid_status(grid_id, GridStatus.STOPPED)

        # 从活跃列表移除
        if grid_id in self.active_grids:
            del self.active_grids[grid_id]
        if grid_id in self.grid_levels:
            del self.grid_levels[grid_id]

        print(f"🛑 网格 #{grid_id} 已停止")
        return True

    async def pause_grid(self, grid_id: int) -> bool:
        """
        暂停网格交易

        暂停后不会取消现有订单，只是停止监控

        Args:
            grid_id: 网格 ID

        Returns:
            bool: 是否暂停成功
        """
        if grid_id not in self.active_grids:
            return False

        self.db.update_grid_status(grid_id, GridStatus.PAUSED)
        print(f"⏸️ 网格 #{grid_id} 已暂停")
        return True

    async def resume_grid(self, grid_id: int) -> bool:
        """
        恢复网格交易

        Args:
            grid_id: 网格 ID

        Returns:
            bool: 是否恢复成功
        """
        config = self.db.get_grid(grid_id)
        if not config or config.status != GridStatus.PAUSED.value:
            return False

        self.db.update_grid_status(grid_id, GridStatus.RUNNING)
        self.active_grids[grid_id] = config
        self._ensure_monitor_running()

        print(f"▶️ 网格 #{grid_id} 已恢复")
        return True

    def _validate_config(self, config: GridConfig):
        """验证网格配置"""
        if config.lower_price >= config.upper_price:
            raise ValueError("下限价格必须小于上限价格")
        if config.grid_count < 2:
            raise ValueError("网格数量至少为 2")
        if config.grid_count > 100:
            raise ValueError("网格数量最多为 100")
        if config.per_grid_amount <= 0:
            raise ValueError("每格投资金额必须大于 0")
        if config.leverage < 1 or config.leverage > 100:
            raise ValueError("杠杆倍数必须在 1-100 之间")

    # ========== 订单管理 ==========

    async def _create_grid_orders(self, grid_id: int, levels: List[GridLevel]):
        """
        在数据库中创建网格订单记录

        Args:
            grid_id: 网格 ID
            levels: 网格层级列表
        """
        orders = []
        for level in levels:
            order = GridOrder(
                grid_id=grid_id,
                grid_level=level.level,
                side=level.side,
                price=level.price,
                quantity=level.quantity,
                status=GridOrderStatus.PENDING.value
            )
            orders.append(order)

        self.db.create_grid_orders_batch(orders)

    async def _place_grid_orders(self, grid_id: int, levels: List[GridLevel]):
        """
        向交易所提交网格订单

        Args:
            grid_id: 网格 ID
            levels: 网格层级列表
        """
        config = self.active_grids.get(grid_id)
        if not config:
            return

        # 获取数据库中的订单记录
        db_orders = self.db.get_grid_orders(grid_id)
        order_map = {o.grid_level: o for o in db_orders}

        # 批量提交订单
        for level in levels:
            try:
                # 创建订单
                order = await self.exchange.create_order(
                    symbol=config.symbol,
                    side=OrderSide.BUY if level.side == "buy" else OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    quantity=level.quantity,
                    price=level.price,
                    leverage=config.leverage
                )

                # 更新层级状态
                level.order_id = order.order_id
                level.status = "open"

                # 更新数据库
                if level.level in order_map:
                    db_order = order_map[level.level]
                    self.db.update_grid_order_status(
                        db_order.id,
                        GridOrderStatus.OPEN,
                        order.order_id
                    )

            except Exception as e:
                print(f"❌ 网格 #{grid_id} 层级 {level.level} 下单失败: {e}")

    async def _cancel_all_grid_orders(self, grid_id: int):
        """
        取消网格的所有挂单

        Args:
            grid_id: 网格 ID
        """
        config = self.active_grids.get(grid_id) or self.db.get_grid(grid_id)
        if not config:
            return

        # 获取活跃订单
        db_orders = self.db.get_open_grid_orders(grid_id)

        for order in db_orders:
            if order.order_id:
                try:
                    await self.exchange.cancel_order(config.symbol, order.order_id)
                    self.db.update_grid_order_status(
                        order.id, GridOrderStatus.CANCELLED
                    )
                except Exception as e:
                    print(f"取消订单 {order.order_id} 失败: {e}")

    # ========== 订单监控和补单 ==========

    def _ensure_monitor_running(self):
        """确保监控任务在运行"""
        if not self._running or self._monitor_task is None:
            self._running = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self):
        """
        订单监控循环

        定期检查订单状态，处理成交事件
        """
        print("🔄 网格监控已启动")

        while self._running and self.active_grids:
            try:
                for grid_id in list(self.active_grids.keys()):
                    config = self.active_grids.get(grid_id)
                    if not config or config.status != GridStatus.RUNNING.value:
                        continue

                    await self._check_grid_orders(grid_id)

                # 每 5 秒检查一次
                await asyncio.sleep(5)

            except Exception as e:
                print(f"监控循环错误: {e}")
                await asyncio.sleep(5)

        print("🔄 网格监控已停止")

    async def _check_grid_orders(self, grid_id: int):
        """
        检查网格订单状态

        检测成交订单并触发补单

        Args:
            grid_id: 网格 ID
        """
        config = self.active_grids.get(grid_id)
        if not config:
            return

        levels = self.grid_levels.get(grid_id, [])
        if not levels:
            return

        # 获取交易所的活跃订单
        exchange_orders = await self.exchange.get_open_orders(config.symbol)
        exchange_order_ids = {o.order_id for o in exchange_orders}

        # 检查每个层级
        for level in levels:
            if level.status != "open" or not level.order_id:
                continue

            # 如果订单不在活跃列表中，说明已成交或已取消
            if level.order_id not in exchange_order_ids:
                # 查询订单状态确认
                try:
                    order = await self.exchange.get_order(
                        config.symbol, level.order_id
                    )
                    if order.status == OrderStatus.FILLED:
                        await self._handle_order_filled(grid_id, level, order)

                except Exception as e:
                    print(f"查询订单 {level.order_id} 状态失败: {e}")

    async def _handle_order_filled(self, grid_id: int, level: GridLevel, order):
        """
        处理订单成交事件

        成交后的操作：
        1. 更新数据库记录
        2. 记录成交历史
        3. 在对侧挂补单
        4. 更新盈亏统计

        Args:
            grid_id: 网格 ID
            level: 网格层级
            order: 成交的订单
        """
        config = self.active_grids.get(grid_id)
        if not config:
            return

        print(f"✅ 网格 #{grid_id} 层级 {level.level} 订单成交: "
              f"{level.side} {order.quantity}@{order.price}")

        # 更新层级状态
        level.status = "filled"

        # 更新数据库订单状态
        db_orders = self.db.get_grid_orders(grid_id)
        for db_order in db_orders:
            if db_order.grid_level == level.level:
                db_order.status = GridOrderStatus.FILLED.value
                db_order.fill_price = order.price
                db_order.fill_time = datetime.now().isoformat()
                db_order.filled_quantity = order.filled_quantity
                self.db.update_grid_order(db_order)

                # 记录成交
                self._record_trade(grid_id, db_order, order)
                break

        # 创建补单（在对侧挂单）
        await self._create_replenishment_order(grid_id, level, config)

        # 更新盈亏统计
        await self._update_grid_statistics(grid_id)

        # 触发回调
        if self._on_order_filled:
            self._on_order_filled(grid_id, level, order)

    async def _create_replenishment_order(self, grid_id: int, filled_level: GridLevel,
                                           config: GridConfig):
        """
        创建补单

        补单逻辑（参考 ASTER/OKX）：
        - 买单成交后，在上一个网格层级挂卖单
        - 卖单成交后，在下一个网格层级挂买单

        Args:
            grid_id: 网格 ID
            filled_level: 刚成交的网格层级
            config: 网格配置
        """
        levels = self.grid_levels.get(grid_id, [])
        if not levels:
            return

        # 确定补单的层级
        if filled_level.side == "buy":
            # 买单成交 → 在上方挂卖单
            target_level_idx = filled_level.level + 1
            new_side = "sell"
        else:
            # 卖单成交 → 在下方挂买单
            target_level_idx = filled_level.level - 1
            new_side = "buy"

        # 检查目标层级是否有效
        if target_level_idx < 0 or target_level_idx >= len(levels):
            print(f"⚠️ 网格 #{grid_id} 层级 {target_level_idx} 超出范围，无法补单")
            return

        target_level = levels[target_level_idx]

        # 检查目标层级是否已有活跃订单
        if target_level.status == "open":
            print(f"⚠️ 网格 #{grid_id} 层级 {target_level_idx} 已有活跃订单")
            return

        # 更新层级方向和状态
        target_level.side = new_side
        target_level.status = "pending"

        # 计算数量
        quantity = self.calculate_grid_quantity(
            config.per_grid_amount,
            target_level.price,
            config.leverage
        )
        target_level.quantity = quantity

        # 创建数据库订单记录
        db_order = GridOrder(
            grid_id=grid_id,
            grid_level=target_level.level,
            side=new_side,
            price=target_level.price,
            quantity=quantity,
            status=GridOrderStatus.PENDING.value
        )
        order_db_id = self.db.create_grid_order(db_order)

        # 提交到交易所
        try:
            order = await self.exchange.create_order(
                symbol=config.symbol,
                side=OrderSide.BUY if new_side == "buy" else OrderSide.SELL,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                price=target_level.price,
                leverage=config.leverage
            )

            target_level.order_id = order.order_id
            target_level.status = "open"

            self.db.update_grid_order_status(
                order_db_id, GridOrderStatus.OPEN, order.order_id
            )

            print(f"📝 网格 #{grid_id} 补单: {new_side} "
                  f"{quantity:.4f}@{target_level.price}")

        except Exception as e:
            print(f"❌ 网格 #{grid_id} 补单失败: {e}")

    def _record_trade(self, grid_id: int, db_order: GridOrder, exchange_order):
        """
        记录成交到数据库

        Args:
            grid_id: 网格 ID
            db_order: 数据库订单记录
            exchange_order: 交易所订单信息
        """
        # 计算成交金额
        value = exchange_order.filled_quantity * exchange_order.price

        # 计算手续费（假设 Taker 费率 0.04%）
        fee = value * 0.0004

        # 创建成交记录
        trade = Trade(
            grid_id=grid_id,
            grid_order_id=db_order.id,
            order_id=exchange_order.order_id,
            side=db_order.side,
            price=exchange_order.price,
            quantity=exchange_order.filled_quantity,
            value=value,
            fee=fee,
            pnl=0  # 盈亏在后续计算
        )
        self.db.create_trade(trade)

    async def _update_grid_statistics(self, grid_id: int):
        """
        更新网格盈亏统计

        Args:
            grid_id: 网格 ID
        """
        stats = self.db.get_grid_statistics(grid_id)

        # 计算已实现盈亏
        # 简单计算：(卖出金额 - 买入金额) - 手续费
        trades = self.db.get_trades_by_grid(grid_id)

        buy_value = sum(t.value for t in trades if t.side == "buy")
        sell_value = sum(t.value for t in trades if t.side == "sell")
        total_fees = sum(t.fee for t in trades)

        realized_pnl = sell_value - buy_value - total_fees

        # 更新数据库
        self.db.update_grid_pnl(
            grid_id,
            realized_pnl=realized_pnl,
            unrealized_pnl=0,  # 未实现盈亏需要根据当前持仓计算
            total_trades=stats['total_trades'],
            total_fees=total_fees
        )

        # 触发回调
        if self._on_grid_updated:
            self._on_grid_updated(grid_id)

    # ========== 状态查询 ==========

    async def get_grid_status(self, grid_id: int) -> Optional[Dict[str, Any]]:
        """
        获取网格状态

        返回网格的完整状态信息

        Args:
            grid_id: 网格 ID

        Returns:
            Dict: 网格状态
                - config: 网格配置
                - levels: 网格层级列表
                - statistics: 统计信息
                - current_price: 当前价格
        """
        config = self.active_grids.get(grid_id) or self.db.get_grid(grid_id)
        if not config:
            return None

        # 获取当前价格
        ticker = await self.exchange.get_ticker(config.symbol)

        # 获取网格层级
        levels = self.grid_levels.get(grid_id, [])
        if not levels:
            # 从数据库恢复
            db_orders = self.db.get_grid_orders(grid_id)
            levels = [
                GridLevel(
                    level=o.grid_level,
                    price=o.price,
                    side=o.side,
                    quantity=o.quantity,
                    order_id=o.order_id,
                    status=o.status
                )
                for o in db_orders
            ]

        # 获取统计信息
        stats = self.db.get_grid_statistics(grid_id)

        return {
            "config": {
                "id": config.id,
                "symbol": config.symbol,
                "mode": config.mode,
                "status": config.status,
                "lower_price": config.lower_price,
                "upper_price": config.upper_price,
                "grid_count": config.grid_count,
                "per_grid_amount": config.per_grid_amount,
                "leverage": config.leverage,
                "created_at": config.created_at,
                "started_at": config.started_at,
            },
            "levels": [
                {
                    "level": l.level,
                    "price": l.price,
                    "side": l.side,
                    "quantity": l.quantity,
                    "status": l.status,
                }
                for l in levels
            ],
            "statistics": stats,
            "current_price": ticker.last_price,
        }

    async def get_grid_orders(self, grid_id: int) -> List[Dict]:
        """
        获取网格订单列表

        Args:
            grid_id: 网格 ID

        Returns:
            List[Dict]: 订单列表
        """
        orders = self.db.get_grid_orders(grid_id)
        return [
            {
                "id": o.id,
                "grid_level": o.grid_level,
                "side": o.side,
                "price": o.price,
                "quantity": o.quantity,
                "filled_quantity": o.filled_quantity,
                "status": o.status,
                "created_at": o.created_at,
            }
            for o in orders
        ]

    async def get_grid_trades(self, grid_id: int, limit: int = 50) -> List[Dict]:
        """
        获取网格成交历史

        Args:
            grid_id: 网格 ID
            limit: 返回数量限制

        Returns:
            List[Dict]: 成交记录列表
        """
        trades = self.db.get_trades_by_grid(grid_id, limit)
        return [
            {
                "id": t.id,
                "side": t.side,
                "price": t.price,
                "quantity": t.quantity,
                "value": t.value,
                "fee": t.fee,
                "pnl": t.pnl,
                "created_at": t.created_at,
            }
            for t in trades
        ]

    # ========== 回调设置 ==========

    def set_callbacks(self, on_order_filled: Callable = None,
                      on_grid_updated: Callable = None):
        """
        设置回调函数

        Args:
            on_order_filled: 订单成交回调 (grid_id, level, order)
            on_grid_updated: 网格更新回调 (grid_id)
        """
        self._on_order_filled = on_order_filled
        self._on_grid_updated = on_grid_updated

    # ========== 清理 ==========

    async def shutdown(self):
        """
        关闭引擎

        停止所有活跃网格并清理资源
        """
        self._running = False

        # 停止所有网格
        for grid_id in list(self.active_grids.keys()):
            await self.stop_grid(grid_id, cancel_orders=True)

        # 等待监控任务结束
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        print("🔌 网格引擎已关闭")
