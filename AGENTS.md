# AGENTS.md

本文档面向在本仓库中工作的编码代理。除非更深层目录中存在单独的 `AGENTS.md`，以下约定适用于整个仓库。

## 项目目标

本项目是一个基于 Python 3.13、PySide6 和 SQLite 的单机施工排程应用。核心能力包括任务树、工作日历、四类任务关系、事件/里程碑、班组与空间资源平衡、关键路径和横道图。

修改代码前先阅读 `README.md`，不要破坏其中描述的排程语义。当前明确不包含成本、基线和多项目管理；除非任务明确要求，不要顺带扩展这些范围。

## 开发环境与常用命令

依赖和锁文件以 `pyproject.toml`、`uv.lock` 为准，使用 `uv` 管理环境：

```powershell
uv sync --frozen
uv run construction-schedule
```

运行全部自动化测试：

```powershell
uv run python -m unittest discover -s tests -v
```

运行单个测试模块或测试用例：

```powershell
uv run python -m unittest tests.test_scheduler -v
uv run python -m unittest tests.test_scheduler.SchedulerTests.test_cycle_is_rejected -v
```

仓库当前没有配置独立的格式化、静态检查或类型检查命令。不要假设 `pytest`、Ruff、Black 或 mypy 已安装；如需引入新的开发工具，应同时更新 `pyproject.toml`、`uv.lock` 和相关文档。

## 代码结构

- `main.py`：GUI 启动入口、应用字体和图标配置。
- `construction_manager/main_window.py`：主窗口编排、用户操作与刷新流程。
- `construction_manager/dialogs.py`：各类编辑和管理对话框。
- `construction_manager/gantt.py`：横道图绘制、交互与图片导出。
- `construction_manager/network_data.py`：网络图共用的数据筛选逻辑，保持不依赖 Qt，便于单元测试。
- `construction_manager/time_scaled_network.py`：单代号时标网络图绘制、交互与图片导出。
- `construction_manager/task_tree.py`：任务树交互和拖放。
- `construction_manager/database.py`：SQLite 架构、迁移、事务和数据访问。
- `construction_manager/domain/`：类型化排程领域模型及输入边界。
- `construction_manager/scheduling/`：排程引擎、策略选项、结果、班组分配和关键路径。
- `construction_manager/scheduler.py`：兼容旧调用方的门面；新代码优先使用 `SchedulingEngine`。
- `construction_manager/work_calendar.py`：工作时段、工作日和自然日计算。
- `tests/`：基于标准库 `unittest` 的领域、数据库、日历和排程回归测试。

## 重要设计约束

### 排程模型

- 任务定义属于输入，计算起止时间、计算班组、时差和排程原因属于输出。
- `tasks` 不应重新保存派生排程字段。排程版本写入 `schedule_runs` 和 `task_schedule_results`。
- 持续活动与零工期事件是不同实体语义。里程碑工期必须为零；待发生事件必须继续阻断依赖它的后续任务。
- FS、SS、FF、SF、工作日/自然日间隔、工作日历和自然连续时间的差异必须保留。
- 工艺依赖是硬约束；资源竞争产生的先后顺序是计算结果，不应偷偷固化为工艺关系。
- 汇总任务的日期由后代任务汇总，不作为普通末级活动排程。
- 类型化领域对象是排程边界。新增排程代码优先使用 `Activity`、`Dependency`、`ScheduleModel`、`ScheduleOptions` 和 `ScheduleResult`，同时保持现有字典接口的兼容性。

### 数据库与迁移

- 默认数据库是 `data/schedule.db`。运行时数据已被 `.gitignore` 排除，不要提交真实数据库、导出图片或日志。
- 测试必须使用 `tempfile` 中的独立数据库，不能读写开发者的 `data/schedule.db`。
- `Database.session()` 负责提交、回滚、关闭连接和启用外键；数据库写操作应复用这一事务边界。
- 架构变化必须能从已有数据库原地升级。新增列或表时同步更新初始化架构和迁移逻辑，并添加“旧架构升级后数据仍正确”的测试。
- 不要无提示删除用户业务数据。若确需移除旧字段，先迁移其有效数据，并用回归测试覆盖。
- 使用参数化 SQL，不拼接用户输入；动态 SQL 仅可用于已在代码中穷举验证的标识符。

### UI 与分层

- 排程规则应放在 `domain/`、`scheduling/` 或 `work_calendar.py`，不要埋进 Qt 事件处理器。
- 数据读写集中在 `Database`，窗口和对话框不应自行管理 SQLite 连接。
- 耗时计算不得无故阻塞或重复触发 UI 刷新。修改任务、关系、日历或资源后，保持当前的自动重排和刷新语义。
- 用户可见文本以中文为主，并沿用现有术语（任务、工序、作业面、班组、事件、排程等）。
- 修改横道图、时标网络图或主窗口后，至少人工检查启动、重排、树形展开/收起和横向滚动；涉及导出时再检查 PNG 输出。

## 编码约定

- 遵循现有 Python 风格：4 空格缩进、类型标注、`snake_case` 函数、`PascalCase` 类。
- 新模块保留 `from __future__ import annotations` 的现有惯例。
- 领域枚举和验证规则应集中定义，避免在 UI、数据库和排程器中散落新的魔法字符串。
- 对外入口和不直观的业务规则写简短 docstring；注释重点解释“为什么”，不要逐行复述代码。
- 优先做小而聚焦的改动。不要顺带格式化大型文件或重写与任务无关的代码。
- 工作区可能已有用户改动。先查看 `git status` 和相关 diff，保留并兼容这些改动，禁止擅自回退。

## 测试要求

任何行为变更都应补充或更新测试：

- 排程算法、约束、资源和日历变更：在 `SchedulerTests` 或 `WorkCalendarTests` 添加边界与回归用例。
- 数据模型或兼容映射变更：更新 `test_domain_model.py`。
- `SchedulingEngine` 的公开契约变更：更新 `test_scheduling_engine_api.py`。
- 时标网络图的数据筛选或布局规则变更：更新 `test_time_scaled_network.py`。
- 数据库架构、CRUD 或迁移变更：在 `DatabaseTests` 中使用临时数据库覆盖新建库和旧库升级路径。
- Bug 修复：先用能复现问题的测试锁定失败场景，再实现修复。

提交结果前运行完整测试套件。若当前环境无法运行某项验证，应明确说明未验证的内容和原因，不能把“未运行”表述为“已通过”。

## 完成检查

交付前确认：

- 改动符合 `README.md` 的产品语义，且没有扩大任务范围。
- 没有提交 `data/`、数据库、缓存、构建产物或本机配置。
- 数据库迁移保持旧数据兼容，排程输入与派生结果仍然分离。
- 新行为有相应自动化测试，完整测试套件通过。
- UI 变更完成了与改动范围相称的人工冒烟验证。
- 面向用户的功能、启动方式或开发流程变化已同步更新 `README.md` 或 `CONTRIBUTING.md`。
