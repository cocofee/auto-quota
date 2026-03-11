# -*- coding: utf-8 -*-
"""
PDF解析配置注册表

所有省份的PDF解析配置都在这里注册。
添加新省份时，只需：
1. 在 pdf_profiles/ 下新建 xxx.py
2. 在这里 import 并加到 PROFILES 字典
"""

from .ningxia import NingxiaProfile, NingxiaInstallProfile
from .zhengzhou import ZhengzhouProfile
from .guangzhou import GuangzhouProfile
from .hainan import HainanProfile
from .shaanxi import ShaanxiProfile
from .jiangxi import JiangxiProfile

# ======== 配置注册表 ========
# key = CLI的 --profile 参数值
# value = Profile实例
PROFILES = {
    "ningxia": NingxiaProfile(),
    "ningxia_install": NingxiaInstallProfile(),
    "zhengzhou": ZhengzhouProfile(),
    "guangzhou": GuangzhouProfile(),
    "hainan": HainanProfile(),
    "shaanxi": ShaanxiProfile(),
    "jiangxi": JiangxiProfile(),
}


def get_profile(name: str):
    """获取指定名称的配置"""
    return PROFILES.get(name)


def list_profiles() -> list:
    """列出所有可用的配置"""
    return [
        {"name": p.name, "description": p.description}
        for p in PROFILES.values()
    ]
