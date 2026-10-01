# 团队培训安排

登记成员、创建带容量的培训活动，记录报名与查看参与名单，并登记与查询培训完成记录。

## 运行

需要 Python 3.10 或更新版本，仅使用标准库，无依赖安装步骤。请在本目录运行：

```sh
python3 -m team_planner --root ./state demo
python3 -m unittest discover -s tests -v
```

这是本地命令行程序，不监听网络端口，无账户或密码。`demo` 在指定 root 的临时子目录中读取 examples 样例并演示业务，结束后清理样例状态，不改变现有数据。

## 正常使用

公开 API：`from team_planner import TeamPlanner`，然后 `TeamPlanner(root)`。每个命令接收可选的 JSON 文件，其对象键与 API 方法参数一致。例如：

```sh
python3 -m team_planner --root ./state member examples/members.json
```

JSON 数组会按顺序执行多个独立操作；先前成功操作保留，后续失败不会回滚整批。重跑登记命令遇到已存在的标识会报错。

- `member` → `TeamPlanner.add_member(...)`。参数名见 `core.py` 的公开方法签名。
- `activity` → `TeamPlanner.create_activity(...)`。参数名见 `core.py` 的公开方法签名。
- `enroll` → `TeamPlanner.enroll(...)`。参数名见 `core.py` 的公开方法签名。
- `reschedule` → `TeamPlanner.reschedule_activity(...)`。输入对象包含 `activity_id`、`on`；新日期须为真实存在的 `YYYY-MM-DD` 字符串。成功返回完整活动对象（仅 `on` 改变，标题、容量、参与者及顺序不变）。新日期与原日期相同时直接返回原活动，不重写数据。活动已有任何完成记录，或某位当前参与者已报名新日期当天的另一活动时拒绝调整。
- `roster` → `TeamPlanner.roster(...)`。参数名见 `core.py` 的公开方法签名。
- `list` → `TeamPlanner.activities(...)`。参数名见 `core.py` 的公开方法签名。
- `complete` → `TeamPlanner.record_completion(...)`。输入对象包含 `activity_id`、`member_id`、`completed_on`；完成日期须为真实存在的 `YYYY-MM-DD` 日期，且不早于活动日期。成功返回单条完成记录（含活动的 `title`、`on`）。
- `completions` → `TeamPlanner.completions(...)`。输入对象包含 `member_id`；返回该成员的完成记录数组，按 `completed_on`、`on`、`activity_id` 升序排列，无记录时为空数组。
- `merge` → `TeamPlanner.merge_member(...)`。输入对象包含 `source_member_id`、`target_member_id`，分别为被合并成员与保留成员；标识去除首尾空白，非字符串、为空、两者相同或任一成员不存在都会被拒绝。被合并成员在各活动中的席位并入保留成员：仅其报名时在原位置替换为保留成员，两人都报名时保留一个位于两人中较早位置的席位，其余成员相对顺序不变，去重释放的名额可继续报名。完成记录仅一人有则归入保留成员；两人在同一活动都有记录时 `completed_on` 相同才合为一条，不同则拒绝整个合并（不选择日期、不覆盖记录）。成功返回保留成员对象并删除被合并成员档案。

登记完成不取消报名、不释放名额，也不改变名单顺序；同一成员在同一活动只能登记一次。活动或成员不存在、成员未报名、重复登记或完成日期早于活动日期都会被拒绝。历史 `data.json` 没有完成记录字段时视为没有已完成培训。合并同样不额外拒绝同日多项报名；任何活动上的完成日期冲突都会拒绝整个合并，不留下部分结果，失败也不创建数据文件。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前只管理培训报名与完成登记，不支持请假、排班、候补、取消报名或权限系统。 不承诺并发写入或断电恢复。
