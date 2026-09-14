# 参与贡献

感谢你为工程计划横道图项目贡献代码。本文说明本地开发、测试和提交变更的约定；产品功能和排程模型概览请先阅读 [README.md](README.md)。

## 开发前准备

需要安装：

- Python 3.13（项目要求 `>=3.13,<3.14`）
- [uv](https://docs.astral.sh/uv/)
- Windows 桌面环境（完整验证 PySide6 界面时需要）

克隆仓库后，在项目根目录执行：

```powershell
uv sync --frozen
```

`uv sync --frozen` 会按 `uv.lock` 创建或更新项目内的 `.venv`，并确保本地依赖没有悄悄偏离锁定版本。如果你有意修改依赖，请先更新 `pyproject.toml`，再运行 `uv lock` 和 `uv sync`，并一并提交新的 `uv.lock`。

启动应用：

```powershell
uv run construction-schedule
```

也可以在 VS Code 中使用“使用 uv 启动施工排程”调试配置。该配置会先执行锁定依赖同步，再启动 `main.py`。

## 开发流程

1. 从最新代码创建一个聚焦单一问题的分支。
2. 开始修改前运行测试，确认本地基线正常。
3. 实现尽量小且完整的改动，并为新增或修复的行为添加测试。
4. 运行完整自动化测试；涉及界面时再完成对应的人工冒烟检查。
5. 检查 `git diff`，确保没有数据库、缓存、导出文件或无关格式化混入提交。
6. 提交清晰的变更说明，并在合并请求中写明动机、实现方式和验证结果。

仓库当前没有强制的提交消息规范。建议使用简短、祈使式的主题，并让一次提交只表达一个逻辑变更，例如：

```text
修复待发生事件后的任务排程
```

## 运行测试

项目使用 Python 标准库 `unittest`：

```powershell
uv run python -m unittest discover -s tests -v
```

运行单个测试模块：

```powershell
uv run python -m unittest tests.test_scheduler -v
```

运行单个测试用例：

```powershell
uv run python -m unittest tests.test_scheduler.SchedulerTests.test_cycle_is_rejected -v
```

当前测试主要覆盖：

- `test_domain_model.py`：类型化领域对象、兼容映射和不变量。
- `test_scheduler.py`：工作日历、排程算法、资源分配、关键路径、数据库 CRUD 与迁移。
- `test_scheduling_engine_api.py`：新版排程引擎公开接口与旧接口兼容性。
- `test_time_scaled_network.py`：时标网络图的数据筛选规则。

仓库尚未配置单独的格式化器、linter 或类型检查器，因此不要把未配置的工具写成必需检查。如果贡献引入这类工具，请同时提交配置、锁文件和使用说明。

## 架构与改动边界

主要模块职责如下：

| 路径 | 职责 |
| --- | --- |
| `main.py` | 应用启动、字体和图标 |
| `construction_manager/main_window.py` | 主窗口操作和界面编排 |
| `construction_manager/dialogs.py` | 编辑及管理对话框 |
| `construction_manager/gantt.py` | 横道图绘制、交互和导出 |
| `construction_manager/network_data.py` | 网络图共用的数据筛选逻辑 |
| `construction_manager/time_scaled_network.py` | 单代号时标网络图绘制、交互和导出 |
| `construction_manager/database.py` | SQLite 架构、迁移和数据访问 |
| `construction_manager/domain/` | 类型化排程领域模型 |
| `construction_manager/scheduling/` | 排程策略、计算结果、资源分配和关键路径 |
| `construction_manager/scheduler.py` | 旧调用方式的兼容门面 |
| `construction_manager/work_calendar.py` | 工作日和工作时段计算 |

新增排程逻辑应优先通过 `SchedulingEngine.calculate(ScheduleModel, ScheduleOptions)` 进入。`construction_manager.scheduler` 仍需保留旧函数接口，避免一次性破坏现有调用方。

请特别保持以下业务不变量：

- `tasks` 保存计划输入，`schedule_runs` 与 `task_schedule_results` 保存可版本化的计算输出。
- 任务关系支持 FS、SS、FF、SF，并区分工作日与自然日间隔。
- 待发生事件会阻断后续任务；里程碑是零工期事件。
- 工艺关系优先于资源形成的临时先后顺序。
- 汇总任务日期由子任务汇总，末级活动才消耗工期和资源。
- 工作日历、自然连续活动、噪音限制、班组日历和路径日历不能互相混用。

## 数据库变更

应用默认把本地数据保存在 `data/schedule.db`。`data/` 已被忽略，任何真实项目数据都不应进入版本控制。

修改 SQLite 架构时：

- 同时更新新建数据库的 schema 和已有数据库的迁移逻辑。
- 保持迁移可重复执行；应用每次启动都会执行初始化检查。
- 先迁移有效数据，再移除旧字段或旧结构。
- 使用外键和参数化 SQL，并通过 `Database.session()` 管理事务。
- 测试必须使用 `tempfile` 创建隔离数据库，同时覆盖空库和代表性的旧库结构。

对迁移风险较高的变更，建议先手工备份 `data/schedule.db`，再用副本启动应用验证。不要把备份提交到仓库。

## 界面验证

自动化测试不覆盖全部 PySide6 交互。涉及 `main_window.py`、`dialogs.py`、`task_tree.py`、`gantt.py` 或 `time_scaled_network.py` 时，请按改动范围检查：

- 应用能正常启动和关闭。
- 新建、编辑、移动和删除相关对象后数据会刷新并持久化。
- 重新排程不会丢失选择、展开状态或产生重复关系。
- 横道图缩放、滚动、展开/收起和关键路径显示仍正常。
- 时标网络图的时间位置、泳道、关系箭头和关键路径显示仍正常。
- 涉及导出时，PNG 能生成且标题、日期栏和完整范围显示正确。
- 中文文本、日期和错误提示清晰且不被控件截断。

## 提交合并请求

合并请求应包含：

- 问题背景与期望行为。
- 关键实现选择，以及可能影响的排程或迁移语义。
- 已运行的准确命令和结果；不能运行的检查及原因。
- 界面变化的截图或简短录屏（如适用）。
- 数据库变更的升级与回退注意事项（如适用）。

提交前自查：

- [ ] 改动范围聚焦，没有夹带无关重构。
- [ ] 新行为或修复有回归测试。
- [ ] `uv run python -m unittest discover -s tests -v` 通过。
- [ ] UI 改动已做人工冒烟验证。
- [ ] 没有提交 `data/`、`.venv/`、缓存、日志、数据库或导出文件。
- [ ] 依赖变化同时更新了 `pyproject.toml` 与 `uv.lock`。
- [ ] 用户可见行为或开发方式变化已同步更新文档。

更细的编码代理约束见 [AGENTS.md](AGENTS.md)。
