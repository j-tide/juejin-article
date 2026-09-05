# 茶饮工单 Agent Demo

当前版本对应第 07 篇：增加只读 SQL、阶段事件与可见性对照。Python 3.11+；源码实验需要 Git 和 Node.js，前文原生媒体实验另需 macOS、Swift 和 FFmpeg。

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

## 第 05 篇：附件抽帧与原生 OCR

```bash
swiftc scripts/vision_ocr.swift -o /tmp/vision-ocr
python3 -m ticket_agent.media checkout --ocr /tmp/vision-ocr --fps 1
python3 -m ticket_agent.media checkout --ocr /tmp/vision-ocr --fps 5 --start 0.8 --end 1.6
python3 -m ticket_agent.media notice --ocr /tmp/vision-ocr
TICKET_OCR_BINARY=/tmp/vision-ocr python3 -m unittest discover -s tests -v
```

视频已放在 `fixtures/media/`，不需要重新生成。单文件 20 MB、视频 20 秒、400 万像素、40 帧；每条外部命令超时 30 秒，不是整个任务的硬期限。附件、范围和采样计划由调用方绑定，`MEDIA_TOOL` / `media_handler` 可注册到已有执行器；尚未实现飞书附件自动下载、视频动作识别、ASR 或调查路由。

实际运行：macOS 27.0、FFmpeg 8.1.2、Python 3.11.9。合成视频 3 秒，提示位于 [1.1,1.4) 秒。1 fps 采 3 帧未命中，局部 5 fps 采 4 帧在 1.2 秒发现候选，置信分数 0.5，保留 `uncertain_text`。结果在 `fixtures/media/experiment-results.json`。不是准确率评测；没有调用云模型。

累计 70 项测试通过；未设置 `TICKET_OCR_BINARY` 跳过 1 项原生集成测试，没有飞书 SDK 再跳过 1 项 SDK 测试。OCR 路径依赖 macOS Vision；Python 逻辑测试可独立运行。

仅在重建合成素材时需要 Pillow（本次 12.3.0），并提供本机中文字体，字体未随仓库分发：

```bash
python3 scripts/make_media_fixture.py --font /path/to/local-chinese-font.ttf
```

重新生成可能改变视频哈希和 OCR 输出；实验结果需随实际重跑更新。`ground-truth.json` 仅记录合成条件，解析器不读取它。图片和视频均为合成测试素材，没有真实顾客信息。

## 第 06 篇：绑定部署版本的代码读取

```bash
python3 -m ticket_agent.source --workspace /tmp/ticket-source-ch06
python3 -m ticket_agent.source --workspace /tmp/ticket-source-ch06 --store store-002
node scripts/source_comparison.mjs
```

首次传入空实验目录，会从 `fixtures/source/` 建立两个小型 Git 仓库及合成部署表；保留目录用于复核真实生成的提交 ID，再次运行复用它。不要指向已有业务仓库。没有连接真实发布平台；本地配置不是身份鉴权。门店 001 绑定 v1，002 绑定 v2；示例时间为 2026-09-18，匹配不到或匹配多条部署记录时停止读取。

默认是固定五步源码阅读配方，不是模型推理。`--live` 注册 `search_source` 与 `read_source` 到现有执行器，需要 `DEEPSEEK_API_KEY`，未配置会明确失败。真实模型调用未验证。MCP 仅讨论接口映射，未实现 MCP Server 或平台联调。

范围约束：应用绑定提交和允许文件，普通 Git blob、每文件 32 KiB、每次最多扫描 20 个文件、返回 6 处命中、每片段最多 40 行；拒绝符号链接和符号版本。Git 每次命令 10 秒；Agent 最多 6 次工具调用、7 轮、90 秒轮间检查。工具不执行被读取代码。

独立对照脚本实际运行公开合成前后端函数：金额均为 3200 分、规则从 r1 变为 r2 时，v1 返回 PRICE_CHANGED，v2 返回 OK。结果在 `fixtures/source/experiment-results.json`。v2 只是对照条件，不是完整计价修复建议。静态路径与合成执行都不证明客户工单实际走过这个分支。

Python 3.11.9、Git 2.55.0、Node.js 22.22.0 下累计 86 项测试通过（同时启用了前文 OCR 与飞书 SDK 测试）。缺少 OCR 路径跳过 1 项；没有 SDK 再跳过 1 项。Node.js 与 Git 是本章测试必需依赖。

## 第 07 篇：只读 SQL 与事件可见性

```bash
python3 -m ticket_agent.evidence_db --db /tmp/ticket-evidence-ch07.sqlite3 --init-demo
python3 -m ticket_agent.evidence_db --db /tmp/ticket-evidence-ch07.sqlite3 --reference P1002 --as-of 2026-09-18T14:00:15+08:00
python3 -m ticket_agent.evidence_db --db /tmp/ticket-evidence-ch07.sqlite3 --reference P1002 --as-of 2026-09-18T14:00:30+08:00
```

首次初始化只写空路径，后续不再带 `--init-demo`。数据库放仓库外。输入在 `fixtures/database.json`，实际本地查询摘要在 `fixtures/database-results.json`。相同快照的两单有不同事件历史；14:00:06 的打印确认于 14:00:20 入库，早查不可见。用字段过滤模拟延迟，没有运行真实复制服务。

`SqlOrderReader.lookup` 保持原 Reader 接口，复用执行器；未接到飞书命令或真实数据库。模板 SQL 绑定品牌、门店、对象和时间，`mode=ro`、`query_only`、字段授权回调共同约束。每次最多 10 条事件、窗口最多 20 分钟；锁等待 0.1 秒、默认 VM 进度预算 0.2 秒，不是硬 I/O 截止。

累计 100 项测试通过（启用前文 SDK 和原生 OCR）。没有自由 SQL、自动重试、生产压测或历史快照重建；未查到回执、被截断与查询不可用均不证明现场没有打印。设备确认也不等于饮品交付。
