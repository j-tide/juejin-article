# 茶饮工单 Agent Demo

当前版本对应第 04 篇：增加活动规则与历史工单检索，沿用前三篇的执行器和话题代码。Python 3.11+，只使用标准库。

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

## 第 02 篇：飞书事件接入

```bash
python3 -m ticket_agent.feishu --db /tmp/ticket-agent-ch02.sqlite3
```

默认本地事件回放，包含重复事件、同话题图片和机器人消息；不会联网发送。复用同一个数据库再次执行，不重复创建回复。真实运行数据请放仓库外。

可选官方 SDK：

```bash
python3 -m pip install -r requirements-feishu.txt
python3 -m unittest discover -s tests -v
```

共 32 项测试；没有 SDK 时跳过其中 1 项契约测试。SDK 固定 `lark-oapi==1.7.3`，本地契约测试通过，实际平台收发未联调。

真实模式需要环境变量 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`DEEPSEEK_API_KEY`，以及已启用长连接、机器人和所需消息权限的应用。参考 `fixtures/bindings.example.json` 在仓库外建立私有绑定文件：

```bash
python3 -m ticket_agent.feishu --live --db /tmp/ticket-agent-live.sqlite3 --bindings /path/to/private-bindings.json
```

一个租户与群绑定一个门店，仅适合本章测试群。没有多门店身份授权实现。回调不调用模型；单 Worker 读取 Inbox，再保存 Outbox。发送异常或进程在发送中退出，保留 `uncertain` 等待人工核对，不自动重发。只启动一个进程/Worker；恢复函数不支持并发工作者。

附件只保存引用，未下载、未解析。尚未合并话题的多条消息、同步编辑撤回或自动重试失败任务。数据库包含输入正文和调查结果，请勿公开提交。

## 第 03 篇：话题状态与版本化回复

```bash
python3 -m ticket_agent.feishu --conversation --db /tmp/ticket-agent-ch03.sqlite3
```

使用新的独立数据库。回放合成对话：缺少对象 → 补订单 → 查询中更正 → 人工认领 → 继续。更正由回调确定性插入查询过程，不靠 sleep；结果应为 `v2/stale` 和 `v5/current` 两次运行，一条追问、一条当前结果。复用已完成的回放库会提示 `already_replayed`。

明确动作：`/订单 P1002`、`/清除订单`、`/接手`、`/继续`。后两项受 `operators` 和当前认领人约束。少量自然语言停止规则只用于演示，不是通用意图识别。后续原消息均保存，但未把全部讨论整理到模型上下文。

真实入口使用 `--live --conversation`；绑定格式见 `fixtures/conversation-bindings.example.json`，其他准备同第 02 篇。不要让不同模式或多个 Worker 共用运行库。没有实现从旧运行数据迁移的过程。

累计 45 项测试；无 SDK 时跳过 1 项。等待、认领、输入版本和旧结果均持久化；结果保存及发送前检查版本。检查之后仍可能到达新消息，已发回复不自动撤回。乱序旧消息标记 `late_needs_review`，目前须人工查看；没有自动处理编辑撤回和多业务对象。

## 第 04 篇：范围受限的资料检索

```bash
python3 -m ticket_agent.research '两杯水果茶，优惠券用不了' --campaign campaign-a --at 2026-09-18T14:00:00+08:00 --known-at 2026-09-18T14:10:00+08:00 --channel miniapp --product fruit-tea --basket-cents 3200
```

换成 `--campaign campaign-b --store store-002` 可对比条件匹配的另一场活动；不提供 `--product` 检查未知条件。活动 C 包含有意冲突的规则。`fixtures/knowledge.json` 全部为合成资料。

先按授权范围、活动、生效时间和已知时间过滤，再执行原始/领域词扩展两路词法检索，以 RRF 合并名次。没有 Embedding 模型。规则条件分为 match/mismatch/unknown，历史工单不作本单根因证据；冲突检测仅针对相同 rule_key 的结构化要求。

默认固定策略回放，`--live` 才请求模型。资料入口复用 Agent 执行器，但没有接入飞书自动活动识别和调查类型路由；上下文由运行者明确提供。累计 58 项测试通过（无 SDK 时跳过 1 项）。真实模型效果尚未验证。
