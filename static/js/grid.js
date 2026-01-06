/**
 * 网格交易工具 - 前端交互逻辑
 *
 * 功能：
 * 1. MetaMask 钱包连接
 * 2. 网格配置和创建
 * 3. 实时行情显示
 * 4. 订单状态监控
 * 5. 盈亏统计展示
 */

// ========== 全局状态 ==========
const state = {
    // 钱包状态
    wallet: {
        connected: false,
        address: null,
        sessionId: null,
    },
    // API 配置状态
    apiConfig: {
        jwtToken: null,
        signingKey: null,      // Ed25519 私钥（base64）
        requestId: null,       // 请求 ID（base58 公钥）
        simulationMode: false, // 始终使用真实交易模式
        isConfigured: false,   // 是否已配置 API
    },
    // 当前选择
    exchange: 'standx',
    symbol: 'BTC-USD',
    mode: 'neutral',
    leverage: 1,
    // 行情数据
    ticker: null,
    // WebSocket 连接
    ws: null,
    // 活跃网格
    grids: [],
};

// API 基础 URL
const API_BASE = '';

// ========== 工具函数 ==========

/**
 * 显示 Toast 通知
 */
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    // 3 秒后自动移除
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

/**
 * 发送 API 请求
 */
async function apiRequest(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers,
    };

    // 添加会话 Token
    if (state.wallet.sessionId) {
        headers['X-Session-Token'] = state.wallet.sessionId;
    }

    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers,
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || '请求失败');
        }

        return data;
    } catch (error) {
        console.error('API 请求错误:', error);
        throw error;
    }
}

/**
 * 格式化价格
 */
