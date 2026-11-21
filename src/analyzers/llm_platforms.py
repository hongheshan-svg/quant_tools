"""
大模型平台预设（Web 设置页和大模型客户端使用）。
anthropic、gemini、ollama 走 LiteLLM 的原生通道（base_url 可留空，ollama 填本机服务地址），
其余平台按 OpenAI 兼容协议调用各自的 base_url。
"""

from __future__ import annotations

AI_PLATFORMS: dict[str, dict] = {
    "requesty": {
        "name": "Requesty（聚合平台，模型可见性依赖账户权限）",
        "base_url": "https://router.requesty.ai/v1",
        "models": [],
        "default_model": "",
    },
    "deepseek": {
        "name": "DeepSeek (深度求索)",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
    },
    "qwen": {
        "name": "通义千问 (Qwen)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.6-plus", "qwen3.6-flash"],
        "default_model": "qwen3.6-plus",
    },
    "zhipu": {
        "name": "智谱GLM (ChatGLM)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5.1", "glm-4.7-flash"],
        "default_model": "glm-5.1",
    },
    "moonshot": {
        "name": "月之暗面 (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2.6", "kimi-k2.5"],
        "default_model": "kimi-k2.6",
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
        "models": ["doubao-seed-1-6-251015", "doubao-seed-1-6-thinking-251015"],
        "default_model": "doubao-seed-1-6-251015",
    },
    "siliconflow": {
        "name": "硅基流动 (SiliconFlow)",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3-235B-A22B-Thinking-2507"],
        "default_model": "deepseek-ai/DeepSeek-V3.2",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-6.1-sol", "gpt-6.1-terra"],
        "default_model": "gpt-6.1-sol",
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "base_url": "",
        "models": ["claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5"],
        "default_model": "claude-opus-5-5",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "",
        "models": ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
        "default_model": "gemini-3.6-flash",
    },
    "ollama": {
        "name": "Ollama（本地模型，无需 API Key）",
        "base_url": "http://localhost:11434",
        "models": ["qwen2.5:14b", "qwen2.5:7b", "llama3.1:8b"],
        "default_model": "qwen2.5:14b",
    },
    "aihubmix": {
        "name": "AIHubMix（聚合平台）",
        "base_url": "https://aihubmix.com/v1",
        "models": ["gpt-6.1-sol", "claude-sonnet-4-6", "gemini-3.6-flash"],
        "default_model": "gpt-6.1-sol",
    },
    "anspire": {
        "name": "Anspire Open（模型 + 搜索）",
        "base_url": "https://open-gateway.anspire.cn/v6",
        "models": ["Doubao-Seed-2.0-lite", "Doubao-Seed-2.0-pro", "qwen3.5-flash"],
        "default_model": "Doubao-Seed-2.0-lite",
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "models": ["MiniMax-M3.1-Flash", "MiniMax-M3"],
        "default_model": "MiniMax-M3.1-Flash",
    },
    "openrouter": {
        "name": "OpenRouter（聚合平台）",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["~anthropic/claude-sonnet-latest", "~openai/gpt-latest"],
        "default_model": "~anthropic/claude-sonnet-latest",
    },
    "stepfun": {
        "name": "阶跃星辰 (StepFun)",
        "base_url": "https://api.stepfun.com/v1",
        "models": ["step-2-16k", "step-1-32k", "step-1-flash"],
        "default_model": "step-2-16k",
    },
    "xai": {
        "name": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "models": ["grok-4", "grok-3-mini"],
        "default_model": "grok-4",
    },
    "custom": {
        "name": "自定义 (OpenAI 兼容)",
        "base_url": "",
        "models": [],
        "default_model": "",
    },
}

# 专用本地后端，模型名 default 使用本地登录配置的默认模型。
for _provider, _name in (("codex_cli", "Codex CLI"), ("claude_code_cli", "Claude Code CLI"), ("opencode_cli", "OpenCode CLI"), ("hermes", "Hermes 本机服务")):
    AI_PLATFORMS[_provider] = {"name": _name, "base_url": "http://127.0.0.1:8642/v1" if _provider == "hermes" else "", "models": ["hermes-agent" if _provider == "hermes" else "default"], "default_model": "hermes-agent" if _provider == "hermes" else "default"}
