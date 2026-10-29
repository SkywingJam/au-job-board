"""澳洲求职聚合流水线。"""
import os
import sys

__version__ = "0.1.0"


def _disable_platform_wmi(nt=os.name == 'nt', version=sys.version_info):
    """Windows 上 CPython < 3.13.4 的 platform 不走 WMI。

    _wmi.exec_query 超时后返回，查询线程却仍读取调用者已失效的栈，几秒后
    崩溃（CPython gh-130727，未回补 3.12）。uvicorn 启动和 pandas 导入都会
    调用 platform.uname()。让私有的 _wmi_query 抛 OSError，platform 即用自带
    回退（getwindowsversion、ver、注册表、环境变量）。受影响版本已冻结，
    私有接口不会再变；其他平台和新版本不进入此分支。
    """
    if not nt or tuple(version[:3]) >= (3, 13, 4):
        return False
    import platform
    if not hasattr(platform, '_wmi_query'):
        return False

    def unavailable(*_args):
        raise OSError('WMI disabled: CPython gh-130727')
    platform._wmi_query = unavailable
    return True


# 包根最先被导入（面板、worker、MCP、测试），须早于任何 platform.uname() 缓存。
_disable_platform_wmi()


class RunTimeout(Exception):
    """全流程超时。

    定义在包根而不是 cli 里：抓取适配器也要能识别它并**向上放行**。
    各适配器都有「这个来源/查询失败了就跳过」的宽泛 except，
    超时若被那层吞掉，就退化成「每个查询都失败」而不是「整体中断」——
    这正是踩过的坑（cmd_fetch 和 jobspy_source 各有一处）。
    """