function formatPrice(price, decimals = 2) {
    if (price === null || price === undefined) return '--';
    return Number(price).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

/**
 * 格式化地址（显示缩写）
 */
function formatAddress(address) {
    if (!address) return '';
    return `${address.slice(0, 6)}...${address.slice(-4)}`;
}

// ========== 钱包连接 ==========

/**
 * 检查 MetaMask 是否安装
 */
function isMetaMaskInstalled() {
    return typeof window.ethereum !== 'undefined' && window.ethereum.isMetaMask;
}

/**
 * 连接 MetaMask 钱包
 */
async function connectMetaMask() {
    // 检查 MetaMask
    if (!isMetaMaskInstalled()) {
        showToast('请先安装 MetaMask 钱包', 'error');
        window.open('https://metamask.io/download/', '_blank');
        return;
    }

    try {
        // 请求账户授权
        const accounts = await window.ethereum.request({
            method: 'eth_requestAccounts',
        });

        if (!accounts || accounts.length === 0) {
            showToast('未获取到账户', 'error');
            return;
        }

        const address = accounts[0];
        console.log('钱包地址:', address);

        // 向后端请求签名消息
        const connectResponse = await apiRequest('/api/wallet/connect', {
            method: 'POST',
            body: JSON.stringify({ address }),
        });

        const { session_id, message } = connectResponse;

        // 使用 MetaMask 签名
        const signature = await window.ethereum.request({
            method: 'personal_sign',
            params: [message, address],
        });

        console.log('签名完成');

        // 验证签名
        const verifyResponse = await apiRequest('/api/wallet/verify', {
            method: 'POST',
            body: JSON.stringify({
                session_id,
                address,
                message,
                signature,
            }),
        });

        if (verifyResponse.success) {
            // 保存状态
            state.wallet.connected = true;
            state.wallet.address = address;
            state.wallet.sessionId = session_id;

            // 保存到 localStorage
            localStorage.setItem('sessionId', session_id);
            localStorage.setItem('walletAddress', address);

            // 更新 UI
            updateWalletUI();

            // 连接 WebSocket
            connectWebSocket();

            // 加载用户网格
            loadUserGrids();

            showToast('钱包连接成功', 'success');
        }
    } catch (error) {
        console.error('连接钱包失败:', error);
        showToast(error.message || '连接失败', 'error');
    }
}

/**
 * 断开钱包连接
 */
async function disconnectWallet() {
    try {
        if (state.wallet.sessionId) {
            await apiRequest('/api/wallet/disconnect', { method: 'POST' });
        }
    } catch (e) {
        console.log('断开连接请求失败:', e);
    }

    // 清理状态
    state.wallet.connected = false;
    state.wallet.address = null;
    state.wallet.sessionId = null;

    // 清理 localStorage
    localStorage.removeItem('sessionId');
    localStorage.removeItem('walletAddress');

    // 断开 WebSocket
    if (state.ws) {
        state.ws.close();
        state.ws = null;
    }

    // 更新 UI
    updateWalletUI();

    showToast('已断开钱包连接', 'info');
}

/**
 * 更新钱包 UI
 */
function updateWalletUI() {
    const walletStatus = document.getElementById('walletStatus');
    const connectBtn = document.getElementById('connectWallet');
    const startGridBtn = document.getElementById('startGrid');

    if (state.wallet.connected) {
        // 更新状态指示
        walletStatus.innerHTML = `
            <div class="status-dot connected"></div>
            <span>${formatAddress(state.wallet.address)}</span>
        `;

        // 更新按钮
        connectBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 8a3 3 0 100-6 3 3 0 000 6zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
            </svg>
            ${formatAddress(state.wallet.address)}
        `;
        connectBtn.onclick = disconnectWallet;

        // 启用启动按钮
        startGridBtn.disabled = false;
    } else {
        // 重置状态
        walletStatus.innerHTML = `
            <div class="status-dot"></div>
            <span>未连接</span>
        `;

        connectBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M13.5 2h-11C1.67 2 1 2.67 1 3.5v9c0 .83.67 1.5 1.5 1.5h11c.83 0 1.5-.67 1.5-1.5v-9c0-.83-.67-1.5-1.5-1.5z"/>
            </svg>
            连接钱包
        `;
        connectBtn.onclick = () => document.getElementById('walletModal').classList.add('active');

        // 禁用启动按钮
        startGridBtn.disabled = true;
    }
}

/**
 * 恢复会话
 */
async function restoreSession() {
    const sessionId = localStorage.getItem('sessionId');
    const address = localStorage.getItem('walletAddress');

    if (sessionId && address) {
        try {
            // 验证会话是否有效
            state.wallet.sessionId = sessionId;
            const status = await apiRequest('/api/wallet/status');

            if (status.connected) {
                state.wallet.connected = true;
                state.wallet.address = address;
                updateWalletUI();
                connectWebSocket();
                loadUserGrids();
                return true;
            }
        } catch (e) {
            console.log('会话恢复失败:', e);
        }

        // 清理无效会话
        localStorage.removeItem('sessionId');
        localStorage.removeItem('walletAddress');
    }

    return false;
}

// ========== WebSocket 连接 ==========

/**
 * 连接 WebSocket
 */
function connectWebSocket() {
    if (!state.wallet.sessionId) return;

    // 根据页面协议自动选择 ws 或 wss
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${state.wallet.sessionId}`;

    try {
        state.ws = new WebSocket(wsUrl);

        state.ws.onopen = () => {
            console.log('WebSocket 已连接');
            // 订阅当前交易对行情
            subscribeToTicker(state.symbol);
        };

        state.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        };

        state.ws.onclose = () => {
            console.log('WebSocket 已断开');
            // 5 秒后尝试重连
            setTimeout(() => {
                if (state.wallet.connected) {
                    connectWebSocket();
                }
            }, 5000);
        };

        state.ws.onerror = (error) => {
            console.error('WebSocket 错误:', error);
        };
    } catch (e) {
        console.error('WebSocket 连接失败:', e);
    }
}

/**
 * 订阅行情
 */
function subscribeToTicker(symbol) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
            type: 'subscribe_ticker',
            symbol: symbol,
        }));
    }
}

/**
 * 处理 WebSocket 消息
 */
function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'ticker':
            updateTickerUI(data.data);
            break;
        case 'order_filled':
            showToast(`订单成交: ${data.side} ${data.quantity}@${data.price}`, 'success');
            loadUserGrids();
            break;
        case 'grid_updated':
            loadUserGrids();
            break;
    }
}

// ========== 行情更新 ==========

/**
 * 更新行情 UI
 */
function updateTickerUI(ticker) {
    state.ticker = ticker;

    const priceDisplay = document.getElementById('currentPrice');
    if (priceDisplay && ticker) {
        priceDisplay.innerHTML = `
            <span class="price-value">$${formatPrice(ticker.last_price)}</span>
        `;
    }

    // 更新网格预览
    updateGridPreview();
}

/**
 * 获取行情（轮询备用）
 */
async function fetchTicker() {
    try {
        const data = await apiRequest(`/api/market/ticker/${state.symbol}`);
        updateTickerUI(data);
    } catch (e) {
        console.error('获取行情失败:', e);
    }
}

// ========== 网格配置 ==========

/**
 * 更新网格预览
 */
function updateGridPreview() {
    const canvas = document.getElementById('gridCanvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    // 清空画布
    ctx.clearRect(0, 0, width, height);

    // 获取配置
    const lowerPrice = parseFloat(document.getElementById('lowerPrice').value) || 0;
    const upperPrice = parseFloat(document.getElementById('upperPrice').value) || 0;
    const gridCount = parseInt(document.getElementById('gridCount').value) || 10;
    const currentPrice = state.ticker?.last_price || 0;

    if (!lowerPrice || !upperPrice || lowerPrice >= upperPrice) {
        // 显示提示
        ctx.fillStyle = '#64748b';
        ctx.font = '14px Inter';
        ctx.textAlign = 'center';
        ctx.fillText('请设置价格区间', width / 2, height / 2);
        return;
    }

    // 绘制参数
    const padding = 40;
    const chartHeight = height - padding * 2;
    const chartWidth = width - padding * 2;

    // 价格到 Y 坐标的映射
    const priceToY = (price) => {
        const ratio = (price - lowerPrice) / (upperPrice - lowerPrice);
        return height - padding - ratio * chartHeight;
    };

    // 绘制背景
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(padding, padding, chartWidth, chartHeight);

    // 绘制网格线
    const priceStep = (upperPrice - lowerPrice) / (gridCount - 1);

    for (let i = 0; i < gridCount; i++) {
        const price = lowerPrice + i * priceStep;
        const y = priceToY(price);

        // 网格线
        ctx.strokeStyle = i % 2 === 0 ? '#334155' : '#2d3a4d';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding, y);
        ctx.lineTo(width - padding, y);
        ctx.stroke();

        // 价格标签
        ctx.fillStyle = '#94a3b8';
        ctx.font = '10px Inter';
        ctx.textAlign = 'right';
        ctx.fillText(formatPrice(price, 0), padding - 5, y + 3);
    }

    // 绘制当前价格线
    if (currentPrice >= lowerPrice && currentPrice <= upperPrice) {
        const currentY = priceToY(currentPrice);

        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.moveTo(padding, currentY);
        ctx.lineTo(width - padding, currentY);
        ctx.stroke();
        ctx.setLineDash([]);

        // 当前价格标签
        ctx.fillStyle = '#10b981';
        ctx.font = 'bold 11px Inter';
        ctx.textAlign = 'left';
        ctx.fillText(`$${formatPrice(currentPrice, 0)}`, width - padding + 5, currentY + 3);
    }

    // 绘制买卖区域
    if (currentPrice > 0) {
        const currentY = priceToY(Math.min(Math.max(currentPrice, lowerPrice), upperPrice));

        // 买入区域（下方）
        ctx.fillStyle = 'rgba(16, 185, 129, 0.1)';
        ctx.fillRect(padding, currentY, chartWidth, height - padding - currentY);

        // 卖出区域（上方）
        ctx.fillStyle = 'rgba(239, 68, 68, 0.1)';
        ctx.fillRect(padding, padding, chartWidth, currentY - padding);
    }

    // 更新统计
    const gridSpacing = document.getElementById('gridSpacing');
    const profitPerGrid = document.getElementById('profitPerGrid');

    if (gridSpacing) {
        const spacing = ((upperPrice - lowerPrice) / (gridCount - 1) / lowerPrice * 100).toFixed(2);
        gridSpacing.textContent = `${spacing}%`;
    }

    if (profitPerGrid) {
        // 简单估算每格利润
        const perGridAmount = parseFloat(document.getElementById('perGridAmount').value) || 100;
        const spacing = (upperPrice - lowerPrice) / (gridCount - 1);
        const avgPrice = (upperPrice + lowerPrice) / 2;
        const profit = (spacing / avgPrice * perGridAmount * 0.9996).toFixed(2); // 扣除手续费
        profitPerGrid.textContent = `$${profit}`;
    }
}

/**
 * 计算总投资
 */
function updateTotalInvestment() {
    const gridCount = parseInt(document.getElementById('gridCount').value) || 10;
    const perGridAmount = parseFloat(document.getElementById('perGridAmount').value) || 100;

    const total = gridCount * perGridAmount;
    document.getElementById('totalInvestment').textContent = formatPrice(total, 0);
}

/**
 * 更新风险等级
 */
function updateRiskLevel() {
    const leverage = state.leverage;
    const riskBadge = document.getElementById('riskBadge');

    if (leverage <= 2) {
        riskBadge.textContent = '低';
        riskBadge.className = 'badge low';
    } else if (leverage <= 5) {
        riskBadge.textContent = '中';
        riskBadge.className = 'badge medium';
    } else {
        riskBadge.textContent = '高';
        riskBadge.className = 'badge high';
    }
}

// ========== 网格交易操作 ==========

/**
 * 启动网格交易
 */
async function startGrid() {
    if (!state.wallet.connected) {
        showToast('请先连接钱包', 'warning');
        return;
    }

    // 收集配置
    const config = {
        symbol: document.getElementById('symbolSelect').value,
        mode: state.mode,
        lower_price: parseFloat(document.getElementById('lowerPrice').value),
        upper_price: parseFloat(document.getElementById('upperPrice').value),
        grid_count: parseInt(document.getElementById('gridCount').value),
        per_grid_amount: parseFloat(document.getElementById('perGridAmount').value),
        leverage: state.leverage,
        stop_loss_percent: parseFloat(document.getElementById('stopLoss').value) || null,
        take_profit_percent: parseFloat(document.getElementById('takeProfit').value) || null,
    };

    // 验证
    if (!config.lower_price || !config.upper_price) {
        showToast('请设置价格区间', 'warning');
        return;
    }

    if (config.lower_price >= config.upper_price) {
        showToast('下限价格必须小于上限价格', 'warning');
        return;
    }

    try {
        const result = await apiRequest('/api/grid/create', {
            method: 'POST',
            body: JSON.stringify(config),
        });

        showToast(`网格 #${result.grid_id} 启动成功`, 'success');

        // 刷新网格列表
        loadUserGrids();

    } catch (error) {
        showToast(error.message || '启动网格失败', 'error');
    }
}

