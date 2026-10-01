// 大模型调用错误分类的提示（由后端返回中文，前端按原文查词典）
export default {
  'API Key 无效或没有权限，请检查 Key 是否填对、是否开通了该模型': 'The API key is invalid or lacks permission. Check the key and that the model is enabled for it.',
  '账户余额不足或额度已用完': 'Insufficient account balance or quota exhausted',
  '请求太频繁被限流，请稍后再试或配置多个 Key': 'Rate limited. Try again later or configure multiple keys.',
  '模型名称不存在，请用「获取模型列表」选择正确的模型': 'The model name does not exist. Use "Fetch model list" to pick a valid one.',
  '输入内容超过模型的上下文长度': 'The input exceeds the model context length',
  '内容被模型平台的安全审核拦截': 'The content was blocked by the platform safety review',
  '请求超时，请检查网络或调大超时时间': 'Request timed out. Check the network or increase the timeout.',
  '无法连接到模型服务，请检查 Base URL 和网络': 'Cannot reach the model service. Check the Base URL and network.',
  '模型服务暂时不可用（服务端错误）': 'The model service is temporarily unavailable (server error)',
}
