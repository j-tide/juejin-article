# 《拆开 DeepSeek Harness》配套验证

源码固定在 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) `0.1.6-alpha.2`，提交 [ddefc45fbc7f8e46dd73185e68295696d1297887](https://github.com/deepseek-ai/deepseek-harness/commit/ddefc45fbc7f8e46dd73185e68295696d1297887)。

首次验证环境：macOS、Node v22.22.0、pnpm 11.7.0、Vitest 4.1.8。26 个上游测试文件的 762 个测试通过，另有 2 个专栏集成测试通过。这里只覆盖列出的场景，不代表全仓库或所有平台通过。

## 复跑

需要 Git、Node 和 pnpm。在本目录执行以下命令；上游会克隆到已被 Git 忽略的独立目录，避免影响自己的开发分支。

```sh
git clone https://github.com/deepseek-ai/deepseek-harness.git upstream-checkout
git -C upstream-checkout checkout --detach ddefc45fbc7f8e46dd73185e68295696d1297887
bash run-evidence.sh ./upstream-checkout
```

脚本核对提交后执行锁定依赖安装、26 个指定测试文件和 2 个专栏测试。临时测试文件只在不存在时添加，并在结束时移除；同名但内容不同的文件不会被覆盖。不会启动 Web、桌面端或真实模型任务，不会改写你实际使用的 DSH profile。

依赖安装关闭 lifecycle scripts；这套指定测试不要求完整产品构建。其他集成、原生模块或跨平台测试不能直接套用这个前提。网络下载与本地磁盘空间需要由运行环境提供。

每次复跑会在系统临时目录创建独立结果目录，脚本结束时打印位置；检查报告不写回文章仓库。首次运行的任务与 preset 轨迹保留在 `evidence/`，上游测试范围见 `focused-tests.txt`。

## 两个测试分别证明什么

1. **读、改、测完整轨迹**：真实 AgentLoop、工具注册表和 Session；临时文件由真实 Node 文件 API 读写，独立 Node 进程运行断言。模型使用仓库 MockAdapter，读、改、测、回答四次回复和修复内容全部预先给定。验证结果是 1 turn、4 step、4 模型请求、3 次工具往返，修复前断言退出码 1，修复后 0。它不评价真实模型找 Bug 的能力，也不代表内置文件工具的全部约束。
2. **自定义 preset**：实际 Loader 和 AgentPresets 读取 `preset.yml`、`agent.cordis.yml`、`review-plugin.mjs`。确认工具与提示对加入的 Agent 可见，未加入的 Agent 不可见，调用得到真实清单结果。单个 Agent 处置后常驻定义仍存在，处置 roster 后工具与提示贡献移除。模型同样是固定脚本。

第 2 个测试曾先用“Agent 结束就销毁 preset 定义”作为假设，断言失败。阅读固定版本的 standing mount 实现后，分别验证 Agent 注册与常驻组合所有者，得到第九篇记录的结论。这不是修复了上游 bug，而是修正了最初的所有权假设。

`task-trace.json` 保留原始 Session 事件与请求角色序列。此版本中的工具结果采用内部内容块协议，请勿根据角色名称猜测它与其他 SDK 的 wire 格式完全相同。`preset-trace.json` 保留可见工具与生命周期观察结果。

上游 PTC 测试在受控 runtime 边界核对注册表桥接、并发屏障、审批与结果。另运行绑定及 JSON 输出测试，未做 PTC 全平台沙箱和进程树实测，也没有真实模型延迟、token 节省或任务成功率基准。

## 示例 preset

三份文件放在同一目录即可构成本文的最小组合。复跑脚本只把它们复制到临时测试目录。接入产品环境时，需要按对应版本的用户 preset 创作流程配置，并单独验证实际环境中的行为。

示例只有清单工具与提示，不包含代码读取、命令执行或强制只读策略。提示里的行为要求不能代替工具权限和沙箱。它的用途是验证组合机制，而非冒充完整代码审查产品。

## 来源与许可

- 上游：[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/tree/ddefc45fbc7f8e46dd73185e68295696d1297887)，MIT，版权归 DeepSeek；原许可见 `UPSTREAM-LICENSE.txt`。
- `column-evidence.spec.ts` 的运行时安装方式参考上游测试 harness，运行时直接导入该版本的实现和 `mock-adapter.ts`。上游源码与依赖未打包进本附件。
- 专栏新增测试与示例代码按 MIT 提供，见 `LICENSE`。文章内容单独交付，不因代码许可而被自动授权为上游项目文档。