/**
 * 加载用户网格列表
 */
async function loadUserGrids() {
    if (!state.wallet.connected) return;

    try {
        const result = await apiRequest('/api/grid/list');
        state.grids = result.grids || [];

        // 渲染网格列表
        renderGridList();

    } catch (error) {
        console.error('加载网格列表失败:', error);
    }
}

/**
 * 渲染网格列表
 */
function renderGridList() {
    const gridList = document.getElementById('gridList');
    if (!gridList) return;

    if (state.grids.length === 0) {
        gridList.innerHTML = '<div class="grid-empty">暂无网格</div>';
        return;
    }

    gridList.innerHTML = state.grids.map(grid => `
        <div class="grid-item ${grid.status}">
            <div class="grid-item-header">
                <span class="grid-symbol">${grid.symbol}</span>
                <span class="grid-status ${grid.status}">${getStatusText(grid.status)}</span>
            </div>
            <div class="grid-item-info">
                <div class="grid-info-row">
                    <span>区间:</span>
                    <span>$${formatPrice(grid.lower_price)} - $${formatPrice(grid.upper_price)}</span>
                </div>
                <div class="grid-info-row">
                    <span>网格数:</span>
                    <span>${grid.grid_count} 格 × ${grid.per_grid_amount} DUSD</span>
                </div>
                <div class="grid-info-row">
                    <span>盈亏:</span>
                    <span class="${grid.realized_pnl >= 0 ? 'profit' : 'loss'}">${grid.realized_pnl >= 0 ? '+' : ''}${grid.realized_pnl.toFixed(2)} DUSD</span>
                </div>
            </div>
            <div class="grid-item-actions">
                ${grid.status === 'running' ? `<button class="btn-sm btn-warning" onclick="stopGrid(${grid.id})">停止</button>` : ''}
                ${grid.status !== 'running' ? `<button class="btn-sm btn-danger" onclick="deleteGrid(${grid.id})">删除</button>` : ''}
            </div>
        </div>
    `).join('');
}

