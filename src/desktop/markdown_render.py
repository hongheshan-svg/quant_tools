"""
桌面端的 markdown 渲染（纯函数，不依赖 Qt，方便在没有图形库的环境里测试）。
"""

from __future__ import annotations


def render_chat_markdown(turns: list, pending: str = "", status: str = "") -> str:
    """问股对话转为 markdown：问题、查询过的数据、回答。"""
    if not turns and not pending:
        return ("#### 可以这样问\n- 中远海控现在能买吗？止损放哪？\n- 今天的主线是什么，龙头是谁？\n"
                "- 我的模拟盘持仓风险大吗？\n- 用龙回头的标准看看招商轮船\n\n> 仅供学习研究，不构成投资建议")
    lines = []
    for t in turns:
        lines.append(f"**🧑 {t.question}**　*{t.asked_at}｜{t.perspective}*")
        if t.tools:
            lines.append("> 查询：" + "、".join(dict.fromkeys(x["label"] for x in t.tools)))
        lines.append(t.answer or f"*{t.error}*")
        lines.append("---")
    if pending:
        lines += [f"**🧑 {pending}**", f"*{status or '思考中…'}*"]
    return "\n\n".join(lines)


def render_news_markdown(data: dict) -> str:
    """个股新闻与公告转为 markdown（标题带链接，风险公告加标注）。"""
    lines = ["### 公告（近 30 天）"]
    notices = data.get("notices") or []
    for n in notices:
        title = f"[{n['title']}]({n['url']})" if n.get("url") else n["title"]
        flag = f" ⚠️ **{'严重风险' if n.get('severe') else '风险'}：{n['risk']}**" if n.get("risk") else ""
        lines.append(f"- {n['date']} {('【' + n['source'] + '】') if n.get('source') else ''}{title}{flag}")
    if not notices:
        lines.append("- 暂无")
    lines.append("### 个股新闻（近 7 天）")
    news = data.get("news") or []
    lines += [f"- {n['date']} [{n.get('source') or '东方财富'}] " + (f"[{n['title']}]({n['url']})" if n.get("url") else n["title"]) for n in news]
    if not news:
        lines.append("- 暂无")
    return "\n".join(lines)
