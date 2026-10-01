# 团队培训安排

登记成员、创建带容量的培训活动，并记录报名与查看参与名单。

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
- `roster` → `TeamPlanner.roster(...)`。参数名见 `core.py` 的公开方法签名。
- `list` → `TeamPlanner.activities(...)`。参数名见 `core.py` 的公开方法签名。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前只管理培训报名，不支持请假、排班、候补、取消报名、培训完成记录或权限系统。 不承诺并发写入或断电恢复。