/**
 * 获取状态文本
 */
function getStatusText(status) {
    const statusMap = {
        'running': '运行中',
        'stopped': '已停止',
        'paused': '已暂停',
        'completed': '已完成',
        'pending': '等待中'
    };
    return statusMap[status] || status;
}

/**
 * 停止网格
 */
async function stopGrid(gridId) {
    try {
        await apiRequest(`/api/grid/${gridId}/stop`, { method: 'POST' });
        showToast('网格已停止', 'success');
        loadUserGrids();
    } catch (error) {
        showToast(error.message || '停止网格失败', 'error');
    }
}

/**
 * 删除网格
 */
async function deleteGrid(gridId) {
    if (!confirm('确定要删除这个网格吗？')) return;

    try {
        await apiRequest(`/api/grid/${gridId}`, { method: 'DELETE' });
        showToast('网格已删除', 'success');
        loadUserGrids();
    } catch (error) {
        showToast(error.message || '删除网格失败', 'error');
    }
}

// ========== API 配置 ==========

/**
 * 打开 API 配置模态框
 */
function openApiConfigModal() {
    const modal = document.getElementById('apiConfigModal');
    const jwtInput = document.getElementById('jwtTokenInput');
    const testResult = document.getElementById('apiTestResult');
    const tokenStatus = document.getElementById('getTokenStatus');

    // 恢复已保存的配置
    if (state.apiConfig.jwtToken) {
        jwtInput.value = state.apiConfig.jwtToken;
    }

    // 隐藏测试结果和 token 状态
    testResult.style.display = 'none';
    if (tokenStatus) {
        tokenStatus.style.display = 'none';
    }

    // 更新签名密钥状态
    updateSigningKeyStatus();

    modal.classList.add('active');
}

