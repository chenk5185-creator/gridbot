# 网格交易工具 - 安全架构设计

## 🔐 安全原则

### 1. 私钥零暴露原则
**绝不在任何情况下传输或存储私钥**

```
✅ 安全做法：
- 前端：使用 MetaMask 等钱包签名
- 后端：只接收签名结果验证
- 存储：只存储地址和 session token

❌ 危险做法：
- 在配置文件存储私钥
- 通过 API 传输私钥
- 在后端存储私钥
```

### 2. 分层安全架构

```
┌─────────────────────────────────────────┐
│         前端 (浏览器)                    │
│  - MetaMask 钱包连接                     │
│  - 本地签名（私钥不离开浏览器）            │
│  - Session Token 管理                    │
└─────────────┬───────────────────────────┘
              │ HTTPS
              │ 只传输: 地址、签名、Token
              ▼
┌─────────────────────────────────────────┐
│         后端 API 服务器                  │
│  - 验证签名                              │
│  - Session 管理                          │
│  - 业务逻辑                              │
│  ❌ 不存储私钥                           │
└─────────────┬───────────────────────────┘
              │ API Key (not private key)
              ▼
┌─────────────────────────────────────────┐
│         交易所 API                       │
│  - 使用 API Key + Secret                │
│  - 或使用签名授权                        │
└─────────────────────────────────────────┘
```

## 🛡️ 安全实现方案

### 方案 1: MetaMask 连接（推荐）

#### 前端流程
```javascript
// 1. 连接 MetaMask
async function connectMetaMask() {
    const accounts = await window.ethereum.request({
        method: 'eth_requestAccounts'
    });
    const address = accounts[0];
    
    // 2. 获取待签名消息
    const response = await fetch('/api/wallet/connect', {
        method: 'POST',
        body: JSON.stringify({ address })
    });
    const { message, sessionId } = await response.json();
    
    // 3. 使用 MetaMask 签名（私钥不离开浏览器）
    const signature = await window.ethereum.request({
        method: 'personal_sign',
        params: [message, address]
    });
    
    // 4. 提交签名验证
    await fetch('/api/wallet/verify', {
        method: 'POST',
        body: JSON.stringify({ sessionId, address, signature })
    });
    
    // 5. 存储 session token（不存储私钥）
    localStorage.setItem('sessionToken', sessionId);
}
```

#### 后端流程
```python
# 1. 生成签名消息
@app.route('/api/wallet/connect', methods=['POST'])
def connect_wallet():
    address = request.json['address']
    session_id = generate_session_id()
    message = f"Sign to connect: {session_id}"
    
    # 存储待验证的 session
    pending_sessions[session_id] = {
        'address': address,
        'message': message,
        'timestamp': time.time()
    }
    
    return {
        'sessionId': session_id,
        'message': message
    }

# 2. 验证签名
@app.route('/api/wallet/verify', methods=['POST'])
def verify_wallet():
    session_id = request.json['sessionId']
    address = request.json['address']
    signature = request.json['signature']
    
    # 验证签名（不需要私钥）
    session = pending_sessions.get(session_id)
    if not session:
        return {'error': 'Invalid session'}, 400
    
    # 使用 eth_account 验证签名
    from eth_account.messages import encode_defunct
    from eth_account import Account
    
    message_hash = encode_defunct(text=session['message'])
    recovered_address = Account.recover_message(
        message_hash, 
        signature=signature
    )
    
    if recovered_address.lower() != address.lower():
        return {'error': 'Invalid signature'}, 401
    
    # 创建已验证的 session
    active_sessions[session_id] = {
        'address': address,
        'verified': True,
        'timestamp': time.time()
    }
    
    del pending_sessions[session_id]
    
    return {'success': True, 'sessionId': session_id}
```

### 方案 2: API Key 模式（服务端）

#### 使用 API Key 而非私钥
```yaml
# ✅ 安全配置
exchange:
  name: standx
  api_key: "your-api-key"
  api_secret: "your-api-secret"  # 用于签名，不是私钥

# ❌ 不安全配置
wallet:
  private_key: "0x..."  # 永远不要这样做！
```

