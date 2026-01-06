"""
数据库模型
=========

使用 SQLite 存储网格交易数据，包括：
- 网格配置
- 订单记录
- 成交历史
- 盈亏统计

为什么选择 SQLite：
- 轻量级，无需额外安装数据库服务
- 单文件存储，方便备份和迁移
- 对于单用户网格交易工具足够使用
"""

import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum
from contextlib import contextmanager


# ========== 枚举类型定义 ==========

class GridMode(Enum):
    """
    网格模式

    - NEUTRAL: 中性网格，无初始仓位，双向挂单
    - LONG: 做多网格，开初始多仓，低买高卖
    - SHORT: 做空网格，开初始空仓，高卖低买
    """
    NEUTRAL = "neutral"
    LONG = "long"
    SHORT = "short"


class GridStatus(Enum):
    """
    网格状态

    - PENDING: 等待启动
    - RUNNING: 运行中
    - PAUSED: 已暂停
    - STOPPED: 已停止
    - COMPLETED: 已完成（触发止盈/止损）
    """
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class GridOrderStatus(Enum):
    """
    网格订单状态

    - PENDING: 待挂单
    - OPEN: 已挂单，等待成交
    - FILLED: 已成交
    - CANCELLED: 已取消
    """
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"


# ========== 数据类定义 ==========

@dataclass
class GridConfig:
    """
    网格配置

    存储网格交易的所有参数设置
    """
    id: Optional[int] = None           # 网格 ID（数据库自动生成）
    wallet_address: str = ""           # 钱包地址
    exchange: str = "standx"           # 交易所
    symbol: str = "BTC-USD"            # 交易对
    mode: str = "neutral"              # 网格模式 (neutral/long/short)
    status: str = "pending"            # 状态

    # 价格区间
    lower_price: float = 0.0           # 网格下限价格
    upper_price: float = 0.0           # 网格上限价格
    grid_count: int = 10               # 网格数量

    # 资金设置
    per_grid_amount: float = 100.0     # 每格投资金额 (DUSD)
    total_investment: float = 0.0      # 总投资金额
    leverage: int = 1                  # 杠杆倍数

    # 高级设置
    stop_loss_percent: Optional[float] = None   # 止损百分比
    take_profit_percent: Optional[float] = None # 止盈百分比
    auto_restart: bool = True          # 价格回到区间自动重启

    # 统计数据
    realized_pnl: float = 0.0          # 已实现盈亏
    unrealized_pnl: float = 0.0        # 未实现盈亏
    total_trades: int = 0              # 总成交次数
    total_fees: float = 0.0            # 总手续费

    # 时间戳
    created_at: Optional[str] = None   # 创建时间
    started_at: Optional[str] = None   # 启动时间
    stopped_at: Optional[str] = None   # 停止时间


@dataclass
class GridOrder:
    """
    网格订单

    每个网格层级对应一个订单
    """
    id: Optional[int] = None           # 订单记录 ID
    grid_id: int = 0                   # 所属网格 ID
    grid_level: int = 0                # 网格层级（0 = 最低价，grid_count-1 = 最高价）

    # 订单信息
    order_id: str = ""                 # 交易所订单 ID
    side: str = "buy"                  # 方向 (buy/sell)
    price: float = 0.0                 # 挂单价格
    quantity: float = 0.0              # 挂单数量
    filled_quantity: float = 0.0       # 已成交数量

    status: str = "pending"            # 状态

    # 成交信息
    fill_price: Optional[float] = None # 成交均价
    fill_time: Optional[str] = None    # 成交时间
    fee: float = 0.0                   # 手续费

    created_at: Optional[str] = None   # 创建时间
    updated_at: Optional[str] = None   # 更新时间


@dataclass
class Trade:
    """
    成交记录

    记录每一笔实际成交
    """
    id: Optional[int] = None
    grid_id: int = 0                   # 所属网格 ID
    grid_order_id: int = 0             # 关联的网格订单 ID
    order_id: str = ""                 # 交易所订单 ID

    # 成交信息
    side: str = "buy"                  # 方向
    price: float = 0.0                 # 成交价格
    quantity: float = 0.0              # 成交数量
    value: float = 0.0                 # 成交金额
    fee: float = 0.0                   # 手续费
    pnl: float = 0.0                   # 本次成交盈亏

    created_at: Optional[str] = None   # 成交时间