/**
 * 关闭 API 配置模态框
 */
function closeApiConfigModal() {
    document.getElementById('apiConfigModal').classList.remove('active');
}

/**
 * 测试 API 连接
 */
async function testApiConnection() {
    const jwtToken = document.getElementById('jwtTokenInput').value.trim();
    const testBtn = document.getElementById('testApiBtn');
    const testResult = document.getElementById('apiTestResult');

    if (!jwtToken) {
        showToast('请输入 JWT Token', 'warning');
        return;
    }

    // 显示加载状态
    testBtn.classList.add('btn-loading');
    testBtn.textContent = '测试中...';
    testResult.style.display = 'none';

    try {
        // 包含签名密钥（如果有）
        const requestBody = {
            jwt_token: jwtToken,
            signing_key: state.apiConfig.signingKey,
            request_id: state.apiConfig.requestId,
        };

        const result = await apiRequest('/api/config/test', {
            method: 'POST',
            body: JSON.stringify(requestBody),
        });

        // 显示成功结果
        const hasKey = result.has_signing_key;
        testResult.innerHTML = `
            <div class="test-success">
                <span>✅ API 连接成功</span>
                <span class="balance-info">账户余额: ${result.balance || '--'} DUSD</span>
                ${hasKey ?
                    '<span class="signing-key-info">🔑 已配置签名密钥，可以下单</span>' :
                    '<span class="signing-key-warning">⚠️ 未配置签名密钥，无法下单</span>'
                }
            </div>
        `;
        testResult.style.display = 'block';

    } catch (error) {
        // 显示错误结果
        testResult.innerHTML = `
            <div class="test-error">
                <span>❌ 连接失败</span>
                <span class="balance-info">${error.message || '请检查 JWT Token 是否正确'}</span>
            </div>
        `;
        testResult.style.display = 'block';
    } finally {
        testBtn.classList.remove('btn-loading');
        testBtn.textContent = '测试连接';
    }
}

/**
 * 保存 API 配置
 */
async function saveApiConfig() {
    const jwtToken = document.getElementById('jwtTokenInput').value.trim();
    const saveBtn = document.getElementById('saveApiBtn');

    if (!jwtToken) {
        showToast('请输入 JWT Token', 'warning');
        return;
    }

    saveBtn.classList.add('btn-loading');
    saveBtn.textContent = '保存中...';

    // 如果没有签名密钥，自动生成
    if (!state.apiConfig.signingKey || !state.apiConfig.requestId) {
        saveBtn.textContent = '生成签名密钥...';
        const keyGenerated = await ensureSigningKey();
        if (!keyGenerated) {
            showToast('生成签名密钥失败，请重试', 'error');
            saveBtn.classList.remove('btn-loading');
            saveBtn.textContent = '保存配置';
            return;
        }
    }

    try {
        await apiRequest('/api/config/api', {
            method: 'POST',
            body: JSON.stringify({
                jwt_token: jwtToken,
                signing_key: state.apiConfig.signingKey,
                request_id: state.apiConfig.requestId,
                simulation_mode: false,  // 始终使用真实交易模式
            }),
        });

        // 更新状态
        state.apiConfig.jwtToken = jwtToken;
        state.apiConfig.simulationMode = false;
        state.apiConfig.isConfigured = true;

        // 保存到 localStorage
        localStorage.setItem('apiJwtToken', jwtToken);
        localStorage.setItem('apiSigningKey', state.apiConfig.signingKey);
        localStorage.setItem('apiRequestId', state.apiConfig.requestId);

        // 更新 UI
        updateApiStatusUI();

        showToast('API 配置已保存，已启用真实交易', 'success');
        closeApiConfigModal();

    } catch (error) {
        showToast(error.message || '保存配置失败', 'error');
    } finally {
        saveBtn.classList.remove('btn-loading');
        saveBtn.textContent = '保存配置';
    }
}

