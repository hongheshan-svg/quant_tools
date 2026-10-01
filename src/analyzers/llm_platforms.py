"""
大模型平台预设（Web 设置页和大模型客户端使用）。
anthropic、gemini、ollama 走 LiteLLM 的原生通道（base_url 可留空，ollama 填本机服务地址），
其余平台按 OpenAI 兼容协议调用各自的 base_url。
"""

from __future__ import annotations

AI_PLATFORMS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek (深度求索)",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "name": "通义千问 (Qwen)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen-long"],
        "default_model": "qwen-plus",
    },
    "zhipu": {
        "name": "智谱GLM (ChatGLM)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-flash", "glm-4", "glm-4-plus", "glm-4-long"],
        "default_model": "glm-4-flash",
    },
    "moonshot": {
        "name": "月之暗面 (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_model": "moonshot-v1-8k",
    },
    "baidu": {
        "name": "百度文心 (ERNIE)",
        "base_url": "https://qianfan.baidubce.com/v2",
        "models": ["ernie-4.0-8k", "ernie-3.5-8k", "ernie-speed-8k"],
        "default_model": "ernie-4.0-8k",
    },
    "doubao": {
        "name": "豆包 (Doubao)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-1.5-pro-32k", "doubao-1.5-lite-32k", "doubao-pro-32k", "doubao-lite-32k"],
        "default_model": "doubao-1.5-pro-32k",
    },
    "siliconflow": {
        "name": "硅基流动 (SiliconFlow)",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "THUDM/glm-4-9b-chat"],
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
        "default_model": "gpt-4o",
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "base_url": "",
        "models": ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
        "default_model": "claude-opus-5-5",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
        "default_model": "gemini-2.5-flash",
    },
    "ollama": {
        "name": "Ollama（本地模型，无需 API Key）",
        "base_url": "http://localhost:11434",
        "models": ["qwen2.5:14b", "qwen2.5:7b", "llama3.1:8b"],
        "default_model": "qwen2.5:14b",
    },
    "custom": {
        "name": "自定义 (OpenAI 兼容)",
        "base_url": "",
        "models": [],
        "default_model": "",
    },
}
