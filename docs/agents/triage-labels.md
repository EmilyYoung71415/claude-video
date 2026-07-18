# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role, use the corresponding label string from this table.

## 协作下一方标签

活动的跨机器或跨项目事项最多保留一个 `next:*` 标签。它表示“下一步应该由谁采取动作”，不是当前负责人、代码状态或能力类型；交接时替换旧标签。

| 标签 | 下一动作 |
| --- | --- |
| `next:cloud-codex` | 云机 Codex 在 Change Repo 的独立分支或工作区实现、运行测试并提交 PR。 |
| `next:local-codex` | 本机 Codex 复核 PR、运行本地集成测试，必要时退回修改。 |
| `next:human` | Human 回答决策问题、审阅并合并 PR，或处理需要人工授权的阻塞。 |

Home Repo Issue 是当前状态的权威入口；PR 可镜像同一个 `next:*` 标签，方便从 PR 列表找到下一动作，但不能创建第二个不同状态。
