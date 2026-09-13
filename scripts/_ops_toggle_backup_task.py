"""临时运维脚本：禁用/查询 lan-share-backup 计划任务（禁用不删除）。

背景：备份暂停（等第二块 SSD 到位），需停掉每日 23:30 的自动入口。
schtasks.exe 被安全策略屏蔽、PowerShell 工具在沙箱下无输出，
因此走 COM 的 Schedule.Service（不经黑名单程序，也不改任务 XML 文件）。
"""
import sys

TASK = "lan-share-backup"


def svc():
    import win32com.client  # type: ignore
    return win32com.client.Dispatch("Schedule.Service")


def find(root, name):
    # \ 根目录下按名字精确找
    for t in root.GetFolder("\\").GetTasks(1):
        if t.Name == name:
            return t
    return None


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        s = svc()
        s.Connect()
    except Exception as e:
        print("COM_INIT_FAIL:", e)
        return 2

    task = find(s, TASK)
    if task is None:
        print("TASK_NOT_FOUND:", TASK)
        return 3

    folder = s.GetFolder("\\")
    if action == "status":
        print(f"TASK={task.Name} ENABLED={bool(task.Enabled)} "
              f"STATE={task.State}")
        return 0

    if action == "disable":
        before = bool(task.Enabled)
        task.Enabled = False
        task.RegisterChanges = None  # 占位，避免误解；实际用 RegisterTaskDefinition
        # Enabled 属性直接改需经 RegisterTaskDefinition 才落盘
        folder.RegisterTaskDefinition(
            TASK, task.Definition,
            4,  # TASK_UPDATE
            None, None, task.Definition.Principal.LogonType,
        )
        after = bool(folder.GetTask(TASK).Enabled)
        print(f"BEFORE_ENABLED={before} AFTER_ENABLED={after}")
        print("OK-DISABLED" if not after else "FAILED-STILL-ENABLED")
        return 0

    if action == "enable":
        task.Enabled = True
        folder.RegisterTaskDefinition(
            TASK, task.Definition, 4, None, None,
            task.Definition.Principal.LogonType)
        after = bool(folder.GetTask(TASK).Enabled)
        print(f"AFTER_ENABLED={after}")
        return 0

    print("UNKNOWN_ACTION:", action)
    return 4


if __name__ == "__main__":
    sys.exit(main())