#### API Key 签名流程
```python
import hmac
import hashlib
import time

def sign_request(api_secret, method, endpoint, params):
    """使用 API Secret 签名请求"""
    timestamp = str(int(time.time() * 1000))
    message = f"{method}{endpoint}{timestamp}{json.dumps(params)}"
    
    signature = hmac.new(
        api_secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return {
        'X-API-Key': api_key,
        'X-Signature': signature,
        'X-Timestamp': timestamp
    }
```

## 🔑 Session 管理

### Session Token 结构
```python
{
    'session_id': 'uuid-v4',
    'address': '0x...',
    'verified': True,
    'created_at': timestamp,
    'expires_at': timestamp + 3600,  # 1小时后过期
    'permissions': ['read', 'trade']
}
```

### Session 安全措施
```python
class SessionManager:
    def __init__(self):
        self.sessions = {}
        self.max_age = 3600  # 1小时
    
    def create_session(self, address):
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            'address': address,
            'created_at': time.time(),
            'expires_at': time.time() + self.max_age
        }
        return session_id
    
    def validate_session(self, session_id):
        session = self.sessions.get(session_id)
        if not session:
            return False
        
        # 检查是否过期
        if time.time() > session['expires_at']:
            del self.sessions[session_id]
            return False
        
        return True
    
    def cleanup_expired(self):
        """清理过期 session"""
        now = time.time()
        expired = [
            sid for sid, s in self.sessions.items()
            if now > s['expires_at']
        ]
        for sid in expired:
            del self.sessions[sid]
```

## 🌐 HTTPS 强制

### Nginx 配置
```nginx
server {
    listen 80;
    server_name yourdomain.com;
    
    # 强制跳转 HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    # 安全头部
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 🔒 环境变量管理

### .env 文件（本地开发）
```bash
# .env
API_KEY=your-api-key
API_SECRET=your-api-secret
SESSION_SECRET=random-secret-key
ENVIRONMENT=development
```

### 生产环境（使用平台 Secrets）
```bash
# Railway / Vercel / Render
# 在平台 Dashboard 设置环境变量
API_KEY=***
API_SECRET=***
SESSION_SECRET=***
```

### 代码中读取
```python
import os
from dotenv import load_dotenv

load_dotenv()  # 加载 .env 文件

API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')

# 永远不要硬编码
# API_KEY = "abc123"  ❌
```

## 🛡️ 安全检查清单

### 部署前检查
- [ ] 所有敏感信息使用环境变量
- [ ] .gitignore 包含 .env, config.yaml
- [ ] 启用 HTTPS
- [ ] 实现 Session 过期机制
- [ ] 添加请求频率限制
- [ ] 验证所有用户输入
- [ ] 实现 CSRF 保护

### 代码审查
- [ ] 没有硬编码的密钥
- [ ] 没有 console.log 敏感信息
- [ ] 使用参数化查询防止注入
- [ ] 正确处理错误（不泄露信息）
- [ ] 实现适当的权限检查

### 运行时监控
- [ ] 记录异常登录尝试
- [ ] 监控 API 调用频率
- [ ] 设置异常交易告警
- [ ] 定期清理过期 session
- [ ] 备份重要数据

## 📊 安全最佳实践对比

| 方面 | ❌ 不安全 | ✅ 安全 |
|------|----------|---------|
| **私钥存储** | 存储在配置文件 | 永不存储，使用钱包签名 |
| **认证** | 传输私钥 | 传输签名结果 |
| **Session** | 永久有效 | 1小时过期，可刷新 |
| **通信** | HTTP | HTTPS |
| **配置** | 硬编码 | 环境变量 |
| **权限** | 全部权限 | 最小权限原则 |
| **日志** | 记录敏感信息 | 脱敏后记录 |

## 🚨 应急响应

### 发现密钥泄露时
1. 立即撤销泄露的 API Key
2. 生成新的 API Key
3. 更新所有环境变量
4. 检查是否有异常交易
5. 通知用户（如果影响用户）

### 发现异常活动时
1. 暂停可疑 session
2. 记录详细日志
3. 分析攻击模式
4. 加强安全措施
5. 通知相关方

## 📚 相关资源

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Web3 安全最佳实践](https://consensys.github.io/smart-contract-best-practices/)
- [MetaMask 开发文档](https://docs.metamask.io/)
- [EIP-191: 签名数据标准](https://eips.ethereum.org/EIPS/eip-191)

---

**记住：安全是一个持续的过程，不是一次性的任务！**