/**
 * 更新 API 状态 UI
 */
function updateApiStatusUI() {
    const apiStatus = document.getElementById('apiStatus');

    if (state.apiConfig.isConfigured) {
        apiStatus.innerHTML = `
            <span class="api-mode live">真实交易</span>
            <span class="api-hint">已连接 StandX API</span>
        `;
    } else {
        apiStatus.innerHTML = `
            <span class="api-mode not-configured">未配置</span>
            <span class="api-hint">请先配置 JWT Token</span>
        `;
    }
}

/**
 * 恢复 API 配置
 */
function restoreApiConfig() {
    const jwtToken = localStorage.getItem('apiJwtToken');
    const signingKey = localStorage.getItem('apiSigningKey');
    const requestId = localStorage.getItem('apiRequestId');

    if (jwtToken) {
        state.apiConfig.jwtToken = jwtToken;
        state.apiConfig.isConfigured = true;
    }

    if (signingKey) {
        state.apiConfig.signingKey = signingKey;
    }

    if (requestId) {
        state.apiConfig.requestId = requestId;
    }

    // 始终使用真实交易模式
    state.apiConfig.simulationMode = false;

    updateApiStatusUI();
}

/**
 * 更新签名密钥状态 UI
 */
function updateSigningKeyStatus() {
    const statusEl = document.getElementById('signingKeyStatus');
    if (!statusEl) return;

    if (state.apiConfig.signingKey && state.apiConfig.requestId) {
        statusEl.innerHTML = `
            <span class="key-status-ready">🔑 签名密钥已就绪 (Request ID: ${state.apiConfig.requestId.slice(0, 8)}...)</span>
        `;
    } else {
        statusEl.innerHTML = `
            <span class="key-status-pending">⏳ 点击上方按钮获取 JWT Token 时会自动生成签名密钥</span>
        `;
    }
}

/**
 * 生成签名密钥（如果不存在）
 */
async function ensureSigningKey() {
    if (state.apiConfig.signingKey && state.apiConfig.requestId) {
        return true;
    }

    try {
        const keypairResponse = await apiRequest('/api/config/generate-keypair');
        if (!keypairResponse.success) {
            throw new Error('生成密钥对失败');
        }

        state.apiConfig.signingKey = keypairResponse.private_key;
        state.apiConfig.requestId = keypairResponse.request_id;

        console.log('自动生成 Ed25519 密钥对, requestId:', state.apiConfig.requestId);

        // 更新 UI
        updateSigningKeyStatus();

        return true;
    } catch (error) {
        console.error('生成签名密钥失败:', error);
        return false;
    }
}

/**
 * 获取 JWT Token
 *
 * 根据 StandX 官方文档实现：
 * https://docs.standx.com/standx-api/perps-auth
 *
 * 流程：
 * 1. 生成 Ed25519 密钥对
 * 2. prepare-signin 获取签名消息
 * 3. MetaMask 签名
 * 4. login 获取 JWT Token（包含公钥）
 */