# ========== 数据库管理类 ==========

class Database:
    """
    数据库管理类

    提供网格交易数据的 CRUD 操作

    使用示例：
        db = Database('gridbot.db')
        db.init_tables()

        # 创建网格
        grid = GridConfig(
            wallet_address='0x...',
            symbol='BTC-USD',
            lower_price=90000,
            upper_price=100000,
            grid_count=10
        )
        grid_id = db.create_grid(grid)

        # 查询网格
        grid = db.get_grid(grid_id)
    """

    def __init__(self, db_path: str = "gridbot.db"):
        """
        初始化数据库连接

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self._init_tables()

    @contextmanager
    def _get_connection(self):
        """
        获取数据库连接（上下文管理器）

        使用 with 语句自动管理连接的打开和关闭
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 返回字典格式
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_tables(self):
        """
        初始化数据库表

        创建所有必要的表结构
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 网格配置表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS grids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT NOT NULL,
                    exchange TEXT NOT NULL DEFAULT 'standx',
                    symbol TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'neutral',
                    status TEXT NOT NULL DEFAULT 'pending',

                    -- 价格区间
                    lower_price REAL NOT NULL,
                    upper_price REAL NOT NULL,
                    grid_count INTEGER NOT NULL DEFAULT 10,

                    -- 资金设置
                    per_grid_amount REAL NOT NULL DEFAULT 100,
                    total_investment REAL NOT NULL DEFAULT 0,
                    leverage INTEGER NOT NULL DEFAULT 1,

                    -- 高级设置
                    stop_loss_percent REAL,
                    take_profit_percent REAL,
                    auto_restart INTEGER NOT NULL DEFAULT 1,

                    -- 统计数据
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    unrealized_pnl REAL NOT NULL DEFAULT 0,
                    total_trades INTEGER NOT NULL DEFAULT 0,
                    total_fees REAL NOT NULL DEFAULT 0,

                    -- 时间戳
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    stopped_at TEXT
                )
            """)

            # 网格订单表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS grid_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grid_id INTEGER NOT NULL,
                    grid_level INTEGER NOT NULL,

                    -- 订单信息
                    order_id TEXT,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    filled_quantity REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',

                    -- 成交信息
                    fill_price REAL,
                    fill_time TEXT,
                    fee REAL NOT NULL DEFAULT 0,

                    created_at TEXT NOT NULL,
                    updated_at TEXT,

                    FOREIGN KEY (grid_id) REFERENCES grids(id)
                )
            """)

            # 成交记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grid_id INTEGER NOT NULL,
                    grid_order_id INTEGER,
                    order_id TEXT,

                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    value REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    pnl REAL NOT NULL DEFAULT 0,

                    created_at TEXT NOT NULL,

                    FOREIGN KEY (grid_id) REFERENCES grids(id),
                    FOREIGN KEY (grid_order_id) REFERENCES grid_orders(id)
                )
            """)

            # 创建索引以提高查询性能
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_grids_wallet
                ON grids(wallet_address)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_grids_status
                ON grids(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_grid_orders_grid_id
                ON grid_orders(grid_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_grid_id
                ON trades(grid_id)
            """)

    # ========== 网格 CRUD 操作 ==========

    def create_grid(self, grid: GridConfig) -> int:
        """
        创建新网格

        Args:
            grid: 网格配置对象

        Returns:
            int: 新创建的网格 ID
        """
        grid.created_at = datetime.now().isoformat()
        grid.total_investment = grid.per_grid_amount * grid.grid_count

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO grids (
                    wallet_address, exchange, symbol, mode, status,
                    lower_price, upper_price, grid_count,
                    per_grid_amount, total_investment, leverage,
                    stop_loss_percent, take_profit_percent, auto_restart,
                    realized_pnl, unrealized_pnl, total_trades, total_fees,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                grid.wallet_address, grid.exchange, grid.symbol,
                grid.mode, grid.status,
                grid.lower_price, grid.upper_price, grid.grid_count,
                grid.per_grid_amount, grid.total_investment, grid.leverage,
                grid.stop_loss_percent, grid.take_profit_percent,
                1 if grid.auto_restart else 0,
                grid.realized_pnl, grid.unrealized_pnl,
                grid.total_trades, grid.total_fees,
                grid.created_at
            ))
            return cursor.lastrowid

    def get_grid(self, grid_id: int) -> Optional[GridConfig]:
        """
        获取网格配置

        Args:
            grid_id: 网格 ID

        Returns:
            GridConfig: 网格配置，不存在返回 None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM grids WHERE id = ?", (grid_id,))
            row = cursor.fetchone()

            if row:
                return self._row_to_grid(row)
            return None

    def get_grids_by_wallet(self, wallet_address: str) -> List[GridConfig]:
        """
        获取钱包的所有网格

        Args:
            wallet_address: 钱包地址

        Returns:
            List[GridConfig]: 网格列表
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM grids WHERE wallet_address = ? ORDER BY created_at DESC",
                (wallet_address,)
            )
            return [self._row_to_grid(row) for row in cursor.fetchall()]

    def get_running_grids(self) -> List[GridConfig]:
        """
        获取所有运行中的网格

        Returns:
            List[GridConfig]: 运行中的网格列表
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM grids WHERE status = ?",
                (GridStatus.RUNNING.value,)
            )
            return [self._row_to_grid(row) for row in cursor.fetchall()]

    def update_grid(self, grid: GridConfig) -> bool:
        """
        更新网格配置

        Args:
            grid: 网格配置对象（必须包含 id）

        Returns:
            bool: 是否更新成功
        """
        if not grid.id:
            return False

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE grids SET
                    status = ?, realized_pnl = ?, unrealized_pnl = ?,
                    total_trades = ?, total_fees = ?,
                    started_at = ?, stopped_at = ?
                WHERE id = ?
            """, (
                grid.status, grid.realized_pnl, grid.unrealized_pnl,
                grid.total_trades, grid.total_fees,
                grid.started_at, grid.stopped_at,
                grid.id
            ))
            return cursor.rowcount > 0

    def update_grid_status(self, grid_id: int, status: GridStatus) -> bool:
        """
        更新网格状态

        Args:
            grid_id: 网格 ID
            status: 新状态

        Returns:
            bool: 是否更新成功
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 如果是启动状态，记录启动时间
            if status == GridStatus.RUNNING:
                cursor.execute("""
                    UPDATE grids SET status = ?, started_at = ?
                    WHERE id = ?
                """, (status.value, datetime.now().isoformat(), grid_id))
            # 如果是停止状态，记录停止时间
            elif status in (GridStatus.STOPPED, GridStatus.COMPLETED):
                cursor.execute("""
                    UPDATE grids SET status = ?, stopped_at = ?
                    WHERE id = ?
                """, (status.value, datetime.now().isoformat(), grid_id))
            else:
                cursor.execute("""
                    UPDATE grids SET status = ? WHERE id = ?
                """, (status.value, grid_id))

            return cursor.rowcount > 0

    def update_grid_pnl(self, grid_id: int, realized_pnl: float,
                        unrealized_pnl: float, total_trades: int,
                        total_fees: float) -> bool:
        """
        更新网格盈亏统计

        Args:
            grid_id: 网格 ID
            realized_pnl: 已实现盈亏
            unrealized_pnl: 未实现盈亏
            total_trades: 总成交次数
            total_fees: 总手续费

        Returns:
            bool: 是否更新成功
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE grids SET
                    realized_pnl = ?, unrealized_pnl = ?,
                    total_trades = ?, total_fees = ?
                WHERE id = ?
            """, (realized_pnl, unrealized_pnl, total_trades, total_fees, grid_id))
            return cursor.rowcount > 0

    def delete_grid(self, grid_id: int) -> bool:
        """
        删除网格及其所有订单和成交记录

        Args:
            grid_id: 网格 ID

        Returns:
            bool: 是否删除成功
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 删除成交记录
            cursor.execute("DELETE FROM trades WHERE grid_id = ?", (grid_id,))

            # 删除网格订单
            cursor.execute("DELETE FROM grid_orders WHERE grid_id = ?", (grid_id,))

            # 删除网格
            cursor.execute("DELETE FROM grids WHERE id = ?", (grid_id,))

            return cursor.rowcount > 0

    def _row_to_grid(self, row: sqlite3.Row) -> GridConfig:
        """将数据库行转换为 GridConfig 对象"""
        return GridConfig(
            id=row['id'],
            wallet_address=row['wallet_address'],
            exchange=row['exchange'],
            symbol=row['symbol'],
            mode=row['mode'],
            status=row['status'],
            lower_price=row['lower_price'],
            upper_price=row['upper_price'],
            grid_count=row['grid_count'],
            per_grid_amount=row['per_grid_amount'],
            total_investment=row['total_investment'],
            leverage=row['leverage'],
            stop_loss_percent=row['stop_loss_percent'],
            take_profit_percent=row['take_profit_percent'],
            auto_restart=bool(row['auto_restart']),
            realized_pnl=row['realized_pnl'],
            unrealized_pnl=row['unrealized_pnl'],
            total_trades=row['total_trades'],
            total_fees=row['total_fees'],
            created_at=row['created_at'],
            started_at=row['started_at'],
            stopped_at=row['stopped_at']
        )

    # ========== 网格订单 CRUD 操作 ==========

    def create_grid_order(self, order: GridOrder) -> int:
        """
        创建网格订单

        Args:
            order: 网格订单对象

        Returns:
            int: 新创建的订单 ID
        """
        order.created_at = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO grid_orders (
                    grid_id, grid_level, order_id, side, price, quantity,
                    filled_quantity, status, fee, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.grid_id, order.grid_level, order.order_id,
                order.side, order.price, order.quantity,
                order.filled_quantity, order.status, order.fee,
                order.created_at
            ))
            return cursor.lastrowid

    def create_grid_orders_batch(self, orders: List[GridOrder]) -> List[int]:
        """
        批量创建网格订单

        Args:
            orders: 网格订单列表

        Returns:
            List[int]: 创建的订单 ID 列表
        """
        now = datetime.now().isoformat()
        order_ids = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for order in orders:
                order.created_at = now
                cursor.execute("""
                    INSERT INTO grid_orders (
                        grid_id, grid_level, order_id, side, price, quantity,
                        filled_quantity, status, fee, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order.grid_id, order.grid_level, order.order_id,
                    order.side, order.price, order.quantity,
                    order.filled_quantity, order.status, order.fee,
                    order.created_at
                ))
                order_ids.append(cursor.lastrowid)

        return order_ids

    def get_grid_orders(self, grid_id: int) -> List[GridOrder]:
        """
        获取网格的所有订单

        Args:
            grid_id: 网格 ID

        Returns:
            List[GridOrder]: 订单列表
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM grid_orders WHERE grid_id = ? ORDER BY grid_level",
                (grid_id,)
            )
            return [self._row_to_grid_order(row) for row in cursor.fetchall()]

    def get_open_grid_orders(self, grid_id: int) -> List[GridOrder]:
        """
        获取网格的活跃订单

        Args:
            grid_id: 网格 ID

        Returns:
            List[GridOrder]: 活跃订单列表
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM grid_orders WHERE grid_id = ? AND status = ? ORDER BY grid_level",
                (grid_id, GridOrderStatus.OPEN.value)
            )
            return [self._row_to_grid_order(row) for row in cursor.fetchall()]

    def update_grid_order(self, order: GridOrder) -> bool:
        """
        更新网格订单

        Args:
            order: 网格订单对象

        Returns:
            bool: 是否更新成功
        """
        if not order.id:
            return False

        order.updated_at = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE grid_orders SET
                    order_id = ?, status = ?, filled_quantity = ?,
                    fill_price = ?, fill_time = ?, fee = ?, updated_at = ?
                WHERE id = ?
            """, (
                order.order_id, order.status, order.filled_quantity,
                order.fill_price, order.fill_time, order.fee,
                order.updated_at, order.id
            ))
            return cursor.rowcount > 0

    def update_grid_order_status(self, order_id: int, status: GridOrderStatus,
                                  exchange_order_id: str = None) -> bool:
        """
        更新网格订单状态

        Args:
            order_id: 数据库订单 ID
            status: 新状态
            exchange_order_id: 交易所订单 ID（可选）

        Returns:
            bool: 是否更新成功
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if exchange_order_id:
                cursor.execute("""
                    UPDATE grid_orders SET status = ?, order_id = ?, updated_at = ?
                    WHERE id = ?
                """, (status.value, exchange_order_id, datetime.now().isoformat(), order_id))
            else:
                cursor.execute("""
                    UPDATE grid_orders SET status = ?, updated_at = ?
                    WHERE id = ?
                """, (status.value, datetime.now().isoformat(), order_id))
            return cursor.rowcount > 0

    def _row_to_grid_order(self, row: sqlite3.Row) -> GridOrder:
        """将数据库行转换为 GridOrder 对象"""
        return GridOrder(
            id=row['id'],
            grid_id=row['grid_id'],
            grid_level=row['grid_level'],
            order_id=row['order_id'] or "",
            side=row['side'],
            price=row['price'],
            quantity=row['quantity'],
            filled_quantity=row['filled_quantity'],
            status=row['status'],
            fill_price=row['fill_price'],
            fill_time=row['fill_time'],
            fee=row['fee'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )

    # ========== 成交记录 CRUD 操作 ==========

    def create_trade(self, trade: Trade) -> int:
        """
        创建成交记录

        Args:
            trade: 成交记录对象

        Returns:
            int: 新创建的记录 ID
        """
        trade.created_at = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (
                    grid_id, grid_order_id, order_id, side,
                    price, quantity, value, fee, pnl, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.grid_id, trade.grid_order_id, trade.order_id,
                trade.side, trade.price, trade.quantity, trade.value,
                trade.fee, trade.pnl, trade.created_at
            ))
            return cursor.lastrowid

    def get_trades_by_grid(self, grid_id: int, limit: int = 100) -> List[Trade]:
        """
        获取网格的成交记录

        Args:
            grid_id: 网格 ID
            limit: 返回数量限制

        Returns:
            List[Trade]: 成交记录列表
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trades WHERE grid_id = ? ORDER BY created_at DESC LIMIT ?",
                (grid_id, limit)
            )
            return [self._row_to_trade(row) for row in cursor.fetchall()]

    def get_grid_statistics(self, grid_id: int) -> Dict[str, Any]:
        """
        获取网格统计信息

        Args:
            grid_id: 网格 ID

        Returns:
            Dict: 统计信息
                - total_trades: 总成交次数
                - total_volume: 总成交量
                - total_value: 总成交金额
                - total_fees: 总手续费
                - total_pnl: 总盈亏
                - buy_trades: 买入次数
                - sell_trades: 卖出次数
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(quantity) as total_volume,
                    SUM(value) as total_value,
                    SUM(fee) as total_fees,
                    SUM(pnl) as total_pnl,
                    SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buy_trades,
                    SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sell_trades
                FROM trades WHERE grid_id = ?
            """, (grid_id,))

            row = cursor.fetchone()
            if row:
                return {
                    "total_trades": row['total_trades'] or 0,
                    "total_volume": row['total_volume'] or 0,
                    "total_value": row['total_value'] or 0,
                    "total_fees": row['total_fees'] or 0,
                    "total_pnl": row['total_pnl'] or 0,
                    "buy_trades": row['buy_trades'] or 0,
                    "sell_trades": row['sell_trades'] or 0,
                }

            return {
                "total_trades": 0,
                "total_volume": 0,
                "total_value": 0,
                "total_fees": 0,
                "total_pnl": 0,
                "buy_trades": 0,
                "sell_trades": 0,
            }

    def _row_to_trade(self, row: sqlite3.Row) -> Trade:
        """将数据库行转换为 Trade 对象"""
        return Trade(
            id=row['id'],
            grid_id=row['grid_id'],
            grid_order_id=row['grid_order_id'],
            order_id=row['order_id'] or "",
            side=row['side'],
            price=row['price'],
            quantity=row['quantity'],
            value=row['value'],
            fee=row['fee'],
            pnl=row['pnl'],
            created_at=row['created_at']
        )


# 全局数据库实例
db = Database()
