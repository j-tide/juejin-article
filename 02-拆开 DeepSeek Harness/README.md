# 拆开 DeepSeek Harness

从“帮我修个 Bug”这句话发出去开始，沿执行顺序读 DeepSeek Harness：输入怎么进来，模型何时被调用，工具怎么执行，又在什么条件下结束。

[掘金专栏](https://juejin.cn/column/7686394441277259822)

## 文章

1. [“帮我修个 Bug”发出去后，跑了哪条路？](<01｜“帮我修个 Bug”发出去后，跑了哪条路？/README.md>)

## 对应版本

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) `0.1.6-alpha.2`，提交 [ddefc45fbc7f8e46dd73185e68295696d1297887](https://github.com/deepseek-ai/deepseek-harness/commit/ddefc45fbc7f8e46dd73185e68295696d1297887)。正文中的源码链接固定到这个版本，后续实现可能不同。

## 配套验证

- [环境、复跑步骤和许可](https://github.com/j-tide/juejin-article/blob/main/02-%E6%8B%86%E5%BC%80%20DeepSeek%20Harness/code/README.md)
- [两个集成测试的代码](https://github.com/j-tide/juejin-article/blob/main/02-%E6%8B%86%E5%BC%80%20DeepSeek%20Harness/code/column-evidence.spec.ts)
- [读、改、测的执行轨迹](https://github.com/j-tide/juejin-article/blob/main/02-%E6%8B%86%E5%BC%80%20DeepSeek%20Harness/code/evidence/task-trace.json)
- [preset 的加载和卸载记录](https://github.com/j-tide/juejin-article/blob/main/02-%E6%8B%86%E5%BC%80%20DeepSeek%20Harness/code/evidence/preset-trace.json)
- [上游测试文件清单](https://github.com/j-tide/juejin-article/blob/main/02-%E6%8B%86%E5%BC%80%20DeepSeek%20Harness/code/focused-tests.txt)

测试实际执行文件读写和测试进程，模型回复由 MockAdapter 给出。记录覆盖 762 项指定上游测试和 2 项专栏测试，不能据此判断真实模型的修复能力。

[配图与可编辑源文件](diagrams/README.md)

[返回首页](<../README.md>)