async function getJwtToken() {
    const btn = document.getElementById('getJwtTokenBtn');
    const status = document.getElementById('getTokenStatus');
    const tokenInput = document.getElementById('jwtTokenInput');

    // 检查钱包是否连接
    if (!isMetaMaskInstalled()) {
        showToast('请先安装 MetaMask', 'error');
        return;
    }

    // 获取钱包地址
    let walletAddress;
    try {
        const accounts = await window.ethereum.request({
            method: 'eth_requestAccounts'
        });
        if (!accounts || accounts.length === 0) {
            showToast('请先连接 MetaMask 钱包', 'warning');
            return;
        }
        walletAddress = accounts[0];
    } catch (e) {
        showToast('连接钱包失败: ' + e.message, 'error');
        return;
    }

    // 切换到 BSC 网络（StandX 要求 chainId: 56）
    const BSC_CHAIN_ID = '0x38'; // 56 in hex
    try {
        const currentChainId = await window.ethereum.request({ method: 'eth_chainId' });
        if (currentChainId !== BSC_CHAIN_ID) {
            console.log('当前链:', currentChainId, '需要切换到 BSC (0x38)');
            try {
                // 尝试切换到 BSC
                await window.ethereum.request({
                    method: 'wallet_switchEthereumChain',
                    params: [{ chainId: BSC_CHAIN_ID }]
                });
            } catch (switchError) {
                // 如果 BSC 网络未添加，尝试添加
                if (switchError.code === 4902) {
                    await window.ethereum.request({
                        method: 'wallet_addEthereumChain',
                        params: [{
                            chainId: BSC_CHAIN_ID,
                            chainName: 'BNB Smart Chain',
                            nativeCurrency: {
                                name: 'BNB',
                                symbol: 'BNB',
                                decimals: 18
                            },
                            rpcUrls: ['https://bsc-dataseed.binance.org/'],
                            blockExplorerUrls: ['https://bscscan.com/']
                        }]
                    });
                } else {
                    throw switchError;
                }
            }
            console.log('已切换到 BSC 网络');
        }
    } catch (e) {
        showToast('切换到 BSC 网络失败: ' + e.message, 'error');
        return;
    }

    // 显示加载状态
    btn.classList.add('btn-loading');
    btn.disabled = true;
    status.className = 'get-token-status loading';
    status.textContent = '⏳ 步骤 1/4: 生成 Ed25519 密钥对...';
    status.style.display = 'block';

    const STANDX_API = 'https://api.standx.com/v1/offchain';
    const CHAIN = 'bsc';

    try {
        // Step 1: 生成 Ed25519 密钥对
        const keypairResponse = await apiRequest('/api/config/generate-keypair');
        if (!keypairResponse.success) {
            throw new Error('生成密钥对失败');
        }

        const signingKey = keypairResponse.private_key;
        const requestId = keypairResponse.request_id;

        console.log('已生成 Ed25519 密钥对, requestId:', requestId);

        // 保存到状态
        state.apiConfig.signingKey = signingKey;
        state.apiConfig.requestId = requestId;

        // Step 2: 调用 prepare-signin
        status.textContent = '⏳ 步骤 2/4: 调用 prepare-signin...';

        const prepareResponse = await fetch(`${STANDX_API}/prepare-signin?chain=${CHAIN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                address: walletAddress,
                requestId: requestId  // 使用 Ed25519 公钥作为 requestId
            })
        });

        if (!prepareResponse.ok) {
            throw new Error(`prepare-signin 失败: ${await prepareResponse.text()}`);
        }

        const prepareData = await prepareResponse.json();
        console.log('prepare-signin 响应:', prepareData);

        const signedData = prepareData.signedData;
        if (!signedData) {
            throw new Error('未获取到 signedData');
        }

        // signedData 是一个 JWT，需要解析获取其中的 message
        // JWT 格式: header.payload.signature
        const jwtParts = signedData.split('.');
        if (jwtParts.length !== 3) {
            throw new Error('signedData 格式错误');
        }

        // 解析 payload (base64url 解码)
        const payloadBase64 = jwtParts[1];
        const payloadJson = atob(payloadBase64.replace(/-/g, '+').replace(/_/g, '/'));
        const payload = JSON.parse(payloadJson);
        console.log('JWT payload:', payload);

        // 获取需要签名的消息
        const messageToSign = payload.message;
        if (!messageToSign) {
            throw new Error('未找到待签名消息');
        }

        console.log('待签名消息:', messageToSign);

        // Step 3: 使用 MetaMask 签名 SIWE 消息
        status.textContent = '⏳ 步骤 3/4: 请在 MetaMask 中签名...';

        // SIWE 标准 (EIP-4361) 要求直接签名原始消息字符串
        // 不需要转换为 hex，MetaMask personal_sign 会自动处理
        // 参考: https://docs.siwe.xyz/ 和 https://eips.ethereum.org/EIPS/eip-4361
        console.log('签名 SIWE 消息:', messageToSign);

        const signature = await window.ethereum.request({
            method: 'personal_sign',
            params: [messageToSign, walletAddress]
        });

        console.log('签名完成:', signature);

        // Step 4: 调用 login 获取 JWT Token
        status.textContent = '⏳ 步骤 4/4: 获取 JWT Token...';

        // 根据 StandX 官方文档构建登录请求
        // https://docs.standx.com/standx-api/perps-auth
        const loginBody = {
            signedData: signedData,    // prepare-signin 返回的 JWT (base64)
            signature: signature,       // MetaMask 签名 (0x...)
            expiresSeconds: 604800      // 7 天有效期
        };
        console.log('login 请求体:', loginBody);
        console.log('signedData 前20字符:', signedData.substring(0, 50));
        console.log('signature:', signature);

        const loginResponse = await fetch(`${STANDX_API}/login?chain=${CHAIN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(loginBody)
        });

        if (!loginResponse.ok) {
            const errorText = await loginResponse.text();
            console.error('login 失败:', errorText);
            throw new Error(`login 失败: ${errorText}`);
        }

        const loginData = await loginResponse.json();
        console.log('login 响应:', loginData);

        const jwtToken = loginData.token;
        if (!jwtToken) {
            throw new Error('未获取到 token');
        }

        // 成功！填入 Token
        tokenInput.value = jwtToken;
        status.className = 'get-token-status success';
        status.innerHTML = `
            ✅ JWT Token 获取成功！已自动填入下方输入框<br>
            🔑 已生成签名密钥 (Request ID: ${requestId.slice(0, 8)}...)
        `;

        // 更新签名密钥状态 UI
        updateSigningKeyStatus();

        showToast('JWT Token 和签名密钥获取成功！', 'success');

    } catch (error) {
        console.error('获取 Token 错误:', error);
        status.className = 'get-token-status error';
        status.textContent = '❌ ' + error.message;
        showToast('获取 Token 失败: ' + error.message, 'error');
    } finally {
        btn.classList.remove('btn-loading');
        btn.disabled = false;
    }
}

