from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import requests
import json
import base64
import hashlib
import time
import os
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import unpad

app = FastAPI()

# 从环境变量读取（你稍后在 Vercel 里填，现在先不动）
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY")
KIMI_API_KEY = os.getenv("KIMI_API_KEY")

KIMI_BASE_URL = "https://api.moonshot.cn/v1"

# ===== 豆选 + MaxMa System Prompt（根据咱们之前的约定） =====
SYSTEM_PROMPT = """你是心知能源的【首席政策研究室主任】，底层 OS 为【豆选】文风，运行【MaxMa】思维模型。

【豆选文风特征】：
- 用犀利、反直觉的比喻切入电力市场本质
- 将政策条文翻译为利益博弈图谱，拒绝"正确的废话"
- 常用句式："X不是Y，而是Z"的重新定义结构
- 核心隐喻：帕累托剩余、维度折叠、绞杀器、套利空间等
- 冷静、锋利、第三方观察者视角，避免血腥与死亡类词汇，改用"出清""淘汰""剩余再分配"等商业术语

【MaxMa 思维模型】：
- 第一性原理（马斯克层）：穿透政策条文看物理约束（供需、网络、燃料）
- 护城河思维（巴菲特层）：评估商业模式的复利效应与风险不对称性
- 所有分析必须同时回答：物理层是否可行？商业层是否可持续？

输出要求：简洁、结构化、专业，必要时使用 Markdown 表格或列表。"""

# ===== 飞书加密/解密工具 =====
def decrypt_feishu_message(encrypt_key, encrypt_msg):
    """解密飞书推送的消息"""
    try:
        key = hashlib.sha256(encrypt_key.encode('utf-8')).digest()[:32]
        cipher = AES.new(key, AES.MODE_CBC, iv=b'0000000000000000')
        decrypted = unpad(cipher.decrypt(base64.b64decode(encrypt_msg)), AES.block_size)
        return json.loads(decrypted.decode('utf-8'))
    except Exception as e:
        print(f"解密失败: {e}")
        return None

# ===== Kimi 调用 =====
def call_kimi(user_text: str, chat_context: list = None) -> str:
    """调用 Kimi K2.5"""
    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    if chat_context:
        messages.extend(chat_context)
    messages.append({"role": "user", "content": user_text})
    
    payload = {
        "model": "kimi-k2-5",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4000
    }
    
    try:
        resp = requests.post(
            f"{KIMI_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Kimi 调用失败: {e}")
        return f"服务暂时异常: {str(e)}"

# ===== 飞书 Token 管理（简单版，实际生产建议缓存） =====
def get_feishu_token():
    """获取 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    })
    return resp.json().get("tenant_access_token")

def send_feishu_reply(chat_id, text, msg_type="text"):
    """回复消息"""
    token = get_feishu_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # 如果内容太长，截断或分段（飞书单条限制 8000 字符）
    if len(text) > 7900:
        text = text[:7900] + "\n\n...[内容过长，已截断]"
    
    params = {"receive_id_type": "chat_id"}
    content = json.dumps({"text": text})
    
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": content
    }
    
    resp = requests.post(url, headers=headers, params=params, json=body)
    return resp.json()

# ===== Webhook 入口 =====
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    
    # 1. 处理飞书首次配置时的 Challenge 验证
    if "challenge" in data:
        return JSONResponse({"challenge": data["challenge"]})
    
    # 2. 处理加密消息（如果你开启了加密）
    encrypt_msg = data.get("encrypt")
    if encrypt_msg and FEISHU_ENCRYPT_KEY:
        decrypted_data = decrypt_feishu_message(FEISHU_ENCRYPT_KEY, encrypt_msg)
        if decrypted_data:
            data = decrypted_data
    
    # 3. 处理业务消息
    event = data.get("event", {})
    if event.get("type") == "im.message.receive_v1":
        message = event.get("message", {})
        content_data = json.loads(message.get("content", "{}"))
        
        # 提取纯文本
        user_text = content_data.get("text", "")
        chat_id = message.get("chat_id")
        
        # 可选：@机器人时去掉 @部分
        mentions = content_data.get("mentions", [])
        for mention in mentions:
            user_text = user_text.replace(mention.get("key", ""), "").strip()
        
        print(f"收到消息: {user_text}")
        
        # 调用 Kimi
        reply_text = call_kimi(user_text)
        
        # 回复
        send_feishu_reply(chat_id, reply_text)
    
    return JSONResponse({"status": "ok"})

@app.get("/")
async def root():
    return {"message": "心知能源飞书机器人服务运行中", "model": "kimi-k2-5"}
