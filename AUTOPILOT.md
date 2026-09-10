# AUTOPILOT — rmrb-canary 后台作业登记

> 本文件是后台作业的**唯一登记处**（CLAUDE.md §2）。
> 未登记在此的定时任务一律不得启动。

## 当前状态

**没有任何后台作业在运行。** 本轮（P2）只交付了运行器，没有启动调度。

## 待批准的作业（尚未启动）

下面是按方案 §7.1 建议的双节奏配置。**这些都还没有创建**，
需要你确认节奏、通知渠道与请求预算之后才会启动。

| 作业名 | 建议周期 | 命令 | 停止命令 |
|---|---|---|---|
| `rmrb-daily` | 每日出版时段一次 | `python3 -m agent.runner --topics ai vaccine --enterprise <enterprise-key>` | `launchctl unload ~/Library/LaunchAgents/rmrb-daily.plist`（作业创建后此处填实际值） |
| `policy-docs-poll` | 30–60 分钟 | `python3 -m agent.runner --topics ai vaccine --skip-media` | 同上 |

启动前必须确定的事项：

- 通知渠道（当前告警只到"待投递"，没有任何真实发送实现）
- 请求预算与退避策略（来源方的访问要求优先于我们的节奏偏好）
- 失败退避与告警风暴上限
- 停止命令必须**先实测能停下来**，再写进上表 —— "停止"指调度器本身停止并经验证

## 运行痕迹

- 每次分析运行都记入 `~/.rmrb_sentinel/history.db` 的 `run_log` 表。
- 告警状态流转记入 `~/.rmrb_sentinel/alerts.db` 的 `alert_transitions` 表。
- 运行日志不增加统计样本（§8.2）。

## 手动运行（不属于后台作业）

```bash
python3 -m agent.runner --topics ai vaccine --enterprise <enterprise-key>
python3 -m agent.runner --pending        # 查看待投递告警
```
