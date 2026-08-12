# 茶饮工单 Agent Demo

当前版本对应第 01 篇：文字工单、只读查询和证据回复。Python 3.11+，只使用标准库。

## 运行

在本目录执行：

```bash
python3 -m unittest discover -s tests -v
python3 -m ticket_agent '顾客付款流水 P1001，店长说没出单'
python3 -m ticket_agent '顾客付款流水 P1001，店长说没出单' --json
```

默认 `replay` 是固定策略的离线回放，不是模型推理。数据位于 `fixtures/orders.json`，全部为合成记录，不随电脑时钟变化。

设置环境变量 `DEEPSEEK_API_KEY` 后：

```bash
python3 -m ticket_agent --mode live '顾客付款流水 P1001，店长说没出单'
```

真实模式调用 `https://api.deepseek.com/chat/completions`，默认 `DEEPSEEK_MODEL=deepseek-flash`，关闭 thinking。接口或模型名称变化时以官方文档为准。缺少 Key 时直接退出，不自动降级成回放。

模型只能传 `reference`。品牌、门店由入口绑定；本地命令行的 `--brand`、`--store` 是演示操作，不是已实现的身份鉴权。默认 `demo-brand/store-001`。

## 本篇范围与限制

- 只读合成订单，记录支付、订单、出单任务和打印回执；查不到不等于业务对象不存在。
- 回复必须原样引用本轮全部证据，程序渲染最终文字。没有自动退款、补单、重打或关闭工单。
- 最多 4 轮模型调用和 2 次工具调用；每次最大 1200 输出 Token。20 秒网络超时，45 秒轮间期限检查；不是强制终止任意阻塞函数的硬期限。
- `OrderReader` 是本地读取。测试通过抛出 `TimeoutError` 验证错误传播，没有声称已经实现远程查询超时或取消。
- `--json` 输出轨迹和供应商 usage，不写入文件；模型请求体、API Key 和 HTTP 错误正文不进入轨迹。
- 在 Python 3.11.9 上通过 19 项脚本化测试。未配置 API Key，真实模型端到端调用尚未验证。

每篇目录中的 `code.zip` 冻结该章代码，可独立解压运行；共享目录会随着后续文章演进。代码使用规则见 COPYRIGHT.md；未另行授予开源许可。