// ========== 事件绑定 ==========

document.addEventListener('DOMContentLoaded', () => {
    // 恢复会话
    restoreSession();

    // 恢复 API 配置
    restoreApiConfig();

    // 定时获取行情（WebSocket 的备用方案）
    setInterval(fetchTicker, 5000);
    fetchTicker();

    // 钱包连接按钮
    document.getElementById('connectWallet').addEventListener('click', () => {
        if (!state.wallet.connected) {
            document.getElementById('walletModal').classList.add('active');
        }
    });

    // MetaMask 连接按钮
    document.getElementById('connectMetaMask').addEventListener('click', async () => {
        document.getElementById('walletModal').classList.remove('active');
        await connectMetaMask();
    });

    // API 配置按钮
    document.getElementById('configApiBtn').addEventListener('click', openApiConfigModal);
    document.getElementById('testApiBtn').addEventListener('click', testApiConnection);
    document.getElementById('saveApiBtn').addEventListener('click', saveApiConfig);

    // 交易对选择
    document.getElementById('symbolSelect').addEventListener('change', (e) => {
        state.symbol = e.target.value;
        // 重新订阅行情
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            subscribeToTicker(state.symbol);
        }
        fetchTicker();
    });

    // 网格模式选择
    document.querySelectorAll('.mode-card').forEach(card => {
        card.addEventListener('click', () => {
            document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('active'));
            card.classList.add('active');
            state.mode = card.dataset.mode;
        });
    });

    // 杠杆选择
    document.querySelectorAll('.lev-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.lev-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.leverage = parseInt(btn.dataset.leverage);
            updateRiskLevel();
        });
    });

    // 价格和网格数量输入
    ['lowerPrice', 'upperPrice', 'gridCount', 'perGridAmount'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', () => {
                updateGridPreview();
                updateTotalInvestment();
            });
        }
    });

    // 高级设置折叠
    document.getElementById('advancedToggle').addEventListener('click', () => {
        const section = document.querySelector('.collapsible');
        section.classList.toggle('open');
    });

    // 启动网格按钮
    document.getElementById('startGrid').addEventListener('click', startGrid);

    // 监听 MetaMask 账户变化
    if (window.ethereum) {
        window.ethereum.on('accountsChanged', (accounts) => {
            if (accounts.length === 0) {
                disconnectWallet();
            } else if (accounts[0] !== state.wallet.address) {
                disconnectWallet();
                showToast('账户已切换，请重新连接', 'warning');
            }
        });
    }

    // 初始化 UI
    updateWalletUI();
    updateRiskLevel();
    updateTotalInvestment();
});
